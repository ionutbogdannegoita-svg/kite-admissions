"""v1.1 — Slice 1: migrazione allo schema 2, catalogo, validazione dei JSON, ripristino di backup V1 (IW01, IW13)."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

import pytest

from kite_admissions import catalog
from kite_admissions import db as dbmod
from kite_admissions.paths import Paths
from kite_admissions.schema import FOLLOWUP_ACTIONS, SCHEMA_VERSION, TABLES
from kite_admissions.services import backups
from kite_admissions.services import interview as interview_service
from kite_admissions.services import leads as lead_service
from kite_admissions.services import offers as offer_service
from kite_admissions.services.common import ValidationError
from kite_admissions.settings import SettingsStore

from . import v1_data
from .helpers import create_family

NEW_COLUMNS = {
    "Family": {"contact_source_detail": None},
    "StudentLead": {"birth_date": None, "current_school": None, "current_grade": None, "languages": "[]",
                    "bilingual_context": None, "profile_note": None, "educational_review": 0,
                    "educational_review_note": None},
    "Appointment": {"interview": "{}"},
    "Offer": {"enrollment_fee_cents": None, "reductions": "[]", "authorized_by": None},
    "FollowUp": {"appointment_id": None, "assignee": None},
    "Interaction": {},
    "CalendarExclusion": {},
}


def v1_home(home):
    """Cartella dati con il database lasciato dalla V1 (nessun avvio dell'app v1.1)."""
    paths = Paths.resolve(home)
    paths.ensure()
    v1_data.build(paths.db_path)
    return paths


def restore_via_ui(client, backup_path, kind="manuale"):
    page = client.get(f"/dati/ripristino/{kind}/{backup_path.name}").get_data(as_text=True)
    assert "Verificato" in page, page[:2000]
    return client.post(f"/dati/ripristino/{kind}/{backup_path.name}", data={"confirm_text": "RIPRISTINA"})


# --- IW01: migrazione -------------------------------------------------------------------------

def test_v1_database_migrates_at_start_keeping_every_value(home, make_app):
    paths = v1_home(home)
    before_columns = v1_data.v1_columns(paths.db_path)
    before = v1_data.snapshot(paths.db_path)
    assert all(before[table] for table in TABLES)  # ogni tabella V1 è popolata

    app = make_app()
    state = app.extensions["kite"]
    assert state.recovery is None
    preop = list(paths.preop_backups_dir.glob("*.zip"))
    assert len(preop) == 1 and "pre-migrazione" in preop[0].name
    assert backups.read_manifest(preop[0])["schema_version"] == 1

    conn = dbmod.open_connection(paths.db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
        assert dbmod.check_connection(conn) == []
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
        assert tables == set(TABLES) and len(TABLES) == 7
        triggers = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type = 'trigger'")}
        assert {"trg_offer_frozen", "trg_follow_up_appointment_family", "trg_follow_up_appointment_fixed"} <= triggers
        for table, defaults in NEW_COLUMNS.items():
            columns = v1_data.columns(conn, table)
            assert columns[:len(before_columns[table])] == before_columns[table]  # le colonne V1 restano
            assert set(columns[len(before_columns[table]):]) == set(defaults)
            for row in conn.execute(f"SELECT * FROM {table}"):
                for column, value in defaults.items():
                    assert row[column] == value, (table, column)
    finally:
        conn.close()
    assert v1_data.snapshot(paths.db_path, only=before_columns) == before  # stesse righe, stessi valori V1

    client = app.test_client()
    for url in (f"/famiglie/{v1_data.F1}", f"/appuntamenti/{v1_data.A1}", "/", "/famiglie/", "/appuntamenti/"):
        assert client.get(url).status_code == 200, url


def test_fresh_database_equals_migrated_database(home, make_app, tmp_path):
    fresh = tmp_path / "nuovo.sqlite3"
    dbmod.initialize(fresh)
    migrated = v1_home(home).db_path
    make_app()

    def schema(path):
        conn = sqlite3.connect(path)
        try:
            return sorted((row[0], row[1], " ".join((row[2] or "").split())) for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"))
        finally:
            conn.close()

    assert schema(fresh) == schema(migrated)


def test_communicated_offer_freezes_new_fields_in_the_database(home, make_app):
    paths = v1_home(home)
    make_app()
    conn = dbmod.open_connection(paths.db_path)
    try:
        for assignment in ("enrollment_fee_cents = 30000", "reductions = '[{\"reason\": \"FRATELLI\"}]'",
                           "authorized_by = 'Direzione'", "proposed_fee_cents = 1"):
            with pytest.raises(sqlite3.IntegrityError, match="offer_frozen"):
                conn.execute(f"UPDATE Offer SET {assignment} WHERE id = ?", (v1_data.O1,))
        # La bozza resta modificabile, anche nei campi nuovi.
        conn.execute("UPDATE Offer SET enrollment_fee_cents = 30000, reductions = '[]', authorized_by = 'Direzione' "
                     "WHERE id = ?", (v1_data.O2,))
    finally:
        conn.close()


def test_follow_up_origin_belongs_to_the_same_family_and_is_fixed(home, make_app):
    paths = v1_home(home)
    make_app()
    conn = dbmod.open_connection(paths.db_path)
    base = ("INSERT INTO FollowUp (id, family_id, action, due_on, appointment_id, created_at, updated_at, created_by, "
            "updated_by) VALUES (?, ?, 'RICHIAMARE', '2026-10-05', ?, ?, ?, 'Ionut', 'Ionut')")
    ts = v1_data.TS
    try:
        conn.execute(base, ("99999999-9999-4999-8999-999999999991", v1_data.F1, v1_data.A1, ts, ts))
        with pytest.raises(sqlite3.IntegrityError, match="follow_up_appointment_family"):
            conn.execute(base, ("99999999-9999-4999-8999-999999999992", v1_data.F2, v1_data.A1, ts, ts))
        with pytest.raises(sqlite3.IntegrityError, match="follow_up_appointment_fixed"):
            conn.execute("UPDATE FollowUp SET appointment_id = ? WHERE id = ?", (v1_data.A1, v1_data.U1))
        conn.execute("UPDATE FollowUp SET note = 'ok', assignee = 'Segreteria' WHERE id = ?",
                     ("99999999-9999-4999-8999-999999999991",))
        conn.execute("UPDATE FollowUp SET appointment_id = NULL WHERE appointment_id = ?", (v1_data.A1,))
        assert conn.execute("SELECT count(*) FROM FollowUp WHERE appointment_id IS NOT NULL").fetchone()[0] == 0
        assert dbmod.check_connection(conn) == []
    finally:
        conn.close()


@pytest.mark.parametrize("assignment", [
    "birth_date = '2021-02-30'",
    "birth_date = '2020-05-10'",  # l'anno di nascita V1 della richiesta è 2021
    "languages = '{}'",
    "educational_review_note = 'nota senza flag'",
    "educational_review = 2",
    "bilingual_context = 3",
])
def test_new_student_lead_constraints(home, make_app, assignment):
    paths = v1_home(home)
    make_app()
    conn = dbmod.open_connection(paths.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE StudentLead SET {assignment} WHERE id = ?", (v1_data.L1,))
        conn.execute("UPDATE StudentLead SET birth_date = '2021-03-14', educational_review = 1, "
                     "educational_review_note = 'Colloquio con il coordinamento didattico' WHERE id = ?", (v1_data.L1,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE Appointment SET interview = '[]' WHERE id = ?", (v1_data.A1,))
    finally:
        conn.close()


# --- IW01: ripristino di un backup della V1 ---------------------------------------------------

def _v1_backup(paths):
    """Backup creato quando il database è ancora quello della V1."""
    info = backups.create_backup(paths, SettingsStore(paths.settings_path).load(), backups.KIND_MANUAL,
                                 now=datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc))
    assert backups.read_manifest(info.path)["schema_version"] == 1
    return info.path


def test_v1_backup_restores_into_v11_and_is_migrated(home, make_app):
    paths = v1_home(home)
    before_columns = v1_data.v1_columns(paths.db_path)
    expected = v1_data.snapshot(paths.db_path)
    backup_path = _v1_backup(paths)
    app = make_app()
    client = app.test_client()
    create_family(client, display_name="Famiglia Esempio Creata Dopo")

    response = restore_via_ui(client, backup_path)
    assert response.status_code == 302 and "/dati/verifica-ripristino" in response.headers["Location"]
    state = app.extensions["kite"]
    assert state.recovery is None
    inspection = dbmod.inspect(paths.db_path)
    assert inspection.status == "ok" and inspection.version == 2
    assert v1_data.snapshot(paths.db_path, only=before_columns) == expected
    assert state.settings.load()["post_restore"]["missing"] == ["Famiglia Esempio Creata Dopo"]


def test_failed_migration_after_restore_enters_recovery_mode(home, make_app, monkeypatch):
    paths = v1_home(home)
    backup_path = _v1_backup(paths)
    app = make_app()
    client = app.test_client()

    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("disco pieno (simulato)")

    monkeypatch.setattr(dbmod, "migrate", broken)
    response = client.post(f"/dati/ripristino/manuale/{backup_path.name}", data={"confirm_text": "RIPRISTINA"})
    assert response.status_code == 302 and "/dati/ripristino" in response.headers["Location"]
    state = app.extensions["kite"]
    assert state.recovery is not None and state.recovery.reason == "restore_failed"
    assert "aggiornamento dello schema" in state.recovery.detail
    assert not state.settings.load().get("post_restore")  # nessun esito positivo dichiarato
    assert client.get("/famiglie/").status_code == 302  # l'uso normale resta sospeso


# --- IW13: catalogo ---------------------------------------------------------------------------

LISTS = {name: value for name, value in vars(catalog).items()
         if isinstance(value, tuple) and value and isinstance(value[0], catalog.Entry)}
FORBIDDEN_STEMS = ("diagnos", "certific", "terapi", "farmac", "sanitar", "salute", "allerg", "intoller", "dieta",
                   "religi", "politic", "etnia", "cittadinanza", "reddito", "professione", "separa", "affido", "tutore")
FORBIDDEN_WORDS = ("104", "dsa", "bes", "pei", "pdp", "isee")


def _all_catalog_text():
    texts = []
    for entries in LISTS.values():
        texts += [entry.code for entry in entries] + [entry.label for entry in entries]
    for value in (catalog.CONTACT_SOURCES, catalog.ASSIGNEE_SUGGESTIONS, catalog.AUTHORIZER_SUGGESTIONS,
                  catalog.GRADE_SUGGESTIONS, catalog.CURRENT_GRADE_SUGGESTIONS,
                  (catalog.WAIT_NOTE, catalog.VERIFY_NOTE)):
        texts += list(value)
    texts += [item.label for item in catalog.QUICK_OUTCOMES] + [item.note for item in catalog.QUICK_OUTCOMES]
    return [text.casefold() for text in texts]


def test_catalog_contains_no_sensitive_categories():
    for text in _all_catalog_text():
        assert not any(stem in text for stem in FORBIDDEN_STEMS), text
        assert not any(re.search(rf"\b{word}\b", text) for word in FORBIDDEN_WORDS), text


def test_catalog_lists_are_well_formed():
    assert {"MEETING_KINDS", "ATTENDEE_RELATIONS", "MOTIVATIONS", "LANGUAGES", "LANGUAGE_LEVELS",
            "SERVICES_OF_INTEREST", "PRESENTATION_TOPICS", "MATERIALS", "INTEREST_LEVELS", "DECISION_TIMINGS",
            "DRIVERS", "OBSTACLES", "REDUCTION_REASONS"} <= set(LISTS)
    for name, entries in LISTS.items():
        assert len({entry.code for entry in entries}) == len(entries), name
        assert all(entry.label.strip() for entry in entries), name
        assert all(re.fullmatch(r"[A-Z0-9_]+", entry.code) for entry in entries), name
    assert catalog.codes(catalog.INTEREST_LEVELS) == ("BASSO", "MEDIO", "ALTO", "MOLTO_ALTO")
    assert "probabilit" not in " ".join(entry.label.casefold() for entry in catalog.INTEREST_LEVELS)
    for outcome in catalog.QUICK_OUTCOMES:
        assert outcome.action in FOLLOWUP_ACTIONS + (catalog.WAIT_ACTION, None)
    authorization = {entry.code: entry.needs_authorization for entry in catalog.REDUCTION_REASONS}
    assert authorization == {"FRATELLI": False, "PAGAMENTO_ANNUALE": False, "PROMOZIONE": True,
                             "ACCORDO_DIREZIONE": True, "ALTRO": True}
    assert "TUTORE" not in catalog.codes(catalog.ATTENDEE_RELATIONS)
    assert len(catalog.CONTACT_SOURCES) == len(set(catalog.CONTACT_SOURCES))


def test_school_levels_are_infanzia_primaria_and_secondaria_di_primo_grado():
    cycles = {catalog.grade_cycle(grade) for grade in catalog.GRADE_SUGGESTIONS}
    assert cycles == {"INFANZIA", "PRIMARIA_1", "PRIMARIA", "SECONDARIA_1"}
    assert "Nido" not in catalog.GRADE_SUGGESTIONS and not any("II grado" in g for g in catalog.GRADE_SUGGESTIONS)
    assert catalog.CURRENT_GRADE_SUGGESTIONS[0] == "Nido"
    assert catalog.grade_cycle("Primaria 1ª") == "PRIMARIA_1"
    assert catalog.grade_cycle("  primaria   1a (inserimento) ") == "PRIMARIA_1"
    assert catalog.grade_cycle("Primaria 2ª") == "PRIMARIA"
    assert catalog.grade_cycle("Secondaria di primo grado 1ª") == "SECONDARIA_1"
    assert catalog.grade_cycle("Infanzia") == "INFANZIA"
    assert catalog.grade_cycle(None) is None and catalog.grade_cycle("Corso estivo") is None
    topics = catalog.for_cycle(catalog.PRESENTATION_TOPICS, {"PRIMARIA"})
    assert "INSERIMENTO" not in catalog.codes(topics)
    assert "INSERIMENTO" in catalog.codes(catalog.for_cycle(catalog.PRESENTATION_TOPICS, {"INFANZIA"}))


def test_catalog_labels_keep_removed_codes_readable():
    assert catalog.label(catalog.OBSTACLES, "COSTO") == "Costo"
    assert catalog.label(catalog.OBSTACLES, "VECCHIO") == "VECCHIO (voce non più in elenco)"
    assert catalog.label(catalog.OBSTACLES, None) == ""
    assert catalog.wait_days("ALCUNE_SETTIMANE") == 14 and catalog.wait_days(None) == 7


# --- JSON validati nel servizio ---------------------------------------------------------------

FULL = {
    "kind": "PRIMA_VISITA",
    "attendees": ["PADRE", "MADRE", "MADRE"],
    "motivations": ["LINGUE", "APPROCCIO_DIDATTICO"],
    "motivations_note": "  inglese ogni giorno ",
    "needs": {"services": ["MENSA", "POST_SCUOLA"], "schedule": "uscita 16:30", "activities": "",
              "siblings_enrolled": "0", "desired_start": "2027-09-13"},
    "presented": ["COSTI", "PROGETTO_EDUCATIVO"],
    "questions": [{"q": "Trasporto dalla zona Esempio?", "a": "", "verify": True}, {"q": "", "a": ""}],
    "assessment": {"interest": "ALTO", "timing": "ALCUNE_SETTIMANE", "driver": "LINGUE", "obstacle": "TRASPORTO"},
}


def test_clean_interview_normalizes_and_omits_empty_values():
    cleaned = interview_service.clean_interview(FULL)
    assert cleaned == {
        "format": 1,
        "kind": "PRIMA_VISITA",
        "attendees": ["MADRE", "PADRE"],  # ordine dell'elenco, senza doppioni
        "motivations": ["APPROCCIO_DIDATTICO", "LINGUE"],
        "motivations_note": "inglese ogni giorno",
        "needs": {"services": ["POST_SCUOLA", "MENSA"], "schedule": "uscita 16:30", "siblings_enrolled": False,
                  "desired_start": "2027-09-13"},
        "presented": ["PROGETTO_EDUCATIVO", "COSTI"],
        "questions": [{"q": "Trasporto dalla zona Esempio?", "a": "", "verify": True}],
        "assessment": {"interest": "ALTO", "timing": "ALCUNE_SETTIMANE", "driver": "LINGUE", "obstacle": "TRASPORTO"},
    }
    assert interview_service.clean_interview({}) == {"format": 1}
    assert not interview_service.has_content({"format": 1, "saved_at": "x"})
    assert interview_service.has_content(cleaned)


@pytest.mark.parametrize("change, field", [
    ({"kind": "INVENTATO"}, "kind"),
    ({"motivations": ["LINGUE", "RELIGIONE"]}, "motivations"),
    ({"needs": {"desired_start": "2027-02-30"}}, "desired_start"),
    ({"questions": [{"q": "", "a": "Risposta senza domanda"}]}, "questions"),
    ({"questions": [{"q": f"Domanda {n}"} for n in range(13)]}, "questions"),
    ({"motivations_note": "x" * 601}, "motivations_note"),
    ({"assessment": {"interest": "CERTO"}}, "interest"),
])
def test_clean_interview_rejects_invalid_values(change, field):
    with pytest.raises(ValidationError) as error:
        interview_service.clean_interview({**FULL, **change})
    assert error.value.field == field


def test_clean_interview_keeps_codes_removed_from_the_catalog_only_if_already_saved():
    previous = {"motivations": ["VECCHIA_VOCE"], "assessment": {"obstacle": "VECCHIO"}}
    cleaned = interview_service.clean_interview(
        {"motivations": ["LINGUE", "VECCHIA_VOCE"], "assessment": {"obstacle": "VECCHIO"}}, previous)
    assert cleaned["motivations"] == ["LINGUE", "VECCHIA_VOCE"] and cleaned["assessment"]["obstacle"] == "VECCHIO"
    with pytest.raises(ValidationError):
        interview_service.clean_interview({"motivations": ["VECCHIA_VOCE"]})


def test_fingerprint_is_stable():
    assert interview_service.fingerprint({"a": 1, "b": [1, 2]}) == interview_service.fingerprint({"b": [1, 2], "a": 1})
    assert interview_service.fingerprint({"a": 1}) != interview_service.fingerprint({"a": 2})


def test_clean_languages():
    cleaned = lead_service.clean_languages([
        {"code": "EN", "level": "BASE"}, {"code": "IT", "level": "MADRELINGUA"}, {"code": "FR", "level": ""},
        {"code": "ALTRA", "name": "Lingua Esempio", "level": "AVANZATO"}, {"code": "ALTRA", "name": "", "level": ""},
    ])
    assert cleaned == [{"code": "IT", "level": "MADRELINGUA"}, {"code": "EN", "level": "BASE"},
                       {"code": "ALTRA", "name": "Lingua Esempio", "level": "AVANZATO"}]
    for bad in ([{"code": "ALTRA", "name": "Lingua", "level": ""}], [{"code": "ALTRA", "name": "", "level": "BASE"}],
                [{"code": "DE", "level": "BASE"}], [{"code": "IT", "level": "PERFETTO"}],
                [{"code": "ALTRA", "name": f"L{n}", "level": "BASE"} for n in range(3)]):
        with pytest.raises(ValidationError):
            lead_service.clean_languages(bad)
    assert lead_service.clean_languages([{"code": "IT", "level": "VECCHIO"}], [{"code": "IT", "level": "VECCHIO"}])


def test_clean_reductions_and_authorization():
    rows = offer_service.clean_reductions([
        {"reason": "FRATELLI", "amount_cents": 54000, "note": None},
        {"reason": "", "amount_cents": None, "note": None},
        {"reason": "ALTRO", "amount_cents": 10000, "note": "Accordo di esempio"},
    ])
    assert rows == [{"reason": "FRATELLI", "amount_cents": 54000, "note": ""},
                    {"reason": "ALTRO", "amount_cents": 10000, "note": "Accordo di esempio"}]
    assert offer_service.needs_authorization(rows)
    assert not offer_service.needs_authorization(rows[:1])
    assert not offer_service.needs_authorization([{"reason": "PAGAMENTO_ANNUALE"}])
    for bad in ([{"reason": "FRATELLI", "amount_cents": 0}], [{"reason": "", "amount_cents": 100}],
                [{"reason": "ALTRO", "amount_cents": 100, "note": None}], [{"reason": "INVENTATO", "amount_cents": 1}],
                [{"reason": "FRATELLI", "amount_cents": 1}] * 5):
        with pytest.raises(ValidationError):
            offer_service.clean_reductions(bad)
