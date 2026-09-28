"""Slice 4: scheda famiglia, note e cronologia persistente (AC05, casi B1 di AC03)."""

from __future__ import annotations

import uuid

import pytest

from kite_admissions.db import Database
from kite_admissions.gcal.fake import FakeCalendarSource
from kite_admissions.services import timeline as timeline_service

from .calendar_data import CAL, timed
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


def import_event(app, client, fake, event):
    fake.put(CAL, event)
    refresh(app, client)
    client.post("/appuntamenti/importa", data={"event_id": [event["id"]]})
    db = Database(app.extensions["kite"].paths.db_path)
    appointment_id = db.scalar("SELECT id FROM Appointment WHERE google_event_id = ?", (event["id"],))
    db.close()
    return appointment_id


def note(client, family_id, text, **extra):
    data = {"new_id": str(uuid.uuid4()), "type": "TELEFONATA", "text": text}
    data.update(extra)
    return client.post(f"/famiglie/{family_id}/note", data=data)


def test_timeline_is_built_from_records_without_copies(app, client, fake, db, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Cronologia", first_contact_on="2026-09-15",
                              contact_source="Passaparola")
    lead_id = create_lead(client, family_id, display_name="Alunna Esempio Cronologia")
    appointment_id = import_event(app, client, fake, timed("evt-1", "2026-09-30T15:00:00+02:00",
                                                           title="Visita Esempio Cronologia"))
    client.post(f"/appuntamenti/{appointment_id}/collega", data={"family_id": family_id, "revision": 1})
    client.post(f"/appuntamenti/{appointment_id}/resoconto", data={  # v1.1 (OD-5): con il prossimo passo
        "visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:00", "visit_report": "Visita molto positiva (esempio)",
        "next_action": "RICHIAMARE", "next_due_on": "2026-10-05", "next_new_id": str(uuid.uuid4()), "revision": 2})
    note(client, family_id, "Richiamata per chiarimenti sulla mensa (esempio)")
    clock.advance(minutes=5)
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "ISCRITTO", "closed_on": "2026-10-01", "enrollment_ref": "DOMANDA-ESEMPIO-7",
        "revision": revision(db, "StudentLead", lead_id)})
    items = timeline_service.family_timeline(db, family_id)
    kinds = [item.kind for item in items]
    assert kinds.count("Appuntamento") == 1 and kinds.count("Resoconto") == 1
    assert kinds.count("Telefonata") == 1 and kinds.count("Transizione") == 1 and kinds.count("Primo contatto") == 1
    assert items[0].kind == "Transizione" and "Iscritto" in items[0].title  # più recente in alto
    # In Interaction ci sono solo la telefonata e la transizione: appuntamento e resoconto non vengono copiati.
    assert sorted(row["type"] for row in interactions(db)) == ["LEAD_ENROLLED", "TELEFONATA"]
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Visita molto positiva (esempio)" in page and "DOMANDA-ESEMPIO-7" in page
    assert "Richiamata per chiarimenti" in page


def test_calendar_transitions_recorded_before_linking_appear_after_linking(app, client, fake, db, clock):
    appointment_id = import_event(app, client, fake, timed("evt-1", "2026-10-05T09:30:00+02:00",
                                                           title="Visita Esempio Prima del Collegamento"))
    fake.cancel(CAL, "evt-1")
    clock.advance(minutes=5)
    refresh(app, client)
    fake.put(CAL, timed("evt-1", "2026-10-06T09:30:00+02:00", title="Visita Esempio Prima del Collegamento"))
    clock.advance(minutes=5)
    refresh(app, client)
    before = interactions(db, appointment_id=appointment_id)
    assert [row["type"] for row in before] == ["CALENDAR_CANCELLED", "CALENDAR_REACTIVATED"]
    assert all(row["family_id"] is None for row in before)
    family_id = create_family(client, display_name="Famiglia Esempio Collegata Dopo")
    client.post(f"/appuntamenti/{appointment_id}/collega", data={"family_id": family_id, "revision": 1})
    after = interactions(db, appointment_id=appointment_id)
    assert [dict(row) for row in after] == [dict(row) for row in before]  # stesse righe, non copiate né ricreate
    titles = [item.title for item in timeline_service.family_timeline(db, family_id)]
    assert any("Evento annullato su Calendar" in title for title in titles)
    assert any("Evento riattivato su Calendar" in title for title in titles)
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Annullato su Calendar → Confermato" in page


def test_transitions_survive_a_restart(make_app, fake, home, clock):
    app = make_app(calendar_source=fake)
    client = app.test_client()
    family_id = create_family(client, display_name="Famiglia Esempio Riavvio")
    lead_id = create_lead(client, family_id)
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "NON_PROSEGUE", "closed_on": "2026-10-01", "closure_reason": "Motivo di esempio", "revision": 1})
    restarted = make_app(calendar_source=fake)  # nuovo processo simulato sullo stesso database
    db = Database(restarted.extensions["kite"].paths.db_path)
    rows = interactions(db)
    assert len(rows) == 1
    row = rows[0]
    assert (row["type"], row["student_lead_id"], row["family_id"]) == ("LEAD_NOT_CONTINUING", lead_id, family_id)
    assert (row["previous_state"], row["next_state"], row["origin"]) == ("IN_CORSO", "NON_PROSEGUE", "Ionut")
    assert row["occurred_at"] == "2026-10-01T08:00:00.000000Z"
    page = restarted.test_client().get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Non prosecuzione" in page and "Motivo di esempio" in page
    db.close()


def test_page_views_and_unchanged_imports_add_no_events(app, client, fake, db):
    family_id = create_family(client)
    appointment_id = import_event(app, client, fake, timed("evt-1", "2026-10-05T09:30:00+02:00"))
    client.post(f"/appuntamenti/{appointment_id}/collega", data={"family_id": family_id, "revision": 1})
    for path in ("/", "/famiglie/", f"/famiglie/{family_id}", "/appuntamenti/", f"/appuntamenti/{appointment_id}",
                 "/dati/"):
        assert client.get(path).status_code == 200
    refresh(app, client)
    refresh(app, client)
    client.post(f"/famiglie/{family_id}/modifica", data={"display_name": "Famiglia Esempio Rinominata",
                                                        "revision": revision(db, "Family", family_id)})
    assert interactions(db) == []


def test_manual_notes_can_be_edited_and_deleted_but_transitions_cannot(app, client, db):
    family_id = create_family(client)
    lead_id = create_lead(client, family_id)
    assert note(client, family_id, "Nota di esempio", type="NOTA").status_code == 302
    note_row = interactions(db, type="NOTA")[0]
    url = f"/famiglie/{family_id}/note/{note_row['id']}/modifica"
    assert client.get(url).status_code == 200
    client.post(url, data={"type": "EMAIL", "text": "Email di esempio inviata", "revision": 1})
    edited = db.one("SELECT * FROM Interaction WHERE id = ?", (note_row["id"],))
    assert (edited["type"], edited["text"], edited["revision"]) == ("EMAIL", "Email di esempio inviata", 2)
    assert client.post(url, data={"type": "NOTA", "text": "sovrascrittura", "revision": 1}).status_code == 409
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "NON_PROSEGUE", "closed_on": "2026-10-01", "revision": 1})
    transition = interactions(db, type="LEAD_NOT_CONTINUING")[0]
    assert client.get(f"/famiglie/{family_id}/note/{transition['id']}/modifica").status_code == 404
    assert client.post(f"/famiglie/{family_id}/note/{transition['id']}/elimina", data={"confirm": "1"}).status_code == 404
    client.post(f"/famiglie/{family_id}/note/{note_row['id']}/elimina", data={})
    assert db.one("SELECT 1 FROM Interaction WHERE id = ?", (note_row["id"],)) is not None  # serve la conferma
    client.post(f"/famiglie/{family_id}/note/{note_row['id']}/elimina", data={"confirm": "1"})
    assert db.one("SELECT 1 FROM Interaction WHERE id = ?", (note_row["id"],)) is None
    assert len(interactions(db, type="LEAD_NOT_CONTINUING")) == 1


def test_note_rules(app, client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Note")
    other_id = create_family(client, display_name="Famiglia Esempio Altra")
    other_lead = create_lead(client, other_id)
    note(client, family_id, "Riferita al figlio di un'altra famiglia", student_lead_id=other_lead)
    note(client, family_id, "   ")
    note(client, family_id, "Nel futuro", occurred_at="2027-01-01T10:00")
    assert interactions(db) == []
    data = {"new_id": str(uuid.uuid4()), "type": "NOTA", "text": "Doppio invio di esempio"}
    client.post(f"/famiglie/{family_id}/note", data=data)
    client.post(f"/famiglie/{family_id}/note", data=data)
    assert len(interactions(db)) == 1
