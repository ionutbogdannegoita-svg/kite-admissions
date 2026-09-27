"""Slice 7: backup, ripristino B2, export ed eliminazione (AC08, AC09)."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from kite_admissions.db import Database
from kite_admissions.gcal.fake import FakeCalendarSource
from kite_admissions.paths import Paths
from kite_admissions.schema import TABLES
from kite_admissions.services import backups
from kite_admissions.services import restore as restore_service

from . import dataset
from .calendar_data import CAL, timed
from .conftest import LocalClient, PORT
from .helpers import create_family, revision


@pytest.fixture
def fake():
    return FakeCalendarSource()


@pytest.fixture
def app(make_app, fake):
    return make_app(calendar_source=fake)


def files(folder):
    return sorted(path.name for path in folder.glob("*.zip"))


def manual_backup(client, app):
    client.post("/dati/backup")
    paths = app.extensions["kite"].paths
    return max(paths.manual_backups_dir.glob("*.zip"), key=lambda path: path.stat().st_mtime)


def restore_via_ui(client, backup_path, kind="manuale"):
    page = client.get(f"/dati/ripristino/{kind}/{backup_path.name}").get_data(as_text=True)
    assert "Verificato" in page, page[:2000]
    return client.post(f"/dati/ripristino/{kind}/{backup_path.name}", data={"confirm_text": "RIPRISTINA"})


# --- Backup automatici e manuali ------------------------------------------------------------

def test_daily_backup_is_refreshed_after_each_save(app, client):
    paths = app.extensions["kite"].paths
    assert files(paths.auto_backups_dir) == []
    create_family(client, display_name="Famiglia Esempio Backup Uno")
    assert files(paths.auto_backups_dir) == ["kite-admissions-automatico-2026-10-01.zip"]
    create_family(client, display_name="Famiglia Esempio Backup Due")
    daily = paths.auto_backups_dir / "kite-admissions-automatico-2026-10-01.zip"
    assert files(paths.auto_backups_dir) == [daily.name]  # una copia giornaliera, non una per clic
    assert backups.read_manifest(daily)["tables"]["Family"]["count"] == 2
    last = app.extensions["kite"].settings.load()["last_backup"]
    assert last["name"] == daily.name and last["error"] is None
    client.get("/")  # le letture non producono copie
    assert files(paths.auto_backups_dir) == [daily.name]


def test_daily_backup_after_import(app, client, fake):
    app.extensions["kite"].settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Test"}))
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00"))
    dataset.refresh(app, client)
    daily = app.extensions["kite"].paths.auto_backups_dir / "kite-admissions-automatico-2026-10-01.zip"
    assert daily.exists()
    client.post("/appuntamenti/importa", data={"event_id": ["evt-1"]})
    assert backups.read_manifest(daily)["tables"]["Appointment"]["count"] == 1


def test_backup_failure_after_save_keeps_the_data(app, client, db, monkeypatch):
    original = backups.create_backup

    def failing(paths, settings, kind, **kwargs):
        if kind == backups.KIND_AUTO:
            raise backups.BackupError("disco pieno (simulato)")
        return original(paths, settings, kind, **kwargs)

    monkeypatch.setattr(backups, "create_backup", failing)
    response = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "display_name": "Famiglia Esempio Salvata"},
                           follow_redirects=True)
    page = response.get_data(as_text=True)
    assert "Dati salvati, backup non riuscito: disco pieno (simulato)" in page
    assert db.scalar("SELECT count(*) FROM Family") == 1
    assert app.extensions["kite"].settings.load()["last_backup"]["error"] == "disco pieno (simulato)"
    assert "Ultimo backup automatico non riuscito" in client.get("/").get_data(as_text=True)


def test_manual_backup_is_standalone_and_downloadable(app, client, tmp_path):
    create_family(client, display_name="Famiglia Esempio Manuale")
    path = manual_backup(client, app)
    assert path.name.startswith("kite-admissions-manuale-")
    response = client.get(f"/dati/backup/manuale/{path.name}")
    assert response.status_code == 200 and "attachment" in response.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        (tmp_path / "copia.sqlite3").write_bytes(archive.read("admissions.sqlite3"))
    conn = sqlite3.connect(tmp_path / "copia.sqlite3")
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT display_name FROM Family").fetchone()[0] == "Famiglia Esempio Manuale"
    conn.close()
    assert client.get("/dati/backup/manuale/..%2Fsettings.json").status_code == 404
    assert client.get("/dati/backup/sconosciuto/x.zip").status_code == 404


def test_retention_keeps_recent_manual_and_latest(app):
    state = app.extensions["kite"]
    now = state.now()
    old = now - timedelta(days=45)
    for kind, when, operation in ((backups.KIND_AUTO, old, None), (backups.KIND_PREOP, old, "eliminazione"),
                                  (backups.KIND_MANUAL, old, None), (backups.KIND_AUTO, now - timedelta(days=3), None)):
        backups.create_backup(state.paths, state.settings.load(), kind, operation=operation, now=when, replace=True)
    removed = backups.apply_retention(state.paths, now)
    assert len(removed) == 2
    assert len(files(state.paths.manual_backups_dir)) == 1
    assert files(state.paths.auto_backups_dir) == ["kite-admissions-automatico-2026-09-28.zip"]
    # Anche se tutte le copie fossero vecchie, l'ultima valida resta.
    for path in state.paths.auto_backups_dir.glob("*.zip"):
        path.unlink()
    for path in state.paths.manual_backups_dir.glob("*.zip"):
        path.unlink()
    backups.create_backup(state.paths, state.settings.load(), backups.KIND_AUTO, now=old, replace=True)
    assert backups.apply_retention(state.paths, now) == []
    assert len(files(state.paths.auto_backups_dir)) == 1


# --- Eliminazione definitiva (AC09) ---------------------------------------------------------

def test_failed_preoperation_backup_blocks_deletion(app, client, db, monkeypatch):
    family_id = create_family(client, display_name="Famiglia Esempio Protetta")

    def failing(operation):
        raise backups.BackupError("copia non riuscita (simulata)")

    monkeypatch.setattr(app.extensions["kite"], "preop_backup", failing)
    response = client.post(f"/famiglie/{family_id}/elimina", data={"confirm_label": "Famiglia Esempio Protetta"})
    assert response.status_code == 500 and "eliminazione annullata" in response.get_data(as_text=True)
    assert db.scalar("SELECT count(*) FROM Family") == 1


def test_definitive_deletion_removes_the_dossier_and_excludes_its_events(app, client, fake, clock, db):
    ids = dataset.build(app, client, fake, clock)
    paths = app.extensions["kite"].paths
    before_preop = files(paths.preop_backups_dir)
    page = client.get(f"/famiglie/{ids['bianchi']}/elimina").get_data(as_text=True)
    assert "Google Calendar non viene modificato" in page
    wrong = client.post(f"/famiglie/{ids['bianchi']}/elimina", data={"confirm_label": "famiglia sbagliata"})
    assert wrong.status_code == 422 and db.scalar("SELECT count(*) FROM Family") == 2
    fake.calls.clear()
    done = client.post(f"/famiglie/{ids['bianchi']}/elimina", data={"confirm_label": "Famiglia Esempio Bianchi-Test"})
    assert done.status_code == 200 and "Google Calendar non è stato modificato" in done.get_data(as_text=True)
    assert len(files(paths.preop_backups_dir)) == len(before_preop) + 1
    assert db.scalar("SELECT count(*) FROM Family WHERE id = ?", (ids["bianchi"],)) == 0
    for table, column in (("StudentLead", "family_id"), ("Appointment", "family_id"), ("FollowUp", "family_id"),
                          ("Offer", "family_id")):
        assert db.scalar(f"SELECT count(*) FROM {table} WHERE {column} = ?", (ids["bianchi"],)) == 0
    # Anche le transizioni registrate quando l'evento non era collegato.
    assert db.scalar("SELECT count(*) FROM Interaction WHERE appointment_id = ?", (ids["appt_bianchi"],)) == 0
    assert db.scalar("SELECT count(*) FROM Interaction WHERE family_id = ?", (ids["bianchi"],)) == 0
    exclusion = db.one("SELECT * FROM CalendarExclusion WHERE google_event_id = 'evt-bianchi'")
    assert exclusion["reason"] == "FAMILY_DELETED" and set(exclusion.keys()) == {
        "calendar_id", "google_event_id", "reason", "excluded_at"}
    assert fake.calls == []  # nessuna chiamata a Google durante l'eliminazione
    dataset.refresh(app, client)  # lo stesso evento non ricrea dati e non viene riproposto
    assert db.scalar("SELECT count(*) FROM Appointment WHERE google_event_id = 'evt-bianchi'") == 0
    assert "evt-bianchi" not in app.extensions["kite"].import_runner.preview.candidates
    assert all(call[0] in ("list_events", "get_event") for call in fake.calls)
    assert db.scalar("SELECT count(*) FROM Family WHERE id = ?", (ids["rossi"],)) == 1
    assert integrity(db) == ("ok", 0)


def integrity(db):
    return db.scalar("PRAGMA integrity_check"), len(db.all("PRAGMA foreign_key_check"))


# --- Ripristino (AC08, B2) ------------------------------------------------------------------

def current_revision(app, table, row_id):
    database = Database(app.extensions["kite"].paths.db_path)
    try:
        return revision(database, table, row_id)
    finally:
        database.close()  # nessuna connessione aperta durante il ripristino


def test_backup_modify_restore_returns_exact_values(app, client, fake, clock):
    ids = dataset.build(app, client, fake, clock)
    paths = app.extensions["kite"].paths
    backup_path = manual_backup(client, app)
    expected = dataset.snapshot(paths.db_path)
    assert all(expected[table] for table in TABLES)  # dataset rappresentativo: ogni tabella popolata

    # Modifiche successive alla copia.
    client.post(f"/famiglie/{ids['rossi']}/modifica", data={"display_name": "Famiglia Rinominata Dopo",
                                                           "revision": current_revision(app, "Family", ids["rossi"])})
    client.post(f"/famiglie/{ids['rossi']}/offerte/{ids['offer_v2']}/comunica",
                data={"revision": current_revision(app, "Offer", ids["offer_v2"])})
    client.post(f"/famiglie/{ids['rossi']}/follow-up/{ids['followup_open']}/completa", data={"revision": 1})
    client.post(f"/famiglie/{ids['bianchi']}/elimina", data={"confirm_label": "Famiglia Esempio Bianchi-Test"})
    new_family = create_family(client, display_name="Famiglia Esempio Creata Dopo")
    assert dataset.snapshot(paths.db_path) != expected
    preop_before = files(paths.preop_backups_dir)

    response = restore_via_ui(client, backup_path)
    assert response.status_code == 302 and "/dati/verifica-ripristino" in response.headers["Location"]
    assert dataset.snapshot(paths.db_path) == expected  # valori e relazioni identici alla copia
    assert dataset.integrity(paths.db_path) == ("ok", 0)
    assert len(files(paths.preop_backups_dir)) == len(preop_before) + 1  # copia del database sano prima di sostituirlo
    review = client.get("/dati/verifica-ripristino").get_data(as_text=True)
    assert "Famiglia Esempio Bianchi-Test" in review  # riapparsa: eliminata dopo la copia
    assert "Famiglia Esempio Creata Dopo" in review  # mancante: creata dopo la copia
    state = app.extensions["kite"]
    post = state.settings.load()["post_restore"]
    assert post["reappeared"] == ["Famiglia Esempio Bianchi-Test"] and post["missing"] == ["Famiglia Esempio Creata Dopo"]
    assert "Ripristino eseguito dal backup" in client.get("/").get_data(as_text=True)

    # Import e modifiche sospesi finché la verifica non è confermata.
    blocked = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "display_name": "Non ancora"})
    assert blocked.status_code == 302 and "verifica-ripristino" in blocked.headers["Location"]
    blocked = client.post("/appuntamenti/aggiorna")
    assert blocked.status_code == 302 and "verifica-ripristino" in blocked.headers["Location"]
    assert all(row["display_name"] != "Non ancora" for row in dataset.snapshot(paths.db_path)["Family"])
    client.post("/dati/verifica-ripristino", data={"checked": "1"})
    assert state.settings.load()["post_restore"] is None
    create_family(client, display_name="Famiglia Esempio Dopo Verifica")
    assert new_family  # usato sopra


def test_restore_with_missing_current_database(app, client, fake, clock, make_app, home):
    dataset.build(app, client, fake, clock)
    paths = app.extensions["kite"].paths
    backup_path = manual_backup(client, app)
    expected = dataset.snapshot(paths.db_path)
    paths.db_path.unlink()
    restarted = make_app(calendar_source=fake)
    state = restarted.extensions["kite"]
    assert state.recovery is not None and state.recovery.reason == "missing"
    client2 = restarted.test_client()
    assert "/dati/ripristino" in client2.get("/famiglie/").headers["Location"]
    preop_before = files(paths.preop_backups_dir)
    response = restore_via_ui(client2, backup_path)
    assert response.status_code == 302
    assert state.recovery is None
    assert dataset.snapshot(paths.db_path) == expected
    assert files(paths.preop_backups_dir) == preop_before  # nessuna copia preventiva di un database assente
    assert state.settings.load()["post_restore"]["previous_state"] == "missing"


def test_restore_with_corrupted_database_preserves_the_damaged_file(app, client, fake, clock, make_app):
    dataset.build(app, client, fake, clock)
    paths = app.extensions["kite"].paths
    backup_path = manual_backup(client, app)
    expected = dataset.snapshot(paths.db_path)
    garbage = b"SQLite format 3\x00" + b"\x13" * 5000
    paths.db_path.write_bytes(garbage)
    restarted = make_app(calendar_source=fake)
    state = restarted.extensions["kite"]
    assert state.recovery.reason == "corrupt"
    client2 = restarted.test_client()
    page = client2.get(f"/dati/ripristino/manuale/{backup_path.name}").get_data(as_text=True)
    assert "Danneggiato o non leggibile" in page
    restore_via_ui(client2, backup_path)
    assert dataset.snapshot(paths.db_path) == expected
    preserved = list(paths.preserved_dir.rglob("admissions.sqlite3"))
    assert len(preserved) == 1 and preserved[0].read_bytes() == garbage
    assert state.recovery is None


def test_restore_proceeds_even_if_the_damaged_file_cannot_be_preserved(app, client, fake, clock, make_app, monkeypatch):
    dataset.build(app, client, fake, clock)
    paths = app.extensions["kite"].paths
    backup_path = manual_backup(client, app)
    expected = dataset.snapshot(paths.db_path)
    paths.db_path.write_bytes(b"danneggiato" * 100)
    restarted = make_app(calendar_source=fake)

    def cannot_copy(*args, **kwargs):
        raise PermissionError("accesso negato (simulato)")

    monkeypatch.setattr(restore_service, "preserve_current", cannot_copy)
    client2 = restarted.test_client()
    restore_via_ui(client2, backup_path)
    assert dataset.snapshot(paths.db_path) == expected
    review = client2.get("/dati/verifica-ripristino").get_data(as_text=True)
    assert "non è stato conservato" in review


def test_invalid_backup_is_never_used(app, client, fake, clock, tmp_path):
    dataset.build(app, client, fake, clock)
    paths = app.extensions["kite"].paths
    good = manual_backup(client, app)
    current = dataset.snapshot(paths.db_path)
    tampered = paths.manual_backups_dir / "kite-admissions-manuale-20261001-000000.zip"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "admissions.sqlite3":
                work = tmp_path / "db.sqlite3"
                work.write_bytes(data)
                conn = sqlite3.connect(work)
                conn.execute("UPDATE Family SET display_name = 'Manomesso'")  # contenuto diverso dal manifest
                conn.commit()
                conn.close()
                data = work.read_bytes()
            target.writestr(name, data)
    page = client.get(f"/dati/ripristino/manuale/{tampered.name}").get_data(as_text=True)
    assert "non supera la verifica" in page and "RIPRISTINA" not in page.split("Database attuale")[1]
    response = client.post(f"/dati/ripristino/manuale/{tampered.name}", data={"confirm_text": "RIPRISTINA"},
                           follow_redirects=True)
    assert "Backup non utilizzabile" in response.get_data(as_text=True)
    assert dataset.snapshot(paths.db_path) == current
    garbage = paths.manual_backups_dir / "kite-admissions-manuale-20261001-000001.zip"
    garbage.write_bytes(b"non e' uno zip")
    assert "illeggibile" in client.get("/dati/").get_data(as_text=True)


def test_failed_preoperation_copy_protects_a_healthy_database(app, client, fake, clock, monkeypatch):
    dataset.build(app, client, fake, clock)
    paths = app.extensions["kite"].paths
    backup_path = manual_backup(client, app)
    create_family(client, display_name="Famiglia Esempio Da Non Perdere")
    current = dataset.snapshot(paths.db_path)

    def failing(operation):
        raise backups.BackupError("disco pieno (simulato)")

    monkeypatch.setattr(app.extensions["kite"], "preop_backup", failing)
    response = client.post(f"/dati/ripristino/manuale/{backup_path.name}", data={"confirm_text": "RIPRISTINA"},
                           follow_redirects=True)
    assert "ripristino annullato" in response.get_data(as_text=True)
    assert dataset.snapshot(paths.db_path) == current
    assert app.extensions["kite"].settings.load()["post_restore"] is None


def test_failed_verification_is_not_declared_a_success(app, client, fake, clock, monkeypatch):
    dataset.build(app, client, fake, clock)
    backup_path = manual_backup(client, app)
    original = backups.table_digests
    calls = {"n": 0}

    def flaky(conn):
        calls["n"] += 1
        digests = original(conn)
        if calls["n"] >= 2:  # la verifica dopo la sostituzione non torna
            digests["Family"] = {"count": -1, "sha256": "diverso"}
        return digests

    monkeypatch.setattr(backups, "table_digests", flaky)
    response = client.post(f"/dati/ripristino/manuale/{backup_path.name}", data={"confirm_text": "RIPRISTINA"},
                           follow_redirects=True)
    page = response.get_data(as_text=True)
    assert "Verifica dopo il ripristino fallita" in page and "Ripristino completato" not in page
    state = app.extensions["kite"]
    assert state.recovery is not None and state.recovery.reason == "restore_failed"
    blocked = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "display_name": "Bloccata"})
    assert blocked.status_code == 302 and "/dati/ripristino" in blocked.headers["Location"]


def test_database_open_in_another_program_stops_the_restore_safely(app, client):
    create_family(client, display_name="Famiglia Esempio Prima")
    backup_path = manual_backup(client, app)
    create_family(client, display_name="Famiglia Esempio Dopo")
    paths = app.extensions["kite"].paths
    expected = dataset.snapshot(paths.db_path)
    other_program = sqlite3.connect(paths.db_path)  # es. un visualizzatore di database aperto
    other_program.execute("SELECT count(*) FROM Family").fetchone()
    try:
        response = client.post(f"/dati/ripristino/manuale/{backup_path.name}", data={"confirm_text": "RIPRISTINA"},
                               follow_redirects=True)
    finally:
        other_program.close()
    page = response.get_data(as_text=True)
    if "Ripristino completato" in page:
        pytest.skip("il sistema consente di sostituire un file aperto")
    assert "aperto da un altro programma" in page
    assert dataset.snapshot(paths.db_path) == expected and dataset.integrity(paths.db_path) == ("ok", 0)
    assert app.extensions["kite"].recovery is None and app.extensions["kite"].settings.load()["post_restore"] is None


def test_restore_is_refused_while_a_calendar_refresh_is_running(app, client, fake):
    """B2: il ripristino non parte mai insieme a un aggiornamento Calendar; i dati restano intatti."""
    import threading

    state = app.extensions["kite"]
    state.settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Test"}))
    create_family(client, display_name="Famiglia Esempio Prima Del Backup")
    backup_path = manual_backup(client, app)
    create_family(client, display_name="Famiglia Esempio Dopo Il Backup")
    paths = state.paths
    current = dataset.snapshot(paths.db_path)
    release = threading.Event()
    original = fake.list_events

    def slow(*args, **kwargs):
        release.wait(10)
        return original(*args, **kwargs)

    fake.list_events = slow
    client.post("/appuntamenti/aggiorna")
    try:
        assert state.import_runner.running
        response = client.post(f"/dati/ripristino/manuale/{backup_path.name}", data={"confirm_text": "RIPRISTINA"},
                               follow_redirects=True)
        assert "Aggiornamento da Google Calendar in corso" in response.get_data(as_text=True)
        assert dataset.snapshot(paths.db_path) == current and state.settings.load()["post_restore"] is None
    finally:
        release.set()
        state.import_runner.wait(10)
    assert restore_via_ui(client, backup_path).status_code == 302
    assert [row["display_name"] for row in dataset.snapshot(paths.db_path)["Family"]] == [
        "Famiglia Esempio Prima Del Backup"]


def test_restore_requires_explicit_confirmation(app, client):
    create_family(client, display_name="Famiglia Esempio Conferma")
    backup_path = manual_backup(client, app)
    response = client.post(f"/dati/ripristino/manuale/{backup_path.name}", data={"confirm_text": "si"})
    assert response.status_code == 302 and "ripristino/manuale" in response.headers["Location"]
    assert app.extensions["kite"].settings.load()["post_restore"] is None


def test_new_empty_database_from_recovery_keeps_the_old_file(app, client, make_app):
    paths = app.extensions["kite"].paths
    paths.db_path.write_bytes(b"danneggiato" * 50)
    restarted = make_app()
    client2 = restarted.test_client()
    client2.post("/dati/database-vuoto", data={"confirm_text": "sbagliato"})
    assert restarted.extensions["kite"].recovery is not None
    client2.post("/dati/database-vuoto", data={"confirm_text": "NUOVO"})
    assert restarted.extensions["kite"].recovery is None
    assert list(paths.preserved_dir.rglob("admissions.sqlite3"))[0].read_bytes() == b"danneggiato" * 50
    assert client2.get("/famiglie/").status_code == 200


# --- Export (AC09) --------------------------------------------------------------------------

def test_export_package_is_complete_readable_and_without_credentials(app, client, fake, clock, db):
    ids = dataset.build(app, client, fake, clock)
    create_family(client, display_name="=HYPERLINK(\"https://evil.example\")", primary_phone="+39 0773 000 999")
    response = client.post("/dati/export", follow_redirects=True)
    assert "Export creato" in response.get_data(as_text=True)
    paths = app.extensions["kite"].paths
    package = next(paths.exports_dir.glob("kite-admissions-export-*.zip"))
    download = client.get(f"/dati/export/{package.name}")
    assert download.status_code == 200 and "attachment" in download.headers["Content-Disposition"]
    assert client.get("/dati/export/..%2Fsettings.json").status_code == 404
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        assert names == {f"{table}.csv" for table in TABLES[:6]} | {"kite-admissions.json", "LEGGIMI.txt"}
        family_csv = archive.read("Family.csv").decode("utf-8")
        offer_csv = archive.read("Offer.csv").decode("utf-8")
        document = json.loads(archive.read("kite-admissions.json"))
        everything = b"".join(archive.read(name) for name in names)
    assert family_csv.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(family_csv.lstrip("﻿")), delimiter=";"))
    labels = {row["display_name"] for row in rows}
    assert "'=HYPERLINK(\"https://evil.example\")" in labels  # formula neutralizzata
    phones = {row["primary_phone"] for row in rows}
    assert "'+39 0773 000 999" in phones
    assert "proposed_fee_eur" in offer_csv and "1.000,00 €" in offer_csv
    assert set(document["tables"]) == set(TABLES)
    assert len(document["tables"]["Offer"]) == 2 and document["tables"]["CalendarExclusion"]
    versions = {row["id"]: row for row in document["tables"]["Offer"]}
    assert versions[ids["offer_v2"]]["previous_offer_id"] == ids["offer_v1"]
    assert document["settings"]["calendar"]["id"] == CAL
    assert document["tables"]["Family"] == [dict(row) for row in db.all("SELECT * FROM Family ORDER BY id")]
    for secret in (b"refresh_token", b"client_secret", b"autorizzazione.bin"):
        assert secret not in everything
