"""Slice 3: appuntamenti, collegamento famiglia, creazione dall'evento, resoconto (AC04, AC05, AC10)."""

from __future__ import annotations

import time
import uuid

import pytest

from kite_admissions.db import Database
from kite_admissions.gcal.fake import FakeCalendarSource
from kite_admissions.services import appointments as appointment_service
from kite_admissions.textutil import find_phones

from .calendar_data import CAL, timed
from .helpers import create_family, create_lead, interactions, location_id


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


def appointment(db, appointment_id):
    return db.one("SELECT * FROM Appointment WHERE id = ?", (appointment_id,))


def test_suggestion_by_phone_links_with_one_click(app, client, fake, db):
    family_id = create_family(client, display_name="Famiglia Esempio Ferri", primary_phone="+39 0773 000 201")
    ids = import_events(app, client, fake, timed("evt-1", "2026-10-05T09:30:00+02:00", title="Visita Ferri",
                                                   description="Chiamare la mamma allo 0773 000201"))
    page = client.get(f"/appuntamenti/{ids['evt-1']}").get_data(as_text=True)
    assert "Famiglia Esempio Ferri" in page and "stesso telefono" in page
    row = appointment(db, ids["evt-1"])
    response = client.post(f"/appuntamenti/{ids['evt-1']}/collega",
                           data={"family_id": family_id, "revision": row["revision"]})
    assert response.status_code == 302
    linked = appointment(db, ids["evt-1"])
    assert linked["family_id"] == family_id and linked["student_lead_id"] is None
    assert linked["updated_by"] == "Ionut" and linked["revision"] == row["revision"] + 1
    # Doppio invio dello stesso collegamento: nessun errore, nessuna modifica.
    client.post(f"/appuntamenti/{ids['evt-1']}/collega", data={"family_id": family_id, "revision": row["revision"]})
    assert appointment(db, ids["evt-1"])["revision"] == row["revision"] + 1


def test_suggestion_by_attendee_email_and_search_by_child(app, client, fake, db):
    family_id = create_family(client, display_name="Famiglia Esempio Gallo", primary_email="gallo@example.org")
    other_id = create_family(client, display_name="Famiglia Esempio Neri")
    create_lead(client, other_id, display_name="Bambina Esempio Neri")
    ids = import_events(app, client, fake,
                        timed("evt-1", "2026-10-05T09:30:00+02:00", title="Visita",
                              attendees=[{"email": "gallo@example.org", "displayName": "Genitore Gallo"}]))
    page = client.get(f"/appuntamenti/{ids['evt-1']}").get_data(as_text=True)
    assert "Famiglia Esempio Gallo" in page and "stessa email" in page
    search = client.get(f"/appuntamenti/{ids['evt-1']}?q=bambina esempio neri").get_data(as_text=True)
    assert "Famiglia Esempio Neri" in search
    assert family_id  # entrambe proponibili, nessun collegamento automatico
    assert appointment(db, ids["evt-1"])["family_id"] is None


def test_child_of_another_family_is_rejected(app, client, fake, db):
    first = create_family(client, display_name="Famiglia Esempio Prima")
    second = create_family(client, display_name="Famiglia Esempio Seconda")
    other_child = create_lead(client, second, display_name="Figlio Esempio Seconda")
    own_child = create_lead(client, first, display_name="Figlio Esempio Prima")
    ids = import_events(app, client, fake, timed("evt-1", "2026-10-05T09:30:00+02:00"))
    response = client.post(f"/appuntamenti/{ids['evt-1']}/collega", data={
        "family_id": first, "lead_id": other_child, "revision": 1}, follow_redirects=True)
    assert "non appartiene a questa famiglia" in response.get_data(as_text=True)
    assert appointment(db, ids["evt-1"])["family_id"] is None
    client.post(f"/appuntamenti/{ids['evt-1']}/collega", data={"family_id": first, "lead_id": own_child,
                                                              "revision": 1})
    row = appointment(db, ids["evt-1"])
    assert (row["family_id"], row["student_lead_id"]) == (first, own_child)
    response = client.post(f"/appuntamenti/{ids['evt-1']}/richiesta", data={"lead_id": other_child,
                                                                           "revision": row["revision"]},
                           follow_redirects=True)
    assert "non appartiene" in response.get_data(as_text=True)
    assert appointment(db, ids["evt-1"])["student_lead_id"] == own_child


def test_create_family_from_event_uses_editable_suggestions(app, client, fake, db):
    ids = import_events(app, client, fake, timed(
        "evt-1", "2026-10-05T09:30:00+02:00", title="Visita: Famiglia Esempio Conti",
        description="Recapito: +39 0773 000 301",
        attendees=[{"email": "conti.genitore@example.org", "displayName": "Genitore Esempio Conti"}]))
    page = client.get(f"/appuntamenti/{ids['evt-1']}/nuova-famiglia").get_data(as_text=True)
    assert 'value="Famiglia Esempio Conti"' in page
    assert 'value="+39 0773 000 301"' in page and 'value="conti.genitore@example.org"' in page
    assert "da descrizione" in page and "da partecipante" in page
    new_id = str(uuid.uuid4())
    response = client.post(f"/appuntamenti/{ids['evt-1']}/nuova-famiglia", data={
        "new_id": new_id, "revision": 1, "display_name": "Famiglia Esempio Conti",
        "primary_adult_name": "Genitore Esempio Conti", "primary_phone": "+39 0773 000 301",
        "lead_display_name": "Bimbo Esempio Conti", "lead_school_year": "2027/2028"})
    assert response.status_code == 302
    family = db.one("SELECT * FROM Family WHERE id = ?", (new_id,))
    lead = db.one("SELECT * FROM StudentLead WHERE family_id = ?", (new_id,))
    row = appointment(db, ids["evt-1"])
    assert family["display_name"] == "Famiglia Esempio Conti" and family["primary_email"] is None
    assert (row["family_id"], row["student_lead_id"]) == (new_id, lead["id"])
    assert row["src_title"] == "Visita: Famiglia Esempio Conti"  # dati Calendar separati e invariati


def test_dates_and_school_years_are_never_proposed_as_phones(app, client, fake, db):
    """Date e anni scolastici dell'evento non diventano recapiti né precompilano il telefono (SPEC §4.3, §5)."""
    for text in ("a.s. 2026/2027", "anni 2019-2021", "nato il 12/03/2019", "del 25.09.2026",
                 "visita il 25/09/2026 10:30", "data 2026-09-25 10:30"):
        assert find_phones(text) == [], text
    for text, normalized in (("0773/000123", "+390773000123"), ("0773.000.321", "+390773000321"),
                             ("nato il 12/03/2019 0773 000 322", "+390773000322")):
        assert [value for _, value in find_phones(text)] == [normalized], text
    ids = import_events(app, client, fake, timed(
        "evt-1", "2026-10-05T09:30:00+02:00", title="Visita Famiglia Esempio Date - a.s. 2027/2028",
        description="Bambino nato il 12/03/2021. Richiamare dopo il 25/09/2026 10:30 allo 0773 000 311"))
    contacts = appointment_service.contacts(appointment(db, ids["evt-1"]))
    assert [(c["type"], c["normalized"]) for c in contacts] == [("phone", "+390773000311")]
    page = client.get(f"/appuntamenti/{ids['evt-1']}/nuova-famiglia").get_data(as_text=True)
    assert 'id="primary_phone" name="primary_phone" value="0773 000 311"' in page


def test_create_family_from_event_allows_missing_data_and_warns_duplicates(app, client, fake, db):
    create_family(client, display_name="Famiglia Esempio Doppia", primary_phone="+39 0773 000 401")
    ids = import_events(app, client, fake,
                        timed("evt-1", "2026-10-05T09:30:00+02:00", title="Visita", description="tel 0773 000 401"),
                        timed("evt-2", "2026-10-06T09:30:00+02:00", title="Visita senza recapiti"))
    data = {"new_id": str(uuid.uuid4()), "revision": 1, "display_name": "Nucleo Esempio Nuovo",
            "primary_phone": "0773 000 401"}
    response = client.post(f"/appuntamenti/{ids['evt-1']}/nuova-famiglia", data=data)
    assert response.status_code == 200 and "Possibili doppioni" in response.get_data(as_text=True)
    assert db.scalar("SELECT count(*) FROM Family") == 1 and appointment(db, ids["evt-1"])["family_id"] is None
    client.post(f"/appuntamenti/{ids['evt-1']}/nuova-famiglia", data={**data, "confirm_distinct": "1"})
    assert appointment(db, ids["evt-1"])["family_id"] == data["new_id"]
    minimal = {"new_id": str(uuid.uuid4()), "revision": 1, "display_name": "Famiglia Esempio Senza Dati"}
    assert client.post(f"/appuntamenti/{ids['evt-2']}/nuova-famiglia", data=minimal).status_code == 302
    assert appointment(db, ids["evt-2"])["family_id"] == minimal["new_id"]


def test_create_family_from_event_is_all_or_nothing(app, client, fake, db, monkeypatch):
    ids = import_events(app, client, fake, timed("evt-1", "2026-10-05T09:30:00+02:00"))

    def broken(*args, **kwargs):
        raise RuntimeError("errore simulato durante il collegamento")

    monkeypatch.setattr(appointment_service, "_update_local", broken)
    with pytest.raises(RuntimeError):
        client.post(f"/appuntamenti/{ids['evt-1']}/nuova-famiglia", data={
            "new_id": str(uuid.uuid4()), "revision": 1, "display_name": "Famiglia Esempio Parziale",
            "lead_display_name": "Bimbo Esempio"})
    assert db.scalar("SELECT count(*) FROM Family") == 0
    assert db.scalar("SELECT count(*) FROM StudentLead") == 0
    assert appointment(db, ids["evt-1"])["family_id"] is None


def test_change_link_moves_report_and_warns(app, client, fake, db):
    wrong = create_family(client, display_name="Famiglia Esempio Sbagliata")
    right = create_family(client, display_name="Famiglia Esempio Giusta")
    lead_id = create_lead(client, wrong)
    ids = import_events(app, client, fake, timed("evt-1", "2026-09-28T09:30:00+02:00"))
    appointment_id = ids["evt-1"]
    client.post(f"/appuntamenti/{appointment_id}/collega", data={"family_id": wrong, "lead_id": lead_id, "revision": 1})
    client.post(f"/appuntamenti/{appointment_id}/resoconto", data={  # v1.1 (OD-5): con il prossimo passo
        "visit_outcome": "SVOLTA", "visited_at": "2026-09-28T09:30", "visit_report": "Resoconto di esempio",
        "next_action": "RICHIAMARE", "next_due_on": "2026-10-02", "next_new_id": str(uuid.uuid4()), "revision": 2})
    page = client.get(f"/appuntamenti/{appointment_id}/cambia?famiglia={right}").get_data(as_text=True)
    assert "Famiglia Esempio Sbagliata" in page and "Famiglia Esempio Giusta" in page
    assert "anche il resoconto seguirà" in page and "non</strong> vengono trasferiti" in page
    client.post(f"/appuntamenti/{appointment_id}/cambia", data={"family_id": right, "revision": 3})
    row = appointment(db, appointment_id)
    assert row["family_id"] == right and row["student_lead_id"] is None
    assert row["visit_report"] == "Resoconto di esempio"


def test_visit_report_rules(app, client, fake, db):
    ids = import_events(app, client, fake, timed("evt-1", "2026-09-30T15:00:00+02:00"))
    appointment_id = ids["evt-1"]
    page = client.get(f"/appuntamenti/{appointment_id}").get_data(as_text=True)
    assert 'value="2026-09-30T15:00"' in page  # data proposta dall'evento passato, modificabile
    url = f"/appuntamenti/{appointment_id}/resoconto"
    client.post(url, data={"visit_outcome": "SVOLTA", "visited_at": "", "revision": 1})
    assert appointment(db, appointment_id)["visit_outcome"] is None
    client.post(url, data={"visit_outcome": "SVOLTA", "visited_at": "2026-12-01T10:00", "revision": 1})
    assert appointment(db, appointment_id)["visit_outcome"] is None  # visita nel futuro: respinta
    client.post(url, data={"visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:10",
                           "visit_report": "Domande su mensa e trasporto (esempio)",
                           "local_observations": "Molto interessati (esempio)", "revision": 1})
    row = appointment(db, appointment_id)
    assert row["visit_outcome"] == "SVOLTA" and row["visited_at"] == "2026-09-30T13:10:00.000000Z"
    assert row["visit_report"].startswith("Domande") and row["local_observations"].startswith("Molto")
    stale = client.post(url, data={"visit_outcome": "NON_PRESENTATA", "revision": 1}, follow_redirects=True)
    assert "altra scheda" in stale.get_data(as_text=True)
    assert appointment(db, appointment_id)["visit_outcome"] == "SVOLTA"


def test_calendar_updates_never_touch_family_or_local_data(app, client, fake, db, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Stabile", primary_phone="+39 0773 000 501")
    ids = import_events(app, client, fake, timed("evt-1", "2026-09-30T15:00:00+02:00", title="Visita Stabile",
                                                   description="tel 0773 000 501"))
    appointment_id = ids["evt-1"]
    client.post(f"/appuntamenti/{appointment_id}/collega", data={"family_id": family_id, "revision": 1})
    client.post(f"/appuntamenti/{appointment_id}/preparazione", data={"preparation": "Portare il POF", "revision": 2})
    client.post(f"/appuntamenti/{appointment_id}/resoconto", data={  # v1.1 (OD-5): con il prossimo passo
        "visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:00", "visit_report": "Resoconto confermato",
        "next_action": "RICHIAMARE", "next_due_on": "2026-10-03", "next_new_id": str(uuid.uuid4()), "revision": 3})
    fake.put(CAL, timed("evt-1", "2026-10-02T11:00:00+02:00", title="Titolo cambiato dalla segreteria",
                        description="nuovo numero 0773 000 599"))
    clock.advance(hours=1)
    refresh(app, client)
    family = db.one("SELECT * FROM Family WHERE id = ?", (family_id,))
    assert family["display_name"] == "Famiglia Esempio Stabile" and family["primary_phone"] == "+39 0773 000 501"
    row = appointment(db, appointment_id)
    assert row["src_title"] == "Titolo cambiato dalla segreteria"
    assert (row["preparation"], row["visit_outcome"], row["visit_report"]) == (
        "Portare il POF", "SVOLTA", "Resoconto confermato")
    assert row["visited_at"] == "2026-09-30T13:00:00.000000Z"  # la data effettiva resta
    page = client.get(f"/appuntamenti/{appointment_id}").get_data(as_text=True)
    assert "non nella scheda" in page and "0773 000 599" in page  # visibile per verifica manuale
    assert "La visita è registrata il" in page  # discrepanza con il nuovo orario Calendar


def test_past_calendar_event_is_never_marked_visited(app, client, fake, db, clock):
    ids = import_events(app, client, fake, timed("evt-1", "2026-09-29T15:00:00+02:00"))
    clock.advance(days=3)
    refresh(app, client)
    assert appointment(db, ids["evt-1"])["visit_outcome"] is None


def test_remove_erroneous_import_only_without_local_data(app, client, fake, db):
    family_id = create_family(client)
    ids = import_events(app, client, fake, timed("evt-1", "2026-10-05T09:30:00+02:00"),
                        timed("evt-2", "2026-10-06T09:30:00+02:00"))
    client.post(f"/appuntamenti/{ids['evt-2']}/collega", data={"family_id": family_id, "revision": 1})
    assert client.post(f"/appuntamenti/{ids['evt-1']}/rimuovi").status_code == 302
    assert appointment(db, ids["evt-1"]) is None
    assert db.one("SELECT reason FROM CalendarExclusion WHERE google_event_id = 'evt-1'")["reason"] == "IGNORED"
    client.post(f"/appuntamenti/{ids['evt-2']}/rimuovi")
    assert appointment(db, ids["evt-2"]) is not None


def test_five_ordinary_cases_need_few_steps(app, client, fake, db):
    """AC04 (parte automatica): cinque casi sintetici con pochi passi ciascuno."""
    existing = [create_family(client, display_name=f"Famiglia Esempio Caso {n}", primary_phone=f"+39 0773 000 60{n}")
                for n in (1, 2, 3)]
    events = [timed(f"evt-{n}", f"2026-10-0{n + 1}T09:30:00+02:00", title=f"Visita Caso {n}",
                    description=f"tel 0773 000 60{n}") for n in (1, 2, 3)]
    events += [timed(f"evt-{n}", f"2026-10-0{n + 1}T09:30:00+02:00", title=f"Visita Famiglia Esempio Nuova {n}")
               for n in (4, 5)]
    ids = import_events(app, client, fake, *events)
    for n in (1, 2, 3):  # collega da suggerimento: apri evento + un clic
        started = time.perf_counter()
        page = client.get(f"/appuntamenti/{ids[f'evt-{n}']}").get_data(as_text=True)
        assert f"Famiglia Esempio Caso {n}" in page
        client.post(f"/appuntamenti/{ids[f'evt-{n}']}/collega", data={"family_id": existing[n - 1], "revision": 1})
        assert time.perf_counter() - started < 2
    for n in (4, 5):  # crea dall'evento: apri modulo precompilato + salva
        started = time.perf_counter()
        form = client.get(f"/appuntamenti/{ids[f'evt-{n}']}/nuova-famiglia").get_data(as_text=True)
        assert f'value="Famiglia Esempio Nuova {n}"' in form
        client.post(f"/appuntamenti/{ids[f'evt-{n}']}/nuova-famiglia", data={
            "new_id": str(uuid.uuid4()), "revision": 1, "display_name": f"Famiglia Esempio Nuova {n}"})
        assert time.perf_counter() - started < 2
    assert db.scalar("SELECT count(*) FROM Appointment WHERE family_id IS NULL") == 0
