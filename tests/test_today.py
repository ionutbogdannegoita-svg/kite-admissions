"""Slice 6: follow-up e vista Oggi (AC07)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from kite_admissions.db import Database
from kite_admissions.gcal.fake import FakeCalendarSource
from kite_admissions.gcal.source import SourceError
from kite_admissions.services import today as today_service

from .calendar_data import CAL, all_day, timed
from .helpers import create_family, create_lead, interactions, revision


@pytest.fixture
def fake():
    return FakeCalendarSource()


@pytest.fixture
def app(make_app, fake):
    app = make_app(calendar_source=fake)
    app.extensions["kite"].settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Test"}))
    return app


def refresh(app, client):
    client.post("/appuntamenti/aggiorna")
    app.extensions["kite"].import_runner.wait(10)


def import_events(app, client, fake, *events):
    for event in events:
        fake.put(CAL, event)
    refresh(app, client)
    client.post("/appuntamenti/importa", data={"event_id": [event["id"] for event in events]})
    db = Database(app.extensions["kite"].paths.db_path)
    ids = {row["google_event_id"]: row["id"] for row in db.all("SELECT id, google_event_id FROM Appointment")}
    db.close()
    return ids


def followup(client, family_id, **fields):
    data = {"new_id": str(uuid.uuid4()), "action": "RICHIAMARE", "due_on": "2026-10-01"}
    data.update(fields)
    response = client.post(f"/famiglie/{family_id}/follow-up/nuovo", data=data)
    assert response.status_code == 302
    return data["new_id"]


def steps(app, clock, family_id):
    db = Database(app.extensions["kite"].paths.db_path)
    try:
        return today_service.next_steps(db, clock(), [family_id])[family_id]
    finally:
        db.close()


def test_followup_lifecycle(client, db):
    family_id = create_family(client)
    other = create_family(client, display_name="Famiglia Esempio Altra")
    other_lead = create_lead(client, other)
    first = followup(client, family_id, note="Chiedere documenti (esempio)")
    row = db.one("SELECT * FROM FollowUp WHERE id = ?", (first,))
    assert (row["status"], row["action"], row["due_on"], row["created_by"]) == ("APERTO", "RICHIAMARE", "2026-10-01", "Ionut")
    for bad in ({"action": "ALTRO", "note": ""}, {"due_on": ""}, {"action": "SCONOSCIUTA"},
                {"student_lead_id": other_lead}):
        client.post(f"/famiglie/{family_id}/follow-up/nuovo", data={"new_id": str(uuid.uuid4()), "action": "RICHIAMARE",
                                                                   "due_on": "2026-10-02", **bad})
    assert db.scalar("SELECT count(*) FROM FollowUp") == 1
    url = f"/famiglie/{family_id}/follow-up/{first}/completa"
    client.post(url, data={"revision": 1, "outcome": "Parlato con il genitore (esempio)"})
    client.post(url, data={"revision": 1, "outcome": "secondo clic"})  # idempotente
    done = db.one("SELECT * FROM FollowUp WHERE id = ?", (first,))
    assert (done["status"], done["closed_on"], done["outcome"]) == ("COMPLETATO", "2026-10-01",
                                                                   "Parlato con il genitore (esempio)")
    second = followup(client, family_id, action="INVIARE_INFORMAZIONI", due_on="2026-10-05")
    client.post(f"/famiglie/{family_id}/follow-up/{second}/annulla", data={"revision": 1})
    assert db.scalar("SELECT status FROM FollowUp WHERE id = ?", (second,)) == "ANNULLATO"


def test_completion_does_not_invent_a_successful_call(client, db):
    family_id = create_family(client)
    followup_id = followup(client, family_id)
    client.post(f"/famiglie/{family_id}/follow-up/{followup_id}/completa", data={"revision": 1, "outcome": ""})
    assert db.scalar("SELECT outcome FROM FollowUp WHERE id = ?", (followup_id,)) is None
    assert interactions(db) == []  # nessuna telefonata registrata d'ufficio
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "esito non indicato" in page


def test_due_and_overdue_follow_the_rome_calendar(client, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Fuso", primary_phone="+39 0773 000 701")
    followup(client, family_id, due_on="2026-10-01")
    clock.set(datetime(2026, 10, 1, 21, 30, tzinfo=timezone.utc))  # 23:30 a Roma: ancora il 1° ottobre
    page = client.get("/").get_data(as_text=True)
    assert "Famiglia Esempio Fuso" in page and "Scaduto il" not in page and "+39 0773 000 701" in page
    clock.set(datetime(2026, 10, 1, 22, 30, tzinfo=timezone.utc))  # 00:30 a Roma del 2 ottobre
    page = client.get("/").get_data(as_text=True)
    assert "Scaduto il 01/10/2026" in page


def test_completed_followups_leave_the_open_lists(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Chiusa")
    followup_id = followup(client, family_id)
    assert "Famiglia Esempio Chiusa" in client.get("/").get_data(as_text=True)
    client.post(f"/famiglie/{family_id}/follow-up/{followup_id}/completa", data={"revision": 1, "next": "/"})
    page = client.get("/").get_data(as_text=True)
    assert "Nessun richiamo scaduto" in page


def test_family_and_child_coverage_rules(app, client, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Copertura")
    first = create_lead(client, family_id, display_name="Primo Esempio")
    create_lead(client, family_id, display_name="Seconda Esempio")
    followup(client, family_id, student_lead_id=first, due_on="2026-10-10")
    step = steps(app, clock, family_id)
    assert step.missing and step.uncovered_leads == ["Seconda Esempio"]  # l'azione sul figlio copre solo lui
    page = client.get("/").get_data(as_text=True)
    assert "Senza azione: Seconda Esempio" in page
    followup(client, family_id, action="FISSARE_VISITA", due_on="2026-10-12")  # azione della famiglia
    step = steps(app, clock, family_id)
    assert not step.missing and step.label.startswith("Richiamare entro il 10/10/2026")


def test_family_without_children_is_flagged_without_next_step(app, client, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Senza Alunni")
    assert steps(app, clock, family_id).missing
    assert "Nessun alunno identificato" in client.get("/").get_data(as_text=True)
    followup(client, family_id, due_on="2026-10-03")
    assert not steps(app, clock, family_id).missing


def test_cancelling_the_only_visit_flags_the_request(app, client, fake, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Visita Unica")
    create_lead(client, family_id)
    ids = import_events(app, client, fake, timed("evt-1", "2026-10-06T10:00:00+02:00", title="Visita unica"))
    client.post(f"/appuntamenti/{ids['evt-1']}/collega", data={"family_id": family_id, "revision": 1})
    step = steps(app, clock, family_id)
    assert not step.missing and step.label.startswith("Appuntamento")
    fake.cancel(CAL, "evt-1")
    refresh(app, client)
    assert steps(app, clock, family_id).missing
    page = client.get("/").get_data(as_text=True)
    assert "Famiglia Esempio Visita Unica" in page.split("Pratiche senza prossimo passo")[1]


def test_unverified_appointment_is_not_a_certain_next_step(app, client, fake, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Non Verificata")
    ids = import_events(app, client, fake, timed("evt-1", "2026-10-06T10:00:00+02:00", title="Visita incerta"))
    client.post(f"/appuntamenti/{ids['evt-1']}/collega", data={"family_id": family_id, "revision": 1})
    assert not steps(app, clock, family_id).missing
    fake.delete(CAL, "evt-1")
    fake.get_errors["evt-1"] = SourceError.from_status(403)
    refresh(app, client)
    assert steps(app, clock, family_id).missing
    page = client.get("/").get_data(as_text=True)
    assert "Appuntamenti non verificati" in page and "Visita incerta" in page and "403" in page


def test_review_date_and_closed_requests(app, client, db, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Riesame")
    lead_id = create_lead(client, family_id, display_name="Alunno In Pausa")
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "IN_PAUSA", "review_on": "2026-10-03", "revision": 1})
    step = steps(app, clock, family_id)
    assert not step.missing and "Riesame" in step.label
    clock.set(datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc))
    assert "Riesame 03/10/2026" in client.get("/").get_data(as_text=True)
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "NON_PROSEGUE", "closed_on": "2026-10-03", "revision": revision(db, "StudentLead", lead_id)})
    step = steps(app, clock, family_id)
    assert not step.missing and step.closed


def test_archived_families_leave_active_flows_but_keep_future_appointments(app, client, fake):
    family_id = create_family(client, display_name="Famiglia Esempio Archiviata")
    followup(client, family_id)
    ids = import_events(app, client, fake, timed("evt-1", "2026-10-04T10:00:00+02:00", title="Visita futura archiviata"))
    client.post(f"/appuntamenti/{ids['evt-1']}/collega", data={"family_id": family_id, "revision": 1})
    client.post(f"/famiglie/{family_id}/archivia")
    page = client.get("/").get_data(as_text=True)
    assert "Nessun richiamo scaduto" in page
    upcoming = page.split("Prossimi 7 giorni")[1]
    assert "Visita futura archiviata" in upcoming and "Famiglia archiviata" in upcoming
    refresh(app, client)  # un nuovo import non riattiva la famiglia
    db = Database(app.extensions["kite"].paths.db_path)
    assert db.scalar("SELECT archived_at FROM Family WHERE id = ?", (family_id,)) is not None
    db.close()


def test_today_lists_appointments_links_and_missing_reports(app, client, fake):
    import_events(app, client, fake,
                  timed("evt-mattina", "2026-10-01T08:30:00+02:00", title="Visita di stamattina"),
                  timed("evt-pomeriggio", "2026-10-01T15:00:00+02:00", title="Visita del pomeriggio"),
                  all_day("evt-giornata", "2026-10-01", "2026-10-02", title="Giornata porte aperte (esempio)"),
                  timed("evt-domani", "2026-10-02T11:00:00+02:00", title="Visita di domani"))
    page = client.get("/").get_data(as_text=True)
    today_part = page.split("Appuntamenti di oggi")[1].split("Da richiamare")[0]
    assert "Visita di stamattina" in today_part and "Visita del pomeriggio" in today_part
    assert "Giornata porte aperte (esempio)" in today_part and "Orario da verificare" in today_part
    assert "Visita di domani" not in today_part
    to_link = page.split("Eventi da collegare")[1].split("Resoconti da completare")[0]
    assert all(title in to_link for title in ("Visita di stamattina", "Visita di domani"))
    missing = page.split("Resoconti da completare")[1].split("Pratiche senza prossimo passo")[0]
    assert "Visita di stamattina" in missing and "Visita del pomeriggio" not in missing
    assert "Visita di domani" in page.split("Prossimi 7 giorni")[1]


def test_calendar_status_and_errors_are_visible_on_today(app, client, fake):
    page = client.get("/").get_data(as_text=True)
    assert "Nessun aggiornamento da Google Calendar eseguito" in page
    refresh(app, client)
    assert "Completato" in client.get("/").get_data(as_text=True)
    fake.list_error = SourceError("auth")
    refresh(app, client)
    page = client.get("/").get_data(as_text=True)
    assert "Non riuscito" in page and "ricollega Google" in page and "ultimo controllo completo" in page


def test_report_with_next_step_is_saved_in_one_go(app, client, fake, db):
    family_id = create_family(client, display_name="Famiglia Esempio Resoconto")
    lead_id = create_lead(client, family_id)
    ids = import_events(app, client, fake, timed("evt-1", "2026-09-30T15:00:00+02:00"))
    client.post(f"/appuntamenti/{ids['evt-1']}/collega", data={"family_id": family_id, "lead_id": lead_id,
                                                              "revision": 1})
    response = client.post(f"/appuntamenti/{ids['evt-1']}/resoconto", data={
        "visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:00", "visit_report": "Interessati (esempio)",
        "next_action": "RICHIAMARE", "next_due_on": "2026-10-05", "next_note": "Dopo il colloquio di lavoro (esempio)",
        "next_new_id": str(uuid.uuid4()), "revision": 2})
    assert response.status_code == 302
    row = db.one("SELECT * FROM FollowUp WHERE family_id = ?", (family_id,))
    assert (row["action"], row["due_on"], row["student_lead_id"]) == ("RICHIAMARE", "2026-10-05", lead_id)
    assert db.scalar("SELECT visit_outcome FROM Appointment WHERE id = ?", (ids["evt-1"],)) == "SVOLTA"
    bad = client.post(f"/appuntamenti/{ids['evt-1']}/resoconto", data={
        "visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:00", "next_action": "ALTRO", "next_due_on": "2026-10-05",
        "next_note": "", "revision": 3})
    assert bad.status_code == 302 and db.scalar("SELECT count(*) FROM FollowUp") == 1
    assert db.scalar("SELECT revision FROM Appointment WHERE id = ?", (ids["evt-1"],)) == 3  # nulla salvato a metà


def test_families_list_shows_the_next_step(app, client):
    covered = create_family(client, display_name="Famiglia Esempio Con Passo")
    create_family(client, display_name="Famiglia Esempio Senza Passo")
    followup(client, covered, due_on="2026-10-08")
    page = client.get("/famiglie/").get_data(as_text=True)
    assert "Richiamare entro il 08/10/2026" in page and "Senza prossimo passo" in page
