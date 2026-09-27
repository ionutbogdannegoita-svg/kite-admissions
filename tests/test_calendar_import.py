"""Slice 2: Google Calendar → CRM in sola lettura (AC02, AC03, AC10 parti pertinenti)."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import date, datetime, timezone

import pytest

from kite_admissions.gcal import google as google_api
from kite_admissions.gcal.fake import FakeCalendarSource
from kite_admissions.gcal.source import SourceError, normalize_event
from kite_admissions.services import backups, calendar_import
from kite_admissions.timeutil import import_window, rome_today

from .calendar_data import CAL, OTHER_CAL, all_day, occurrence, timed
from .helpers import interactions


@pytest.fixture
def fake():
    return FakeCalendarSource()


def refresh(db, fake, clock, past=30, future=180, calendar_id=CAL):
    time_min, time_max = import_window(rome_today(clock()), past, future)
    return calendar_import.refresh(db, fake, calendar_id=calendar_id, time_min=time_min, time_max=time_max,
                                   now=clock())


def appointments(db):
    return db.all("SELECT * FROM Appointment ORDER BY google_event_id")


def import_ids(db, fake, clock, *event_ids):
    summary, preview = refresh(db, fake, clock)
    imported, already = calendar_import.import_selected(db, preview, list(event_ids), clock())
    return summary, preview, imported, already


# --- AC02: import selettivo in sola lettura -------------------------------------------------

def test_mixed_calendar_imports_only_selected_events(db, fake, clock):
    fake.put(CAL, timed("evt-visita-1", "2026-10-05T09:30:00+02:00", title="Visita Famiglia Esempio Alfa",
                        description="Genitore: +39 0773 000 101", attendees=[
                            {"email": "alfa.genitore@example.org", "displayName": "Genitore Esempio Alfa"},
                            {"email": CAL, "self": True, "organizer": True}]))
    fake.put(CAL, timed("evt-riunione", "2026-10-05T15:00:00+02:00", title="Riunione fornitori (non Admissions)"))
    fake.put(CAL, timed("evt-visita-2", "2026-10-06T11:00:00+02:00", title="Visita Famiglia Esempio Beta"))
    summary, preview = refresh(db, fake, clock)
    assert summary.status == "OK" and summary.listed == 3
    assert set(preview.candidates) == {"evt-visita-1", "evt-riunione", "evt-visita-2"}
    assert appointments(db) == []  # l'anteprima non salva nulla

    imported, already = calendar_import.import_selected(db, preview, ["evt-visita-1"], clock())
    assert (imported, already) == (1, 0)
    rows = appointments(db)
    assert len(rows) == 1
    row = rows[0]
    assert (row["calendar_id"], row["google_event_id"]) == (CAL, "evt-visita-1")
    assert row["src_title"] == "Visita Famiglia Esempio Alfa"
    assert row["src_start_at"] == "2026-10-05T07:30:00.000000Z" and row["src_end_at"] == "2026-10-05T08:30:00.000000Z"
    assert row["src_description"] == "Genitore: +39 0773 000 101"
    assert row["src_status"] == "CONFIRMED" and row["verification_state"] == "VERIFIED"
    assert row["last_synced_at"] == "2026-10-01T08:00:00.000000Z"
    assert row["created_by"] == "Calendar import" and row["family_id"] is None
    contacts = json.loads(row["src_contacts"])
    assert {"type": "phone", "value": "+39 0773 000 101", "normalized": "+390773000101",
            "source": "descrizione"} in contacts
    assert {c["value"] for c in contacts if c["type"] == "email"} == {"alfa.genitore@example.org"}
    # Nessun contenuto degli eventi non selezionati nel database.
    dump = "\n".join(db.conn.iterdump())
    assert "Riunione fornitori" not in dump and "Esempio Beta" not in dump


def test_nothing_is_preselected_and_unselected_events_are_not_logged(make_app, fake, caplog):
    app = make_app(calendar_source=fake)
    app.extensions["kite"].settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Test"}))
    fake.put(CAL, timed("evt-privato", "2026-10-05T09:30:00+02:00", title="Titolo Riservato Esempio"))
    client = app.test_client()
    caplog.set_level("DEBUG")
    assert client.post("/appuntamenti/aggiorna").status_code == 302
    app.extensions["kite"].import_runner.wait(10)
    page = client.get("/appuntamenti/aggiornamento").get_data(as_text=True)
    assert 'value="evt-privato"' in page
    assert re.search(r'<input type="checkbox"[^>]*checked', page) is None
    assert "Titolo Riservato Esempio" not in caplog.text


def test_import_is_idempotent_on_double_click_and_reimport(db, fake, clock):
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00"))
    summary, preview = refresh(db, fake, clock)
    stale_copy = calendar_import.Preview(created_at=preview.created_at, calendar_id=CAL,
                                         candidates=dict(preview.candidates))
    assert calendar_import.import_selected(db, preview, ["evt-1", "evt-1"], clock()) == (1, 0)
    assert calendar_import.import_selected(db, preview, ["evt-1"], clock()) == (0, 0)
    assert calendar_import.import_selected(db, stale_copy, ["evt-1"], clock()) == (0, 1)  # vincolo UNIQUE
    summary, preview = refresh(db, fake, clock)
    assert preview.candidates == {} and summary.unchanged == 1
    summary, preview = refresh(db, fake, clock)  # ritentativo
    assert db.scalar("SELECT count(*) FROM Appointment") == 1


# --- AC03: aggiornamento robusto ------------------------------------------------------------

def test_modified_event_updates_the_same_row_without_touching_local_data(db, fake, clock):
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00", title="Visita Esempio"))
    import_ids(db, fake, clock, "evt-1")
    before = appointments(db)[0]
    db.execute("UPDATE Appointment SET preparation = 'Preparazione locale (esempio)', "
               "local_observations = 'Osservazione locale' WHERE id = ?", (before["id"],))
    fake.put(CAL, timed("evt-1", "2026-10-07T16:00:00+02:00", title="Visita Esempio (spostata)",
                        updated="2026-09-28T09:00:00.000Z"))
    clock.advance(hours=1)
    summary, _ = refresh(db, fake, clock)
    assert summary.updated == 1
    after = appointments(db)[0]
    assert after["id"] == before["id"]
    assert after["src_title"] == "Visita Esempio (spostata)" and after["src_start_at"] == "2026-10-07T14:00:00.000000Z"
    assert after["preparation"] == "Preparazione locale (esempio)" and after["local_observations"] == "Osservazione locale"
    assert after["updated_by"] == "Calendar import"
    assert after["revision"] == before["revision"]  # l'import non invalida i moduli locali aperti
    assert interactions(db) == []


def test_event_moved_outside_the_window_is_updated_by_id(db, fake, clock):
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00"))
    import_ids(db, fake, clock, "evt-1")
    fake.put(CAL, timed("evt-1", "2027-09-15T09:30:00+02:00", title="Visita rinviata all'anno prossimo"))
    summary, _ = refresh(db, fake, clock)
    assert ("get_event", CAL, "evt-1") in fake.calls
    row = appointments(db)[0]
    assert row["src_start_at"] == "2027-09-15T07:30:00.000000Z" and row["verification_state"] == "VERIFIED"
    assert summary.updated == 1 and summary.status == "OK"


def test_confirmed_cancellation_keeps_dossier_and_records_one_interaction(db, fake, clock):
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00", title="Visita da annullare (esempio)"))
    import_ids(db, fake, clock, "evt-1")
    appointment_id = appointments(db)[0]["id"]
    fake.cancel(CAL, "evt-1", minimal=True)  # risposta con il solo ID
    clock.advance(hours=1)
    summary, preview = refresh(db, fake, clock)
    assert summary.cancelled == 1
    row = appointments(db)[0]
    assert row["src_status"] == "CANCELLED_SOURCE"
    assert row["src_title"] == "Visita da annullare (esempio)"  # dati sorgente precedenti conservati
    rows = interactions(db, appointment_id=appointment_id)
    assert len(rows) == 1
    transition = rows[0]
    assert (transition["type"], transition["previous_state"], transition["next_state"]) == (
        "CALENDAR_CANCELLED", "CONFIRMED", "CANCELLED_SOURCE")
    assert transition["family_id"] is None and transition["origin"] == "Calendar import"
    assert transition["occurred_at"] == "2026-10-01T09:00:00.000000Z"  # quando il CRM lo ha rilevato
    assert "evt-1" not in preview.candidates
    refresh(db, fake, clock)  # nuovo import senza cambiamenti: nessuna nuova riga
    refresh(db, fake, clock)
    assert len(interactions(db, appointment_id=appointment_id)) == 1


def test_reactivation_and_second_cancellation_stay_distinguishable(db, fake, clock):
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00"))
    import_ids(db, fake, clock, "evt-1")
    appointment_id = appointments(db)[0]["id"]
    fake.cancel(CAL, "evt-1", minimal=False)
    clock.advance(minutes=10)
    refresh(db, fake, clock)
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00", status="tentative"))
    clock.advance(minutes=10)
    summary, _ = refresh(db, fake, clock)
    assert summary.reactivated == 1
    assert appointments(db)[0]["src_status"] == "TENTATIVE"
    fake.cancel(CAL, "evt-1")
    clock.advance(minutes=10)
    refresh(db, fake, clock)
    refresh(db, fake, clock)
    rows = interactions(db, appointment_id=appointment_id)
    assert [(r["type"], r["previous_state"], r["next_state"]) for r in rows] == [
        ("CALENDAR_CANCELLED", "CONFIRMED", "CANCELLED_SOURCE"),
        ("CALENDAR_REACTIVATED", "CANCELLED_SOURCE", "TENTATIVE"),
        ("CALENDAR_CANCELLED", "TENTATIVE", "CANCELLED_SOURCE"),
    ]
    assert len({r["occurred_at"] for r in rows}) == 3


def test_cancellation_and_reactivation_outside_the_window_are_read_by_id(db, fake, clock):
    """AC03: evento spostato oltre la finestra, annullato e riattivato: letto per ID, una Interaction ciascuno."""
    family_id = "11111111-1111-4111-8111-111111111111"
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00", title="Visita Esempio Rinviata"))
    import_ids(db, fake, clock, "evt-1")
    appointment_id = appointments(db)[0]["id"]
    db.execute("INSERT INTO Family (id, display_name, created_at, updated_at, created_by, updated_by) "
               "VALUES (?, 'Famiglia Esempio Rinvio', ?, ?, 'Ionut', 'Ionut')",
               (family_id, "2026-10-01T08:00:00.000000Z", "2026-10-01T08:00:00.000000Z"))
    db.execute("UPDATE Appointment SET family_id = ?, preparation = 'Preparazione di esempio' WHERE id = ?",
               (family_id, appointment_id))
    fake.put(CAL, timed("evt-1", "2027-06-10T09:30:00+02:00", title="Visita Esempio Rinviata"))  # oltre 180 giorni
    fake.cancel(CAL, "evt-1", minimal=True)
    clock.advance(hours=1)
    summary, _ = refresh(db, fake, clock)
    assert ("get_event", CAL, "evt-1") in fake.calls and (summary.listed, summary.cancelled) == (0, 1)
    row = appointments(db)[0]
    assert row["src_status"] == "CANCELLED_SOURCE" and row["src_start_at"] == "2026-10-05T07:30:00.000000Z"
    assert (row["family_id"], row["preparation"]) == (family_id, "Preparazione di esempio")
    refresh(db, fake, clock)  # ancora annullato: nessuna nuova riga
    fake.put(CAL, timed("evt-1", "2027-06-10T09:30:00+02:00", title="Visita Esempio Rinviata"))
    clock.advance(hours=1)
    summary, _ = refresh(db, fake, clock)
    assert (summary.listed, summary.reactivated) == (0, 1)
    row = appointments(db)[0]
    assert row["src_status"] == "CONFIRMED" and row["src_start_at"] == "2027-06-10T07:30:00.000000Z"
    assert (row["family_id"], row["preparation"]) == (family_id, "Preparazione di esempio")
    refresh(db, fake, clock)
    rows = interactions(db, appointment_id=appointment_id)
    assert [(r["type"], r["previous_state"], r["next_state"], r["family_id"]) for r in rows] == [
        ("CALENDAR_CANCELLED", "CONFIRMED", "CANCELLED_SOURCE", None),
        ("CALENDAR_REACTIVATED", "CANCELLED_SOURCE", "CONFIRMED", None),
    ]


@pytest.mark.parametrize("status", [403, 404, 410])
def test_access_errors_never_delete_or_cancel(db, fake, clock, status):
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00"))
    fake.put(CAL, timed("evt-2", "2026-10-06T09:30:00+02:00"))
    import_ids(db, fake, clock, "evt-1", "evt-2")
    fake.delete(CAL, "evt-1")  # sparisce dalla finestra
    fake.get_errors["evt-1"] = SourceError.from_status(status)
    summary, _ = refresh(db, fake, clock)
    rows = {row["google_event_id"]: row for row in appointments(db)}
    assert set(rows) == {"evt-1", "evt-2"}
    assert rows["evt-1"]["src_status"] == "CONFIRMED" and rows["evt-1"]["verification_state"] == "NOT_VERIFIED"
    assert str(status) in rows["evt-1"]["verification_error"]
    assert rows["evt-2"]["verification_state"] == "VERIFIED"
    assert interactions(db) == []
    assert summary.status == "PARTIAL" and summary.not_verified == 1


def test_revoked_token_or_network_failure_marks_all_unverified_without_calls(db, fake, clock):
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00"))
    fake.put(OTHER_CAL, timed("evt-x", "2026-10-06T09:30:00+02:00"))
    import_ids(db, fake, clock, "evt-1")
    summary, preview = refresh(db, fake, clock, calendar_id=OTHER_CAL)
    calendar_import.import_selected(db, preview, ["evt-x"], clock())
    for error in (SourceError("auth"), SourceError("network")):
        fake.calls.clear()
        fake.list_error = error
        summary, preview = refresh(db, fake, clock)
        assert summary.status == "FAILED" and summary.not_verified == 2
        assert not any(call[0] == "get_event" for call in fake.calls)  # sorgente giù: nessun tentativo inutile
        assert preview.candidates == {}
        assert all(row["verification_state"] == "NOT_VERIFIED" for row in appointments(db))
        assert db.scalar("SELECT count(*) FROM Appointment") == 2
        assert interactions(db) == []
    fake.list_error = None
    summary, _ = refresh(db, fake, clock)
    assert summary.status == "OK"
    assert all(row["verification_state"] == "VERIFIED" for row in appointments(db))


def test_failed_refresh_does_not_claim_a_full_check(make_app, fake, clock):
    app = make_app(calendar_source=fake)
    state = app.extensions["kite"]
    state.settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Test"}))
    client = app.test_client()
    client.post("/appuntamenti/aggiorna")
    state.import_runner.wait(10)
    ok = state.settings.load()["last_import"]
    assert ok["status"] == "OK" and ok["full_check_at"] == ok["finished_at"]
    fake.list_error = SourceError("network")
    clock.advance(hours=2)
    client.post("/appuntamenti/aggiorna")
    state.import_runner.wait(10)
    failed = state.settings.load()["last_import"]
    assert failed["status"] == "FAILED" and failed["full_check_at"] == ok["full_check_at"]
    assert "Rete non disponibile" in failed["problem"]
    page = client.get("/appuntamenti/").get_data(as_text=True)
    assert "Non riuscito" in page and "Rete non disponibile" in page


def test_recurring_event_imports_only_selected_occurrences(db, fake, clock):
    for day in ("2026-10-05", "2026-10-12", "2026-10-19"):
        fake.put(CAL, occurrence("serie-colloqui", f"{day}T17:00:00+02:00"))
    summary, preview = refresh(db, fake, clock)
    assert len(preview.candidates) == 3
    chosen = "serie-colloqui_20261012T170000"
    calendar_import.import_selected(db, preview, [chosen], clock())
    rows = appointments(db)
    assert [row["google_event_id"] for row in rows] == [chosen]
    assert rows[0]["src_recurring_event_id"] == "serie-colloqui"
    assert rows[0]["src_original_start"] == "2026-10-12T17:00:00+02:00"
    summary, preview = refresh(db, fake, clock)
    assert len(preview.candidates) == 2 and db.scalar("SELECT count(*) FROM Appointment") == 1


def test_all_day_event_keeps_dates_without_inventing_a_time(db, fake, clock, make_app):
    fake.put(CAL, all_day("evt-openday", "2026-10-10", "2026-10-11"))
    import_ids(db, fake, clock, "evt-openday")
    row = appointments(db)[0]
    assert row["src_start_at"] is None and row["src_end_at"] is None
    assert (row["src_start_date"], row["src_end_date"]) == ("2026-10-10", "2026-10-11")


def test_all_day_detail_shows_time_to_verify(make_app, fake, clock):
    app = make_app(calendar_source=fake)
    from kite_admissions.db import Database

    db = Database(app.extensions["kite"].paths.db_path)
    fake.put(CAL, all_day("evt-openday", "2026-10-10", "2026-10-11"))
    import_ids(db, fake, clock, "evt-openday")
    appointment_id = appointments(db)[0]["id"]
    db.close()
    page = app.test_client().get(f"/appuntamenti/{appointment_id}").get_data(as_text=True)
    assert "Orario da verificare" in page and "giorno intero" in page


def test_tentative_event_is_to_be_confirmed(db, fake, clock):
    fake.put(CAL, timed("evt-t", "2026-10-05T09:30:00+02:00", status="tentative"))
    summary, preview = refresh(db, fake, clock)
    assert preview.candidates["evt-t"].status == "tentative"
    calendar_import.import_selected(db, preview, ["evt-t"], clock())
    assert appointments(db)[0]["src_status"] == "TENTATIVE"


def test_invalid_events_are_reported_and_previous_copy_kept(db, fake, clock):
    broken = timed("evt-rotto", "2026-10-05T09:30:00+02:00")
    broken["end"] = {"dateTime": "2026-10-05T08:00:00+02:00"}
    fake.put(CAL, broken)
    fake.put(CAL, timed("evt-ok", "2026-10-06T09:30:00+02:00", title="Visita valida"))
    summary, preview = refresh(db, fake, clock)
    assert [event.event_id for event in preview.invalid] == ["evt-rotto"] and "evt-rotto" not in preview.candidates
    calendar_import.import_selected(db, preview, ["evt-ok", "evt-rotto"], clock())
    assert [row["google_event_id"] for row in appointments(db)] == ["evt-ok"]
    later = timed("evt-ok", "2026-10-06T09:30:00+02:00", title="Visita diventata non valida")
    later["start"] = {}
    fake.put(CAL, later)
    summary, _ = refresh(db, fake, clock)
    row = appointments(db)[0]
    assert row["src_title"] == "Visita valida" and row["verification_state"] == "NOT_VERIFIED"
    assert "copia precedente" in row["verification_error"]


def test_copied_event_with_new_id_is_proposed_with_similarity_warning(db, fake, clock):
    fake.put(CAL, timed("evt-orig", "2026-10-05T09:30:00+02:00", title="Visita Famiglia Esempio Copia"))
    import_ids(db, fake, clock, "evt-orig")
    fake.put(CAL, timed("evt-copia", "2026-10-05T10:00:00+02:00", title="Visita Famiglia Esempio Copia"))
    summary, preview = refresh(db, fake, clock)
    assert "evt-copia" in preview.candidates
    assert preview.similar["evt-copia"][0]["title"] == "Visita Famiglia Esempio Copia"
    assert db.scalar("SELECT count(*) FROM Appointment") == 1  # nessuna fusione automatica


# --- CalendarExclusion ----------------------------------------------------------------------

def test_ignore_is_persistent_and_revocable(db, fake, clock):
    fake.put(CAL, timed("evt-ignora", "2026-10-05T09:30:00+02:00", title="Evento da ignorare (esempio)"))
    summary, preview = refresh(db, fake, clock)
    calendar_import.ignore_event(db, preview, "evt-ignora", clock())
    exclusion = db.one("SELECT * FROM CalendarExclusion")
    assert dict(exclusion) == {"calendar_id": CAL, "google_event_id": "evt-ignora", "reason": "IGNORED",
                               "excluded_at": "2026-10-01T08:00:00.000000Z"}
    summary, preview = refresh(db, fake, clock)
    assert "evt-ignora" not in preview.candidates and "evt-ignora" in preview.ignored
    assert calendar_import.unignore_event(db, preview, CAL, "evt-ignora")
    assert "evt-ignora" in preview.candidates
    summary, preview = refresh(db, fake, clock)
    assert "evt-ignora" in preview.candidates


def test_events_of_deleted_families_are_never_proposed(db, fake, clock):
    fake.put(CAL, timed("evt-eliminato", "2026-10-05T09:30:00+02:00"))
    db.execute("INSERT INTO CalendarExclusion VALUES (?, ?, 'FAMILY_DELETED', ?)",
               (CAL, "evt-eliminato", "2026-09-30T08:00:00.000000Z"))
    summary, preview = refresh(db, fake, clock)
    assert "evt-eliminato" not in preview.candidates and "evt-eliminato" not in preview.ignored
    assert calendar_import.import_selected(db, preview, ["evt-eliminato"], clock()) == (0, 0)
    assert not calendar_import.unignore_event(db, preview, CAL, "evt-eliminato")  # non revocabile da qui
    assert db.scalar("SELECT count(*) FROM CalendarExclusion") == 1


# --- Testo sicuro e recapiti ----------------------------------------------------------------

def test_html_from_calendar_is_shown_as_safe_text(make_app, fake, clock):
    app = make_app(calendar_source=fake)
    from kite_admissions.db import Database

    db = Database(app.extensions["kite"].paths.db_path)
    fake.put(CAL, timed(
        "evt-html", "2026-10-05T09:30:00+02:00", title="<script>alert('titolo')</script>Visita",
        description=('<b>Mamma</b>: esempio@example.org<br><img src="https://tracker.example/x.png" onerror="alert(1)">'
                     '<script>alert("desc")</script><a href="javascript:alert(2)">clicca</a>'),
        location='<iframe src="https://evil.example"></iframe>Sede'))
    import_ids(db, fake, clock, "evt-html")
    appointment_id = appointments(db)[0]["id"]
    db.close()
    page = app.test_client().get(f"/appuntamenti/{appointment_id}").get_data(as_text=True)
    assert "<script>alert" not in page and "<img" not in page and "<iframe" not in page
    assert "onerror" not in page and "javascript:alert" not in page
    assert "&lt;script&gt;alert(&#39;titolo&#39;)&lt;/script&gt;Visita" in page  # titolo reso come testo
    assert "Mamma: esempio@example.org" in page  # descrizione convertita in testo semplice


def test_organizer_and_secretary_emails_are_not_family_contacts():
    raw = timed("evt", "2026-10-05T09:30:00+02:00", description="Scrivere a segreteria.persona@example.org",
                attendees=[{"email": "segreteria.persona@example.org"},
                           {"email": "sala-riunioni@resource.calendar.google.com", "resource": True},
                           {"email": "famiglia.esempio@example.org", "displayName": "Famiglia Esempio"}])
    event = normalize_event(raw, CAL)
    emails = {c["value"] for c in event.contacts if c["type"] == "email"}
    assert emails == {"famiglia.esempio@example.org"}


def test_window_is_computed_in_europe_rome():
    # 25 ottobre 2026: fine dell'ora legale. Mezzanotte di Roma = 22:00 UTC in estate, 23:00 in inverno.
    start, end = import_window(date(2026, 10, 25), 30, 30)
    assert start == datetime(2026, 9, 24, 22, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 24, 23, 0, tzinfo=timezone.utc)


# --- Adapter Google: solo GET, tutte le pagine, errori --------------------------------------

class RecordingSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, dict(params or {}), timeout))
        return self.responses.pop(0)

    def __getattr__(self, name):  # qualsiasi altro metodo HTTP farebbe fallire il test
        raise AssertionError(f"metodo HTTP non ammesso: {name}")


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_google_adapter_reads_every_page_with_get_only():
    pages = [
        FakeResponse(200, {"items": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"}),
        FakeResponse(200, {"items": [{"id": "c"}], "nextPageToken": "p3"}),
        FakeResponse(200, {"items": [{"id": "d"}]}),
    ]
    session = RecordingSession(pages)
    source = google_api.GoogleCalendarSource(session, page_size=2)
    seen_pages = []
    start, end = import_window(date(2026, 10, 1), 30, 180)
    items = source.list_events("segreteria test@example.org", start, end, on_page=seen_pages.append)
    assert [item["id"] for item in items] == ["a", "b", "c", "d"] and seen_pages == [1, 2, 3]
    assert all(call[0] == "GET" for call in session.calls)
    first = session.calls[0]
    assert first[1] == "https://www.googleapis.com/calendar/v3/calendars/segreteria%20test%40example.org/events"
    assert first[2]["singleEvents"] == "true" and first[2]["showDeleted"] == "true"
    assert first[2]["timeMin"] == "2026-08-31T22:00:00Z" and first[2]["timeMax"] == "2027-03-30T22:00:00Z"
    assert "pageToken" not in first[2] and session.calls[1][2]["pageToken"] == "p2"
    assert first[3] == google_api.DEFAULT_TIMEOUT


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "forbidden"), (404, "not_found"),
                                         (410, "gone"), (500, "server"), (429, "server")])
def test_google_adapter_maps_errors(status, kind):
    source = google_api.GoogleCalendarSource(RecordingSession([FakeResponse(status, {})]))
    with pytest.raises(SourceError) as raised:
        source.get_event(CAL, "evt/1")
    assert raised.value.kind == kind


def test_google_adapter_network_errors():
    import requests

    class Broken:
        def __init__(self, exc):
            self.exc = exc

        def get(self, *args, **kwargs):
            raise self.exc

    assert _kind(Broken(requests.ConnectionError("rete giù"))) == "network"
    assert _kind(Broken(requests.Timeout("lento"))) == "timeout"
    from google.auth.exceptions import RefreshError

    assert _kind(Broken(RefreshError("invalid_grant"))) == "auth"


def _kind(session):
    with pytest.raises(SourceError) as raised:
        google_api.GoogleCalendarSource(session).get_event(CAL, "evt")
    return raised.value.kind


def test_granted_scopes_are_checked_in_both_formats():
    events, calendars = google_api.SCOPES
    assert google_api.events_scope_granted([events, calendars])
    assert google_api.events_scope_granted(f"{events} {calendars}")
    assert not google_api.events_scope_granted([calendars])
    assert not google_api.events_scope_granted(None)


def test_requested_scopes_are_read_only():
    assert google_api.SCOPES == ("https://www.googleapis.com/auth/calendar.events.readonly",
                                 "https://www.googleapis.com/auth/calendar.calendarlist.readonly")
    source_text = open(google_api.__file__, encoding="utf-8").read()
    for verb in ("session.post", "session.put", "session.patch", "session.delete", ".insert(", ".update("):
        assert verb not in source_text


def test_refresh_through_the_google_adapter_reads_all_pages_and_ids_with_get_only(db, fake, clock):
    """AC02/AC03 con l'adapter Google su HTTP simulato: tutte le pagine, lettura per ID, 404 non è un annullamento."""
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00", title="Visita Esempio Uno"))
    fake.put(CAL, timed("evt-2", "2026-10-06T09:30:00+02:00", title="Visita Esempio Due"))
    import_ids(db, fake, clock, "evt-1", "evt-2")
    session = RecordingSession([
        FakeResponse(200, {"items": [timed("evt-nuovo", "2026-10-08T09:00:00+02:00", title="Evento Esempio Nuovo")],
                           "nextPageToken": "pagina-2"}),
        FakeResponse(200, {"items": [timed("evt-1", "2026-10-07T10:00:00+02:00", title="Visita Esempio Uno (spostata)")]}),
        FakeResponse(404, {"error": {"code": 404}}),  # evt-2: assente dalla finestra e non trovato per ID
    ])
    clock.advance(hours=1)
    summary, preview = refresh(db, google_api.GoogleCalendarSource(session, page_size=1), clock)
    assert [call[0] for call in session.calls] == ["GET", "GET", "GET"]
    assert all(call[1].startswith(google_api.API_BASE + "/calendars/") for call in session.calls)
    assert session.calls[1][2]["pageToken"] == "pagina-2" and session.calls[2][1].endswith("/events/evt-2")
    assert (summary.pages, summary.listed, summary.updated, summary.not_verified) == (2, 2, 1, 1)
    assert summary.status == "PARTIAL" and set(preview.candidates) == {"evt-nuovo"}
    rows = {row["google_event_id"]: row for row in appointments(db)}
    assert rows["evt-1"]["src_title"] == "Visita Esempio Uno (spostata)"
    assert rows["evt-2"]["src_status"] == "CONFIRMED" and rows["evt-2"]["verification_state"] == "NOT_VERIFIED"
    assert rows["evt-2"]["src_title"] == "Visita Esempio Due" and interactions(db) == []


# --- Collegamento Google e credenziali ------------------------------------------------------

class ReversibleStore:
    """Sostituto di DPAPI per i test: inverte e marca i byte (mai testo in chiaro sul disco)."""

    def protect(self, data):
        return b"PROT" + data[::-1]

    def unprotect(self, blob):
        if not blob.startswith(b"PROT"):
            raise ValueError("non protetto")
        return blob[4:][::-1]


CLIENT_JSON = json.dumps({"installed": {
    "client_id": "123456789012-esempio.apps.googleusercontent.com", "client_secret": "segreto-di-prova",
    "project_id": "kite-admissions-test", "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}).encode()
AUTHORIZED_JSON = json.dumps({"token": "accesso-di-prova", "refresh_token": "rinnovo-di-prova",
                              "client_id": "123", "client_secret": "segreto-di-prova"})


def test_google_connection_setup_connect_and_disconnect(make_app):
    revoked = []
    flows = []
    app = make_app(protected_store=ReversibleStore(),
                   oauth_flow=lambda config: flows.append(config) or AUTHORIZED_JSON,
                   revoker=lambda text: revoked.append(text) or True)
    client = app.test_client()
    state = app.extensions["kite"]
    web_client = json.dumps({"web": {"client_id": "x"}}).encode()
    response = client.post("/dati/google/client", data={"client_file": (_file(web_client), "client.json")},
                           content_type="multipart/form-data", follow_redirects=True)
    assert "App desktop" in response.get_data(as_text=True) and state.google.client_config() is None
    client.post("/dati/google/client", data={"client_file": (_file(CLIENT_JSON), "client.json")},
                content_type="multipart/form-data")
    assert state.google.client_config()["installed"]["project_id"] == "kite-admissions-test"
    assert b"segreto-di-prova" not in state.google.client_path.read_bytes()
    response = client.post("/dati/google/collega")
    assert response.status_code == 302 and flows
    assert state.google.is_connected()
    assert b"rinnovo-di-prova" not in state.google.authorized_path.read_bytes()
    page = client.get("/dati/").get_data(as_text=True)
    assert "Collegato" in page and "segreto-di-prova" not in page
    client.post("/dati/google/scollega")
    assert revoked and not state.google.is_connected() and not state.google.authorized_path.exists()


def _file(data):
    import io

    return io.BytesIO(data)


@pytest.mark.skipif(not hasattr(__import__("sys"), "getwindowsversion"), reason="DPAPI solo su Windows")
def test_dpapi_protects_credentials_for_this_windows_user():
    from kite_admissions.gcal.protected import ProtectionError, WindowsProtectedStore

    store = WindowsProtectedStore()
    blob = store.protect(AUTHORIZED_JSON.encode())
    assert b"rinnovo-di-prova" not in blob
    assert store.unprotect(blob).decode() == AUTHORIZED_JSON
    tampered = blob[:-1] + bytes([blob[-1] ^ 0xFF])
    with pytest.raises(ProtectionError):
        store.unprotect(tampered)


def test_credentials_are_excluded_from_backups(make_app):
    app = make_app(protected_store=ReversibleStore(), oauth_flow=lambda config: AUTHORIZED_JSON)
    state = app.extensions["kite"]
    state.google.save_client_config(CLIENT_JSON)
    state.google.connect()
    info = backups.create_backup(state.paths, state.settings.load(), backups.KIND_MANUAL, now=state.now())
    with zipfile.ZipFile(info.path) as archive:
        names = archive.namelist()
        content = b"".join(archive.read(name) for name in names)
    assert names == ["admissions.sqlite3", "settings.json", "manifest.json"]
    for secret in (b"segreto-di-prova", b"rinnovo-di-prova", b"accesso-di-prova", b"PROT"):
        assert secret not in content


def test_calendar_selection_uses_accessible_calendars(make_app, fake):
    fake.calendars = [{"id": CAL, "summary": "Segreteria (test)", "primary": False, "access_role": "reader"},
                      {"id": OTHER_CAL, "summary": "Altro (test)", "primary": True, "access_role": "owner"}]
    app = make_app(calendar_source=fake)
    client = app.test_client()
    page = client.get("/dati/google/calendari").get_data(as_text=True)
    assert "Segreteria (test)" in page and "Altro (test)" in page
    client.post("/dati/google/calendario", data={"calendar_id": "inesistente@example.org"})
    assert app.extensions["kite"].settings.load()["calendar"] is None
    client.post("/dati/google/calendario", data={"calendar_id": CAL})
    assert app.extensions["kite"].settings.load()["calendar"] == {"id": CAL, "summary": "Segreteria (test)"}


def test_refresh_requires_a_selected_calendar(make_app, fake):
    app = make_app(calendar_source=fake)
    response = app.test_client().post("/appuntamenti/aggiorna")
    assert response.status_code == 302 and "/dati/" in response.headers["Location"]
    assert fake.calls == []


def test_only_one_calendar_operation_at_a_time(make_app, fake):
    import threading

    gate = threading.Event()
    original = fake.list_events

    def slow(*args, **kwargs):
        gate.wait(10)
        return original(*args, **kwargs)

    fake.list_events = slow
    app = make_app(calendar_source=fake)
    state = app.extensions["kite"]
    state.settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Test"}))
    client = app.test_client()
    client.post("/appuntamenti/aggiorna")
    assert state.import_runner.running
    page = client.post("/appuntamenti/aggiorna", follow_redirects=True).get_data(as_text=True)
    assert "già in corso" in page
    assert "Aggiornamento in corso" in page and 'http-equiv="refresh"' in page
    gate.set()
    state.import_runner.wait(10)
    assert not state.import_runner.running


def test_full_refresh_flow_through_the_interface(make_app, fake):
    fake.put(CAL, timed("evt-a", "2026-10-05T09:30:00+02:00", title="Visita Esempio A"))
    fake.put(CAL, timed("evt-b", "2026-10-06T09:30:00+02:00", title="Visita Esempio B"))
    app = make_app(calendar_source=fake)
    state = app.extensions["kite"]
    state.settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Test"}))
    client = app.test_client()
    client.post("/appuntamenti/aggiorna", data={"past_days": "10", "future_days": "60"})
    state.import_runner.wait(10)
    page = client.get("/appuntamenti/aggiornamento").get_data(as_text=True)
    assert "Visita Esempio A" in page and "Visita Esempio B" in page and "Importa selezionati" in page
    response = client.post("/appuntamenti/importa", data={"event_id": ["evt-b"]})
    assert response.status_code == 302 and "da_collegare" in response.headers["Location"]
    listing = client.get("/appuntamenti/?vista=da_collegare").get_data(as_text=True)
    assert "Visita Esempio B" in listing and "Visita Esempio A" not in listing
    assert client.post("/appuntamenti/importa", data={}).status_code == 302  # nessuna selezione: niente
