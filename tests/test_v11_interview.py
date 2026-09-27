"""v1.1 — Slice 4: pagina Colloquio, Prepara incontro, sezioni A–K, «Salva» e «Concludi» (IW02–IW07)."""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from kite_admissions import catalog
from kite_admissions.db import Database
from kite_admissions.gcal.fake import FakeCalendarSource
from kite_admissions.schema import FOLLOWUP_ACTIONS
from kite_admissions.services import interview as interview_service

from . import dataset
from .calendar_data import CAL, timed
from .forms import read_form, with_changes
from .helpers import create_family, create_lead, interactions, revision

STATIC = Path(__file__).resolve().parents[1] / "kite_admissions" / "static"


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


def import_event(app, client, fake, event) -> str:
    fake.put(CAL, event)
    refresh(app, client)
    client.post("/appuntamenti/importa", data={"event_id": [event["id"]]})
    db = Database(app.extensions["kite"].paths.db_path)
    try:
        return db.scalar("SELECT id FROM Appointment WHERE google_event_id = ?", (event["id"],))
    finally:
        db.close()


def meeting(app, client, fake, family_id, lead_id=None, *, event_id="evt-1", start="2026-09-30T15:00:00+02:00",
            minutes=60, title="Visita Esempio Colloquio") -> str:
    appointment_id = import_event(app, client, fake, timed(event_id, start, minutes=minutes, title=title))
    response = client.post(f"/appuntamenti/{appointment_id}/collega",
                           data={"family_id": family_id, "lead_id": lead_id or "", "revision": 1})
    assert response.status_code == 302
    return appointment_id


def open_page(client, appointment_id, section=""):
    response = client.get(f"/appuntamenti/{appointment_id}/colloquio" + (f"?sezione={section}" if section else ""))
    assert response.status_code == 200, response.get_data(as_text=True)[:500]
    html = response.get_data(as_text=True)
    return html, read_form(html, "colloquio")


def submit(client, appointment_id, fields, action="save:a", changes=None, **more):
    data = with_changes(fields, changes, **more)
    data["action"] = action
    return client.post(f"/appuntamenti/{appointment_id}/colloquio", data=data)


def row(db, table, row_id):
    return db.one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))


def stored(db, appointment_id):
    return json.loads(row(db, "Appointment", appointment_id)["interview"])


@pytest.fixture
def luca(app, client, fake):
    """Famiglia con Luca (Primaria 1ª) e la sorella Anna; visita di ieri riferita a Luca."""
    family_id = create_family(client, display_name="Famiglia Esempio Colloquio", primary_phone="+39 0773 000 555",
                              preliminary_notes="Preferiscono incontri dopo le 17 (esempio)")
    lead_id = create_lead(client, family_id, display_name="Luca Esempio", school_year="2027/2028", grade="Primaria 1ª")
    sister = create_lead(client, family_id, display_name="Anna Esempio", school_year="2028/2029", grade="Infanzia")
    appointment_id = meeting(app, client, fake, family_id, lead_id)
    return {"family": family_id, "lead": lead_id, "sister": sister, "appointment": appointment_id}


# --- IW02: Prepara incontro -----------------------------------------------------------------

def test_unlinked_appointment_asks_to_link_first(app, client, fake, db):
    appointment_id = import_event(app, client, fake, timed("evt-x", "2026-09-30T15:00:00+02:00"))
    page = client.get(f"/appuntamenti/{appointment_id}/colloquio").get_data(as_text=True)
    assert "Prima collega la famiglia" in page and 'id="colloquio"' not in page
    response = client.post(f"/appuntamenti/{appointment_id}/colloquio", data={"action": "save:a"})
    assert response.status_code == 302 and "#locale" in response.headers["Location"]
    assert row(db, "Appointment", appointment_id)["revision"] == 1


def test_prepare_panel_is_computed_from_every_table(app, client, fake, db, luca, clock):
    family, lead = luca["family"], luca["lead"]
    client.post(f"/famiglie/{family}/note", data={"new_id": str(uuid.uuid4()), "type": "TELEFONATA",
                                                 "text": "Chiesto del trasporto (esempio)"})
    client.post(f"/famiglie/{family}/follow-up/nuovo", data={"new_id": str(uuid.uuid4()), "action": "FISSARE_VISITA",
                                                            "due_on": "2026-09-25", "assignee": "Segreteria"})
    offer_id = str(uuid.uuid4())
    client.post(f"/famiglie/{family}/offerte/nuova", data={"new_id": offer_id, "student_lead_id": lead,
                                                          "proposed_fee": "4.860", "periodicity": "ANNUALE"})
    client.post(f"/famiglie/{family}/offerte/{offer_id}/comunica", data={"revision": 1})
    earlier = meeting(app, client, fake, family, lead, event_id="evt-0", start="2026-09-20T10:00:00+02:00",
                      title="Primo contatto")
    fields = open_page(client, earlier)[1]
    submit(client, earlier, fields, "conclude", kind="PRIMA_VISITA", interest="ALTO", obstacle="COSTO",
           outcome="SVOLTA", step1_action="RICHIAMARE", step1_due_on="2026-10-09")
    html, fields = open_page(client, luca["appointment"], "prepara")
    prepare = html.split('id="sez-prepara"')[1].split('id="sez-a"')[0]
    for text in ("Prepara incontro", "Famiglia Esempio Colloquio", "telefono ✓", "email —",
                 "Preferiscono incontri dopo le 17", "Luca Esempio", "Anna Esempio · Infanzia 2028/2029 · In corso",
                 "Prima visita", "interesse alto", "ostacolo costo", "Richiamare entro 09/10/2026",
                 "v1 comunicata", "finale 4.860,00 € annuale", "Far fissare la visita", "Segreteria",
                 "Telefonata", "Chiesto del trasporto", "Da chiedere o confermare", "come ci hanno conosciuto",
                 "data di nascita di Luca Esempio", "lingue di Luca Esempio", "scuola attuale di Luca Esempio"):
        assert text in prepare, text
    assert "un recapito della famiglia" not in prepare and "cosa cercano (mai raccolto)" in prepare
    assert 'name="kind"' in prepare and 'name="student_lead_id"' in prepare
    response = submit(client, luca["appointment"], fields, "save:prepara", preparation="Portare il POF (esempio)",
                      kind="SECONDA_VISITA")
    assert response.status_code == 302 and response.headers["Location"].endswith("sezione=prepara")
    saved = row(db, "Appointment", luca["appointment"])
    assert saved["preparation"] == "Portare il POF (esempio)" and stored(db, luca["appointment"])["kind"] == "SECONDA_VISITA"


# --- IW03: colloquio durante l'incontro ---------------------------------------------------------

def test_sections_a_to_k_in_order_with_summary_and_server_side_open(client, luca):
    html, _ = open_page(client, luca["appointment"])
    positions = [html.index(f'id="sez-{code}"') for code in interview_service.SECTIONS]
    assert positions == sorted(positions)
    assert re.search(r'id="sez-k"\s+open', html)  # visita passata senza esito: si apre la conclusione
    assert len(re.findall(r'class="iv-section[^"]*" id="sez-[a-z]+"\s+open', html)) == 1
    html, _ = open_page(client, luca["appointment"], "c")
    assert re.search(r'id="sez-c"\s+open', html) and not re.search(r'id="sez-k"\s+open', html)
    for tag in ("Registra", "Domanda", "Economico", "Interno KITE — non condividere con la famiglia"):
        assert tag in html
    assert "[Salva]" not in html and 'value="save:c"' in html and 'value="conclude"' in html


def test_save_writes_only_the_touched_records_and_never_outcome_or_steps(client, db, luca):
    appointment, lead, family = luca["appointment"], luca["lead"], luca["family"]
    before = {"a": revision(db, "Appointment", appointment), "s": revision(db, "StudentLead", lead),
              "f": revision(db, "Family", family)}
    _, fields = open_page(client, appointment)
    response = submit(client, appointment, fields, "save:c", motivations=["LINGUE", "ORARI"],
                      outcome="SVOLTA", step1_action="RICHIAMARE", step1_due_on="2026-10-04",
                      q_1="C'è il trasporto?")
    assert response.status_code == 302 and response.headers["Location"].endswith("sezione=c")
    saved = row(db, "Appointment", appointment)
    assert saved["visit_outcome"] is None and db.scalar("SELECT count(*) FROM FollowUp") == 0
    content = stored(db, appointment)
    assert content["motivations"] == ["LINGUE", "ORARI"] and content["questions"][0]["q"] == "C'è il trasporto?"
    assert content["closing_draft"]["outcome"] == "SVOLTA"  # la bozza di K non si perde
    assert content["closing_draft"]["steps"][0]["action"] == "RICHIAMARE"
    assert saved["revision"] == before["a"] + 1
    assert (revision(db, "StudentLead", lead), revision(db, "Family", family)) == (before["s"], before["f"])

    _, fields = open_page(client, appointment)
    assert fields["outcome"] == "SVOLTA" and fields["step1_action"] == "RICHIAMARE"  # bozza ripresentata
    response = submit(client, appointment, fields, "save:a", s1_current_school="Scuola Esempio",
                      contact_source="Altra famiglia KITE")
    assert response.status_code == 302
    assert row(db, "StudentLead", lead)["current_school"] == "Scuola Esempio"
    assert row(db, "Family", family)["contact_source"] == "Altra famiglia KITE"
    assert revision(db, "Appointment", appointment) == before["a"] + 1  # colloquio non toccato: non riscritto
    assert interactions(db) == []  # nessuna voce di cronologia per salvataggi e spunte


def test_untouched_form_writes_nothing(client, db, luca):
    before = revision(db, "Appointment", luca["appointment"])
    _, fields = open_page(client, luca["appointment"])
    response = submit(client, luca["appointment"], fields, "save:k")
    assert response.status_code == 302
    assert "Nessuna modifica" in client.get(response.headers["Location"]).get_data(as_text=True)
    assert revision(db, "Appointment", luca["appointment"]) == before
    assert row(db, "Appointment", luca["appointment"])["interview"] == "{}"


def test_validation_error_saves_nothing_and_keeps_the_input(client, db, luca):
    _, fields = open_page(client, luca["appointment"])
    response = submit(client, luca["appointment"], fields, "save:c", motivations=["LINGUE"],
                      s1_birth_date="2030-01-01")
    assert response.status_code == 422
    html = response.get_data(as_text=True)
    assert "Data di nascita non valida" in html
    again = read_form(html, "colloquio")
    assert again["motivations"] == "LINGUE" and again["s1_birth_date"] == "2030-01-01"
    assert re.search(r'id="sez-a"\s+open', html)  # riapre la sezione del campo sbagliato
    assert row(db, "Appointment", luca["appointment"])["interview"] == "{}"
    response = submit(client, luca["appointment"], fields, "save:h", q_1="", a_1="Risposta senza domanda")
    assert response.status_code == 422 and "non ha la sua domanda" in response.get_data(as_text=True)


def test_conflict_saves_nothing_and_asks_an_explicit_choice(client, db, luca):
    appointment, lead = luca["appointment"], luca["lead"]
    _, tab_a = open_page(client, appointment)
    _, tab_b = open_page(client, appointment)
    assert submit(client, appointment, tab_a, "save:c", motivations=["LINGUE"]).status_code == 302
    lead_revision = revision(db, "StudentLead", lead)
    response = submit(client, appointment, tab_b, "save:f", needs_schedule="uscita 16:30",
                      s1_current_school="Scuola B (esempio)")
    assert response.status_code == 409
    html = response.get_data(as_text=True)
    assert "Salvataggio non eseguito" in html and "Cosa cercano" in html and "Mantieni i miei valori" in html
    assert "needs" not in stored(db, appointment) and revision(db, "StudentLead", lead) == lead_revision
    second = read_form(html, "colloquio")
    assert second["needs_schedule"] == "uscita 16:30" and "a_current_rev" in second  # valori inviati ripresentati
    response = submit(client, appointment, second, "save:f", a_resolve="mine")
    assert response.status_code == 302
    content = stored(db, appointment)
    assert content["needs"]["schedule"] == "uscita 16:30" and "motivations" not in content  # sovrascritto su conferma
    assert row(db, "StudentLead", lead)["current_school"] == "Scuola B (esempio)"


def test_conflict_resolved_with_the_current_values(client, db, luca):
    appointment = luca["appointment"]
    _, tab_a = open_page(client, appointment)
    _, tab_b = open_page(client, appointment)
    submit(client, appointment, tab_a, "save:c", motivations=["LINGUE"])
    html = submit(client, appointment, tab_b, "save:c", motivations=["ORARI"],
                  contact_source="Passaparola").get_data(as_text=True)
    response = submit(client, appointment, read_form(html, "colloquio"), "save:c", a_resolve="theirs")
    assert response.status_code == 302
    assert stored(db, appointment)["motivations"] == ["LINGUE"]
    assert row(db, "Family", luca["family"])["contact_source"] == "Passaparola"
    assert "Mantenuti i valori attuali" in client.get(response.headers["Location"]).get_data(as_text=True)


def test_repeated_submission_is_not_an_error(client, db, luca):
    appointment = luca["appointment"]
    _, fields = open_page(client, appointment)
    first = submit(client, appointment, fields, "save:c", motivations=["LINGUE"])
    second = submit(client, appointment, fields, "save:c", motivations=["LINGUE"])
    assert first.status_code == second.status_code == 302
    assert revision(db, "Appointment", appointment) == 3  # 1 import, 2 collegamento, 3 un solo salvataggio


def test_changes_to_fields_not_shown_are_not_a_conflict(client, db, luca):
    appointment, family, lead = luca["appointment"], luca["family"], luca["lead"]
    _, fields = open_page(client, appointment)
    client.post(f"/famiglie/{family}/richieste/{lead}/modifica", data={
        "display_name": "Luca Esempio", "school_year": "2027/2028", "grade": "Primaria 1ª",
        "notes": "Nota della richiesta (esempio)", "revision": revision(db, "StudentLead", lead)})
    response = submit(client, appointment, fields, "save:a", s1_current_school="Scuola Esempio")
    assert response.status_code == 302
    saved = row(db, "StudentLead", lead)
    assert (saved["current_school"], saved["notes"]) == ("Scuola Esempio", "Nota della richiesta (esempio)")


def test_codes_are_validated_and_removed_codes_are_kept(client, db, luca):
    appointment = luca["appointment"]
    _, fields = open_page(client, appointment)
    response = submit(client, appointment, fields, "save:c", motivations=["INVENTATA"])
    assert response.status_code == 422 and "voce non valida" in response.get_data(as_text=True)
    db.execute("UPDATE Appointment SET interview = ? WHERE id = ?",
               ('{"format": 1, "motivations": ["VECCHIA_VOCE"], "assessment": {"obstacle": "VECCHIO"}}', appointment))
    html, fields = open_page(client, appointment)
    assert "VECCHIA_VOCE (voce non più in elenco)" in html and "VECCHIO (voce non più in elenco)" in html
    assert submit(client, appointment, fields, "save:c", motivations_note="nota (esempio)").status_code == 302
    content = stored(db, appointment)
    assert content["motivations"] == ["VECCHIA_VOCE"] and content["assessment"]["obstacle"] == "VECCHIO"


def test_leave_guard_and_quick_choices_are_wired(client, luca, clock):
    html, _ = open_page(client, luca["appointment"])
    assert re.search(r'<form method="post" id="colloquio"[^>]*data-guard[^>]*novalidate', html)
    today = date(2026, 10, 1)
    assert f'data-action="RICHIAMARE" data-due="{(today + timedelta(days=3)).isoformat()}"' in html
    assert f'data-action="INVIARE_INFORMAZIONI" data-due="{(today + timedelta(days=1)).isoformat()}" data-note="Proposta economica"' in html
    assert f'ALCUNE_SETTIMANE={(today + timedelta(days=14)).isoformat()}' in html and 'data-close-leads="1"' in html
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "beforeunload" in script and "data-guard" in script and "[data-quick]" in script
    assert "<script>" not in html and "style=" not in html


def test_save_and_go_opens_v1_forms_and_returns_to_the_interview(client, db, luca):
    appointment, family, lead = luca["appointment"], luca["family"], luca["lead"]
    _, fields = open_page(client, appointment)
    response = submit(client, appointment, fields, "go:new_lead", motivations=["LINGUE"])
    location = response.headers["Location"]
    assert response.status_code == 302 and f"/famiglie/{family}/richieste/nuova?next=" in location
    target = parse_qs(urlsplit(location).query)["next"][0]
    assert target == f"/appuntamenti/{appointment}/colloquio?sezione=a"
    assert stored(db, appointment)["motivations"] == ["LINGUE"]
    back = client.get(location).get_data(as_text=True)
    assert f'name="next" value="/appuntamenti/{appointment}/colloquio?sezione=a"' in back
    _, fields = open_page(client, appointment)
    location = submit(client, appointment, fields, f"go:offer_new:{lead}").headers["Location"]
    assert f"/offerte/nuova?ambito={lead}&next=" in location
    location = submit(client, appointment, fields, "go:edit_family").headers["Location"]
    assert f"/famiglie/{family}/modifica?next=" in location
    other = create_family(client, display_name="Famiglia Esempio Estranea")
    foreign = str(uuid.uuid4())
    client.post(f"/famiglie/{other}/offerte/nuova", data={"new_id": foreign, "proposed_fee": "1", "periodicity": "ANNUALE"})
    response = submit(client, appointment, fields, f"go:offer_edit:{foreign}", motivations=["ORARI"])
    assert response.status_code == 422 and stored(db, appointment)["motivations"] == ["LINGUE"]


# --- IW04: dichiarato e interno -------------------------------------------------------------

def test_declared_and_internal_parts_are_labelled(client, db, luca):
    html, fields = open_page(client, luca["appointment"])
    assert "Profilo scolastico (dichiarato dalla famiglia)" in html and "Lingue (dichiarate)" in html
    assert 'class="iv-section iv-internal"' in html and "Interno KITE — non condividere né leggere alla famiglia" in html
    submit(client, luca["appointment"], fields, "conclude", motivations=["LINGUE"], interest="ALTO",
           outcome="SVOLTA", step1_action="RICHIAMARE", step1_due_on="2026-10-04")
    detail = client.get(f"/appuntamenti/{luca['appointment']}").get_data(as_text=True)
    summary = detail.split('class="summary"')[1].split("</dl>")[0]
    assert 'class="sl-declared">Cerca (dichiarato)' in summary and 'class="sl-internal">Interno KITE' in summary
    assert "interesse ALTO" in summary


# --- IW05: domande e verifiche --------------------------------------------------------------

def test_question_rows_and_one_verification_step(client, db, luca):
    appointment = luca["appointment"]
    _, fields = open_page(client, appointment)
    assert all(f"q_{index}" in fields for index in (1, 2, 3)) and "q_4" not in fields
    submit(client, appointment, fields, "save:h", q_1="Trasporto dalla zona Esempio?", verify_1="1",
           q_2="Mensa biologica?", a_2="Sì (esempio)")
    html, fields = open_page(client, appointment, "h")
    assert "q_5" in fields and "2 da verificare" not in html and "1 da verificare" in html
    response = submit(client, appointment, fields, "conclude", outcome="SVOLTA", verify_step="1",
                      verify_due_on="2026-10-03", q_1="Trasporto dalla zona Esempio, anche il sabato?")
    assert response.status_code == 302
    steps = db.all("SELECT * FROM FollowUp WHERE appointment_id = ?", (appointment,))
    assert len(steps) == 1 and steps[0]["action"] == "ALTRO" and steps[0]["due_on"] == "2026-10-03"
    assert steps[0]["note"] == "Verificare e rispondere: Trasporto dalla zona Esempio, anche il sabato?"


def test_verification_step_needs_rows_to_verify(client, db, luca):
    _, fields = open_page(client, luca["appointment"])
    response = submit(client, luca["appointment"], fields, "conclude", outcome="SVOLTA", verify_step="1",
                      verify_due_on="2026-10-03", step1_action="RICHIAMARE", step1_due_on="2026-10-04")
    assert response.status_code == 422 and "da verificare" in response.get_data(as_text=True)
    assert db.scalar("SELECT count(*) FROM FollowUp") == 0


# --- IW06: economia dal colloquio -----------------------------------------------------------

def test_offer_actions_from_the_interview_are_atomic_and_idempotent(client, db, luca, clock):
    appointment, family, lead = luca["appointment"], luca["family"], luca["lead"]
    draft = str(uuid.uuid4())
    client.post(f"/famiglie/{family}/offerte/nuova", data={
        "new_id": draft, "student_lead_id": lead, "standard_fee": "5.400", "reduction_reason_1": "PROMOZIONE",
        "reduction_amount_1": "400", "proposed_fee": "5.000", "periodicity": "ANNUALE"})
    html, fields = open_page(client, appointment, "i")
    offer_part = html.split('id="sez-i"')[1].split('id="sez-j"')[0]
    assert "Per Luca Esempio · Primaria 1ª · 2027/2028" in offer_part and "Condizione riservata" in offer_part
    assert "Nessuna riduzione viene applicata automaticamente" in offer_part and "Anna Esempio" in offer_part
    response = submit(client, appointment, fields, f"offer_communicate:{draft}", motivations=["LINGUE"])
    assert response.status_code == 422 and "manca «Autorizzata da»" in response.get_data(as_text=True)
    assert row(db, "Appointment", appointment)["interview"] == "{}"  # atomico: niente salvato
    client.post(f"/famiglie/{family}/offerte/{draft}/modifica", data={
        "standard_fee": "5.400", "reduction_reason_1": "PROMOZIONE", "reduction_amount_1": "400",
        "authorized_by": "Direzione", "proposed_fee": "5.000", "periodicity": "ANNUALE", "revision": 1})
    _, fields = open_page(client, appointment, "i")
    response = submit(client, appointment, fields, f"offer_communicate:{draft}", motivations=["LINGUE"])
    assert response.status_code == 302 and response.headers["Location"].endswith("sezione=i")
    offer = row(db, "Offer", draft)
    assert (offer["status"], offer["communication_channel"]) == ("COMUNICATA", "Di persona")
    assert stored(db, appointment)["motivations"] == ["LINGUE"]
    clock.advance(minutes=1)
    again = submit(client, appointment, fields, f"offer_communicate:{draft}", motivations=["LINGUE"])
    assert again.status_code == 302 and row(db, "Offer", draft)["communicated_at"] == offer["communicated_at"]
    assert "già comunicata" in client.get(again.headers["Location"]).get_data(as_text=True)

    html, fields = open_page(client, appointment, "i")
    new_id = fields[f"offer_new_id_{draft}"]
    response = submit(client, appointment, fields, f"offer_version:{draft}")
    assert response.status_code == 302 and f"/offerte/{new_id}/modifica?next=" in response.headers["Location"]
    copy = row(db, "Offer", new_id)
    assert (copy["version_no"], copy["status"], copy["authorized_by"]) == (2, "BOZZA", None)
    repeat = submit(client, appointment, fields, f"offer_version:{draft}")
    assert f"/offerte/{new_id}/modifica" in repeat.headers["Location"]
    assert db.scalar("SELECT count(*) FROM Offer") == 2


# --- IW07: conclusione e prossimo passo -----------------------------------------------------

def conclude(client, appointment_id, section="k", **changes):
    _, fields = open_page(client, appointment_id, section)
    return submit(client, appointment_id, fields, "conclude", outcome=changes.pop("outcome", "SVOLTA"), **changes)


def test_visit_done_needs_a_step_after_the_visit(client, db, luca):
    response = conclude(client, luca["appointment"])
    assert response.status_code == 422
    assert "serve un prossimo passo successivo all&#39;incontro per: Luca Esempio" in response.get_data(as_text=True)
    assert row(db, "Appointment", luca["appointment"])["visit_outcome"] is None
    assert db.scalar("SELECT count(*) FROM FollowUp") == 0


def test_meeting_in_progress_does_not_cover_itself(app, client, fake, db, clock):
    family_id = create_family(client, display_name="Famiglia Esempio In Corso")
    lead_id = create_lead(client, family_id)
    appointment = meeting(app, client, fake, family_id, lead_id, start="2026-10-01T09:30:00+02:00", minutes=90)
    response = conclude(client, appointment, visited_at="2026-10-01T09:35")
    assert response.status_code == 422 and "prossimo passo" in response.get_data(as_text=True)


def test_followup_created_before_the_visit_does_not_count(client, db, luca, clock):
    family, appointment = luca["family"], luca["appointment"]
    old = str(uuid.uuid4())
    clock.set(datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc))
    client.post(f"/famiglie/{family}/follow-up/nuovo", data={"new_id": old, "action": "FISSARE_VISITA",
                                                            "due_on": "2026-09-30"})
    clock.set(datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc))
    assert conclude(client, appointment).status_code == 422
    html, fields = open_page(client, appointment, "k")
    assert "Follow-up aperti da prima dell&#39;incontro" in html or "Follow-up aperti da prima dell'incontro" in html
    response = submit(client, appointment, fields, "conclude", {f"complete_{old}": "1"}, outcome="SVOLTA",
                      step1_action="RICHIAMARE", step1_due_on="2026-10-04")
    assert response.status_code == 302
    closed = row(db, "FollowUp", old)
    assert (closed["status"], closed["outcome"]) == ("COMPLETATO", "Completato con il colloquio del 30/09/2026")


def test_another_future_appointment_counts_but_not_if_cancelled(app, client, fake, db, luca):
    family, lead = luca["family"], luca["lead"]
    future = meeting(app, client, fake, family, lead, event_id="evt-2", start="2026-10-06T17:00:00+02:00",
                     title="Incontro economico")
    fake.cancel(CAL, "evt-2")
    refresh(app, client)
    assert conclude(client, luca["appointment"]).status_code == 422  # annullato: non conta
    fake.put(CAL, timed("evt-2", "2026-10-06T17:00:00+02:00", title="Incontro economico"))
    refresh(app, client)
    assert row(db, "Appointment", future)["src_status"] == "CONFIRMED"
    response = conclude(client, luca["appointment"])
    assert response.status_code == 302 and row(db, "Appointment", luca["appointment"])["visit_outcome"] == "SVOLTA"
    assert db.scalar("SELECT count(*) FROM FollowUp") == 0


def test_paused_or_closed_requests_are_covered_and_family_without_requests_needs_a_step(app, client, fake, db):
    family_id = create_family(client, display_name="Famiglia Esempio Pausa")
    lead_id = create_lead(client, family_id)
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "IN_PAUSA", "review_on": "2026-11-01", "revision": 1})
    paused = meeting(app, client, fake, family_id, lead_id)
    assert conclude(client, paused).status_code == 302
    empty = create_family(client, display_name="Famiglia Esempio Senza Richieste")
    appointment = meeting(app, client, fake, empty, event_id="evt-9", title="Visita senza richieste")
    response = conclude(client, appointment)
    assert response.status_code == 422 and "la famiglia (nessuna richiesta registrata)" in response.get_data(as_text=True)
    assert conclude(client, appointment, step1_action="RICHIAMARE", step1_due_on="2026-10-02").status_code == 302


def test_waiting_for_the_family_is_a_valid_next_step(client, db, luca):
    response = conclude(client, luca["appointment"], step1_action=catalog.WAIT_ACTION, step1_due_on="2026-10-15",
                        step1_note="valutano con i nonni (esempio)")
    assert response.status_code == 302
    step = db.one("SELECT * FROM FollowUp")
    assert (step["action"], step["due_on"], step["appointment_id"]) == ("ALTRO", "2026-10-15", luca["appointment"])
    assert step["note"] == "Attendere risposta della famiglia — valutano con i nonni (esempio)"
    assert interview_service.step_label(step) == catalog.WAIT_NOTE
    page = client.get("/").get_data(as_text=True)
    uncovered = page.split("Pratiche senza prossimo passo")[1].split("</section>")[0]
    assert "Senza azione: Anna Esempio" in uncovered and "Luca" not in uncovered  # Luca è coperto dall'attesa
    assert FOLLOWUP_ACTIONS == ("RICHIAMARE", "INVIARE_INFORMAZIONI", "FISSARE_VISITA", "ALTRO")  # nessuna azione nuova


def test_quick_choices_only_prefill_v1_actions():
    for outcome in catalog.QUICK_OUTCOMES:
        assert outcome.action in FOLLOWUP_ACTIONS + (catalog.WAIT_ACTION, None)
    items = {item["code"]: item for item in interview_service.quick_outcomes(date(2026, 10, 1))}
    assert items["RICHIAMO"]["due"] == "2026-10-04" and items["SECONDA_VISITA"]["due"] == "2026-10-03"
    assert items["INVIO_PROPOSTA"]["note"] == "Proposta economica" and items["INVIO_DOCUMENTI"]["due"] == "2026-10-03"
    assert "IMMEDIATA=2026-10-04" in items["ATTENDERE_RISPOSTA"]["by_timing"]
    assert "DA_DEFINIRE=2026-10-08" in items["ATTENDERE_RISPOSTA"]["by_timing"]
    assert items["NON_PROSEGUE"]["close"] and not items["NON_PROSEGUE"]["action"]


def test_not_continuing_closes_each_request_with_one_interaction(app, client, fake, db):
    family_id = create_family(client, display_name="Famiglia Esempio Chiusura")
    first = create_lead(client, family_id, display_name="Primo Esempio")
    second = create_lead(client, family_id, display_name="Seconda Esempio", school_year="2028/2029")
    appointment = meeting(app, client, fake, family_id)  # incontro di tutta la famiglia
    html, fields = open_page(client, appointment, "k")
    assert fields["s1_id"] and fields["s2_id"]  # un blocco per ogni richiesta aperta
    response = submit(client, appointment, fields, "conclude", {
        f"close_{first}": "1", f"close_reason_{first}": "Costo", f"close_{second}": "1"},
        outcome="SVOLTA", obstacle="COSTO")
    assert response.status_code == 302
    for lead_id in (first, second):
        assert row(db, "StudentLead", lead_id)["status"] == "NON_PROSEGUE"
    rows = interactions(db, type="LEAD_NOT_CONTINUING")
    assert len(rows) == 2 and len(interactions(db)) == 2
    assert row(db, "StudentLead", first)["closure_reason"] == "Costo"


def test_steps_carry_origin_and_assignee_and_double_conclude_is_harmless(client, db, luca):
    appointment = luca["appointment"]
    _, fields = open_page(client, appointment, "k")
    data = dict(outcome="SVOLTA", step1_action="INVIARE_INFORMAZIONI", step1_due_on="2026-10-02",
                step1_assignee="Segreteria", step1_note="Proposta economica", step2_action="FISSARE_VISITA",
                step2_due_on="2026-10-03", step2_lead=luca["sister"], materials=["LISTINO", "MODULO_ISCRIZIONE"])
    first = submit(client, appointment, fields, "conclude", **data)
    assert first.status_code == 302 and first.headers["Location"].endswith(f"/appuntamenti/{appointment}#colloquio")
    steps = db.all("SELECT * FROM FollowUp WHERE appointment_id = ? ORDER BY action DESC", (appointment,))
    assert [(s["action"], s["assignee"]) for s in steps] == [("INVIARE_INFORMAZIONI", "Segreteria"),
                                                              ("FISSARE_VISITA", "Ionut")]
    assert steps[0]["note"] == "Proposta economica — Materiale: Rette e servizi, Modulo di iscrizione"
    assert steps[1]["student_lead_id"] == luca["sister"]
    concluded = row(db, "Appointment", appointment)
    second = submit(client, appointment, fields, "conclude", **data)
    assert second.status_code == 302 and "già concluso" in client.get(second.headers["Location"]).get_data(as_text=True)
    assert db.scalar("SELECT count(*) FROM FollowUp") == 2
    assert row(db, "Appointment", appointment)["revision"] == concluded["revision"]
    assert "closing_draft" not in stored(db, appointment) and stored(db, appointment)["conclusion_id"]


def test_materials_need_a_send_information_step(client, db, luca):
    response = conclude(client, luca["appointment"], step1_action="RICHIAMARE", step1_due_on="2026-10-04",
                        materials=["LISTINO"])
    assert response.status_code == 422 and "Inviare informazioni" in response.get_data(as_text=True)
    response = conclude(client, luca["appointment"], step1_action="INVIARE_INFORMAZIONI", step1_due_on="2026-10-02",
                        materials=["ALTRO"], materials_other="orari del pulmino (esempio)")
    assert response.status_code == 302
    assert db.scalar("SELECT note FROM FollowUp") == "Materiale: altro: orari del pulmino (esempio)"


def test_other_step_validation(client, db, luca):
    for changes, message in (({"step1_action": "ALTRO", "step1_due_on": "2026-10-04"}, "descrivi l&#39;azione"),
                             ({"step1_action": "RICHIAMARE"}, "entro quando"),
                             ({"step1_note": "senza azione"}, "scegli l&#39;azione"),
                             ({"outcome": "", "step1_action": "RICHIAMARE", "step1_due_on": "2026-10-04"}, "scegli l&#39;esito"),
                             ({"visited_at": "2026-10-09T10:00", "step1_action": "RICHIAMARE",
                               "step1_due_on": "2026-10-04"}, "nel futuro")):
        response = conclude(client, luca["appointment"], **changes)
        assert response.status_code == 422 and message in response.get_data(as_text=True), message
    assert db.scalar("SELECT count(*) FROM FollowUp") == 0


def test_not_attended_needs_no_step(client, db, luca):
    response = conclude(client, luca["appointment"], outcome="NON_PRESENTATA", visited_at="")
    assert response.status_code == 302
    assert row(db, "Appointment", luca["appointment"])["visit_outcome"] == "NON_PRESENTATA"
    html, fields = open_page(client, luca["appointment"], "k")
    assert "Esito già registrato: Famiglia non presentata" in html and fields["outcome"] == "NON_PRESENTATA"


def test_v1_report_route_applies_the_same_rule(client, db, luca):
    appointment = luca["appointment"]
    url = f"/appuntamenti/{appointment}/resoconto"
    page = client.post(url, data={"visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:00",
                                  "revision": revision(db, "Appointment", appointment)},
                       follow_redirects=True).get_data(as_text=True)
    assert "prossimo passo successivo" in page and row(db, "Appointment", appointment)["visit_outcome"] is None
    response = client.post(url, data={"visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:00",
                                      "next_action": "RICHIAMARE", "next_due_on": "2026-10-04",
                                      "next_new_id": str(uuid.uuid4()),
                                      "revision": revision(db, "Appointment", appointment)})
    assert response.status_code == 302 and row(db, "Appointment", appointment)["visit_outcome"] == "SVOLTA"
    assert db.scalar("SELECT appointment_id FROM FollowUp") == appointment
    client.post(url, data={"visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:10", "visit_report": "Rettifica",
                           "revision": revision(db, "Appointment", appointment)})
    assert row(db, "Appointment", appointment)["visit_report"] == "Rettifica"  # correzione: la regola non si riattiva


def test_after_conclusion_save_corrects_but_never_passes_to_done(client, db, luca):
    appointment = luca["appointment"]
    assert conclude(client, appointment, step1_action="RICHIAMARE", step1_due_on="2026-10-04").status_code == 302
    html, fields = open_page(client, appointment, "k")
    assert fields["a_phase"] == "done" and "Prossimi passi nati da questo incontro" in html and "Richiamare" in html
    stale = dict(fields)
    assert submit(client, appointment, fields, "save:k", visit_outcome="NON_PRESENTATA").status_code == 302
    assert row(db, "Appointment", appointment)["visit_outcome"] == "NON_PRESENTATA"
    response = submit(client, appointment, stale, "save:k", visit_report="da un'altra scheda", a_resolve="mine",
                      a_current_rev=str(revision(db, "Appointment", appointment)))
    assert response.status_code == 422 and "Concludi colloquio" in response.get_data(as_text=True)
    _, fields = open_page(client, appointment, "k")
    assert fields["a_phase"] == "open"
    submit(client, appointment, fields, "save:k", outcome="SVOLTA")
    assert row(db, "Appointment", appointment)["visit_outcome"] == "NON_PRESENTATA"  # «Salva» non registra l'esito
    response = conclude(client, appointment)  # il passo creato alla prima conclusione vale ancora come copertura
    assert response.status_code == 302 and row(db, "Appointment", appointment)["visit_outcome"] == "SVOLTA"


def test_change_link_moves_the_interview_and_detaches_the_steps(client, db, luca):
    appointment, family = luca["appointment"], luca["family"]
    conclude(client, appointment, motivations=["LINGUE"], step1_action="RICHIAMARE", step1_due_on="2026-10-04")
    other = create_family(client, display_name="Famiglia Esempio Giusta Colloquio")
    page = client.get(f"/appuntamenti/{appointment}/cambia?famiglia={other}").get_data(as_text=True)
    assert "insieme al colloquio" in page and "si staccano dal colloquio" in page and "restano lì" in page
    step_id = db.scalar("SELECT id FROM FollowUp")
    step_revision = revision(db, "FollowUp", step_id)
    client.post(f"/appuntamenti/{appointment}/cambia", data={"family_id": other,
                                                            "revision": revision(db, "Appointment", appointment)})
    moved = row(db, "Appointment", appointment)
    assert moved["family_id"] == other and stored(db, appointment)["motivations"] == ["LINGUE"]
    step = row(db, "FollowUp", step_id)
    assert (step["family_id"], step["appointment_id"], step["revision"]) == (family, None, step_revision + 1)


def test_remove_is_refused_when_the_interview_has_content(app, client, fake, db):
    appointment = import_event(app, client, fake, timed("evt-r", "2026-10-05T09:30:00+02:00"))
    db.execute("UPDATE Appointment SET interview = ? WHERE id = ?", ('{"format": 1, "motivations": ["LINGUE"]}', appointment))
    page = client.get(f"/appuntamenti/{appointment}").get_data(as_text=True)
    assert "Togli dagli appuntamenti" not in page
    client.post(f"/appuntamenti/{appointment}/rimuovi")
    assert row(db, "Appointment", appointment) is not None


def test_student_facts_are_updated_in_their_request(client, db, luca):
    appointment, lead = luca["appointment"], luca["lead"]
    _, fields = open_page(client, appointment)
    response = submit(client, appointment, fields, "save:a", s1_birth_date="2021-03-14", s1_lang_IT="MADRELINGUA",
                      s1_educational_review="1", s1_educational_review_note="Colloquio con il coordinamento")
    assert response.status_code == 302
    saved = row(db, "StudentLead", lead)
    assert (saved["birth_date"], saved["birth_year"], saved["educational_review"]) == ("2021-03-14", 2021, 1)
    html, fields = open_page(client, appointment)
    assert "età regolare per la Primaria 1ª" in html
    submit(client, appointment, fields, "save:d", s1_educational_review=None)
    saved = row(db, "StudentLead", lead)
    assert (saved["educational_review"], saved["educational_review_note"], saved["notes"]) == (0, None, None)


def test_saving_and_concluding_are_fast_on_the_synthetic_dataset(app, client, fake, clock, db):
    ids = dataset.build(app, client, fake, clock)
    appointment = meeting(app, client, fake, ids["rossi"], ids["anna"], event_id="evt-veloce",
                          start="2026-10-02T10:00:00+02:00")
    clock.set(datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc))
    for action, changes in (("save:c", {"motivations": ["LINGUE"]}),
                            ("conclude", {"outcome": "SVOLTA", "step1_action": "RICHIAMARE",
                                          "step1_due_on": "2026-10-05"})):
        _, fields = open_page(client, appointment)
        started = time.perf_counter()
        response = submit(client, appointment, fields, action, **changes)
        assert response.status_code == 302 and time.perf_counter() - started < 2
