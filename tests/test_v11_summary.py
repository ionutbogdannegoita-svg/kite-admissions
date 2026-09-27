"""v1.1 — Slice 5: riepilogo, storico, cronologia, Oggi, export, privacy (IW08, IW09, IW11, IW12, IW14)."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid
import zipfile
from datetime import datetime, timezone

import pytest

from kite_admissions.db import Database
from kite_admissions.gcal.fake import FakeCalendarSource
from kite_admissions.services import interview as interview_service
from kite_admissions.services import timeline as timeline_service

from .calendar_data import CAL, timed
from .forms import read_form, with_changes
from .helpers import create_family, create_lead, revision

FORBIDDEN_STEMS = ("diagnos", "certific", "terapi", "farmac", "sanitar", "salute", "allerg", "intoller", "dieta",
                   "religi", "politic", "etnia", "cittadinanza", "reddito", "isee", "professione", "separa", "affido",
                   "tutore")


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


def meeting(app, client, fake, family_id, lead_id=None, *, event_id="evt-1", start="2026-09-30T15:00:00+02:00",
            title="Visita Esempio Riepilogo") -> str:
    fake.put(CAL, timed(event_id, start, title=title))
    refresh(app, client)
    client.post("/appuntamenti/importa", data={"event_id": [event_id]})
    db = Database(app.extensions["kite"].paths.db_path)
    try:
        appointment_id = db.scalar("SELECT id FROM Appointment WHERE google_event_id = ?", (event_id,))
    finally:
        db.close()
    client.post(f"/appuntamenti/{appointment_id}/collega", data={"family_id": family_id, "lead_id": lead_id or "",
                                                                "revision": 1})
    return appointment_id


def fields_of(client, appointment_id, section="k"):
    html = client.get(f"/appuntamenti/{appointment_id}/colloquio?sezione={section}").get_data(as_text=True)
    return read_form(html, "colloquio")


def post(client, appointment_id, action, changes=None, **more):
    data = with_changes(fields_of(client, appointment_id), changes, **more)
    data["action"] = action
    response = client.post(f"/appuntamenti/{appointment_id}/colloquio", data=data)
    assert response.status_code == 302, response.get_data(as_text=True)[:1500]
    return response


def summary(app, appointment_id, clock):
    db = Database(app.extensions["kite"].paths.db_path)
    try:
        row = db.one(f"{interview_service.appointment_service.SELECT} WHERE a.id = ?", (appointment_id,))
        return interview_service.summary(db, row, clock())
    finally:
        db.close()


FULL = {
    "kind": "PRIMA_VISITA", "attendees": ["MADRE", "PADRE", "ALUNNO"],
    "s1_birth_date": "2021-03-14", "s1_current_school": "Scuola dell'infanzia Esempio", "s1_current_grade": "Infanzia",
    "s1_lang_IT": "MADRELINGUA", "s1_lang_EN": "BASE", "s1_lang_FR": "NESSUNA",
    "contact_source": "Altra famiglia KITE",
    "motivations": ["LINGUE", "AMBIENTE_INTERNAZIONALE", "ATTENZIONE_INDIVIDUALE"], "motivations_note": "inglese ogni giorno",
    "needs_services": ["POST_SCUOLA", "MENSA"], "needs_schedule": "uscita 16:30", "needs_siblings": "0",
    "needs_desired_start": "2027-09-13",
    "presented": ["PROGETTO_EDUCATIVO", "INTERNAZIONALE", "LINGUE", "ORARI", "MENSA", "ATTIVITA", "SERVIZI",
                  "EXTRACURRICOLARI", "COMUNICAZIONE", "INSERIMENTO", "COSTI", "ISCRIZIONE"],
    "q_1": "C'è il trasporto dalla zona Esempio?", "verify_1": "1", "q_2": "Mensa interna?", "a_2": "Sì (esempio)",
    "interest": "ALTO", "timing": "ALCUNE_SETTIMANE", "driver": "LINGUE", "obstacle": "TRASPORTO",
    "local_observations": "Nota interna di esempio",
}
CLOSING = {"outcome": "SVOLTA", "visited_at": "2026-09-30T15:05", "step1_action": "INVIARE_INFORMAZIONI",
           "step1_due_on": "2026-10-02", "step1_assignee": "Segreteria", "materials": ["LISTINO", "MODULO_ISCRIZIONE"],
           "verify_step": "1", "verify_due_on": "2026-10-03", "step2_action": "RICHIAMARE", "step2_due_on": "2026-10-09"}


@pytest.fixture
def full_case(app, client, fake, clock):
    """Il caso del riepilogo della specifica (§4): prima visita di Luca, proposta comunicata nel giorno."""
    clock.set(datetime(2026, 9, 30, 14, 0, tzinfo=timezone.utc))  # 16:00 a Roma, subito dopo la visita
    family_id = create_family(client, display_name="Famiglia Esempio Rossi-Riepilogo", primary_phone="+39 0773 000 777")
    lead_id = create_lead(client, family_id, display_name="Luca Esempio", school_year="2027/2028", grade="Primaria 1ª")
    create_lead(client, family_id, display_name="Anna Esempio", school_year="2028/2029", grade="Infanzia")
    appointment_id = meeting(app, client, fake, family_id, lead_id)
    offer_id = str(uuid.uuid4())
    client.post(f"/famiglie/{family_id}/offerte/nuova", data={
        "new_id": offer_id, "student_lead_id": lead_id, "standard_fee": "5.400", "reduction_reason_1": "FRATELLI",
        "reduction_amount_1": "540", "proposed_fee": "4.860", "enrollment_fee": "300", "valid_until": "2026-10-31",
        "periodicity": "ANNUALE"})
    post(client, appointment_id, f"offer_communicate:{offer_id}", FULL)
    post(client, appointment_id, "conclude", CLOSING)
    return {"family": family_id, "lead": lead_id, "appointment": appointment_id, "offer": offer_id}


# --- IW08: riepilogo --------------------------------------------------------------------------

def test_summary_matches_the_specification_in_at_most_twelve_lines(app, client, clock, full_case):
    result = summary(app, full_case["appointment"], clock)
    assert result.structured and result.latest and len(result.lines) <= result.limit == 12
    text = {line.label: line.text for line in result.lines if line.label}
    assert text["Colloquio"] == "Prima visita · mer 30/09/2026 15:05 · 1° incontro · presenti: madre, padre, l'alunno/a"
    assert text["Alunno"] == ("Luca Esempio · 5 anni (6 al 31/12/2027, regolare) · Primaria 1ª 2027/2028 · "
                              "ora: Infanzia Scuola dell'infanzia Esempio")
    assert text["Lingue (dichiarate)"] == "IT madrelingua/bilingue · EN base · FR nessuna esposizione"
    assert text["Cerca (dichiarato)"] == "Lingue · Ambiente internazionale · Attenzione individuale — «inglese ogni giorno»"
    assert text["Esigenze (dichiarate)"] == "post-scuola, mensa · uscita 16:30 · ingresso dal 13/09/2027 · fratelli a KITE: no"
    assert text["Dubbi"] == "2 domande, 1 da verificare: «C'è il trasporto dalla zona Esempio?»"
    assert text["Proposta"] == ("v1 comunicata il 30/09 · listino 5.400,00 € annuale · riservata −540,00 € (Fratelli) · "
                                "finale 4.860,00 € annuale · iscrizione 300,00 € · valida fino al 31/10/2026")
    assert text["Interno KITE"] == "interesse ALTO · decisione: alcune settimane · driver: lingue · ostacolo: trasporto"
    steps = [line.text for line in result.lines if line.label == "Prossimi passi" or (not line.label and "[" in line.text)]
    assert steps[0].startswith("Inviare informazioni entro 02/10 · Segreteria · «Materiale: Rette e servizi, Modulo")
    assert any("Verificare e rispondere entro 03/10 · Ionut" in line for line in steps)
    assert all(line.endswith("[Aperto]") for line in steps)
    assert text["Non ancora presentato"] == "calendario scolastico, divisa"
    kinds = {line.label: line.kind for line in result.lines}
    assert kinds["Cerca (dichiarato)"] == "declared" and kinds["Interno KITE"] == "internal"
    detail = client.get(f"/appuntamenti/{full_case['appointment']}").get_data(as_text=True)
    assert "Riepilogo" in detail and "1° incontro" in detail and "Ultimo salvataggio del colloquio" in detail


def test_each_extra_student_adds_two_lines(app, client, fake, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Fratelli")
    create_lead(client, family_id, display_name="Primo Esempio", school_year="2027/2028", grade="Primaria 2ª")
    create_lead(client, family_id, display_name="Seconda Esempio", school_year="2027/2028", grade="Infanzia")
    appointment_id = meeting(app, client, fake, family_id)
    changes = dict(FULL)
    changes.update({"s2_lang_IT": "MADRELINGUA", "s1_birth_date": "2019-05-10"})
    post(client, appointment_id, "save:a", changes)
    post(client, appointment_id, "conclude", CLOSING)
    result = summary(app, appointment_id, clock)
    assert result.limit == 14 and len(result.lines) <= 14
    assert [line.text.split(" · ")[0] for line in result.lines if line.label == "Alunno"] == ["Primo Esempio",
                                                                                             "Seconda Esempio"]
    assert sum(1 for line in result.lines if line.label == "Lingue (dichiarate)") == 2


def test_older_colloqui_do_not_show_current_student_data(app, client, fake, clock):
    family_id = create_family(client, display_name="Famiglia Esempio Storico")
    lead_id = create_lead(client, family_id, display_name="Luca Esempio", school_year="2027/2028", grade="Primaria 1ª")
    first = meeting(app, client, fake, family_id, lead_id, event_id="evt-1", start="2026-09-20T10:00:00+02:00")
    post(client, first, "conclude", {"kind": "PRIMA_VISITA", "motivations": ["LINGUE"], "outcome": "SVOLTA",
                                     "visited_at": "2026-09-20T10:00", "step1_action": "RICHIAMARE",
                                     "step1_due_on": "2026-09-25"})
    second = meeting(app, client, fake, family_id, lead_id, event_id="evt-2", start="2026-09-30T15:00:00+02:00")
    post(client, second, "conclude", {"kind": "INCONTRO_ECONOMICO", "interest": "MOLTO_ALTO", "outcome": "SVOLTA",
                                      "s1_birth_date": "2021-03-14", "step1_action": "RICHIAMARE",
                                      "step1_due_on": "2026-10-04"})
    older, newer = summary(app, first, clock), summary(app, second, clock)
    assert not older.latest and newer.latest
    assert [line.text for line in older.lines if line.label == "Alunno"] == [
        "Dati dell'alunno: vedi richiesta (valori attuali)"]
    assert not any("anni" in line.text for line in older.lines)
    assert any(line.text == "nessuna proposta comunicata in questo incontro" for line in older.lines)
    assert any("5 anni" in line.text for line in newer.lines if line.label == "Alunno")
    assert "2° incontro" in newer.lines[0].text and "1° incontro" in older.lines[0].text


def test_a_future_meeting_only_prepared_does_not_make_the_visit_older(app, client, fake, clock, db, full_case):
    """Regressione dal collaudo: un incontro futuro con la sola preparazione non è il «colloquio più recente»."""
    future = meeting(app, client, fake, full_case["family"], event_id="evt-futuro", start="2026-10-06T17:00:00+02:00",
                     title="Incontro con la Direzione (futuro)")
    post(client, future, "save:prepara", kind="INCONTRO_DIREZIONE", preparation="Portare il POF (esempio)")
    visit = summary(app, full_case["appointment"], clock)
    assert visit.latest and any("5 anni" in line.text for line in visit.lines if line.label == "Alunno")
    page = client.get(f"/famiglie/{full_case['family']}").get_data(as_text=True)
    side = page.split('id="ultimo-colloquio"')[1].split("</section>")[0]
    assert "Prima visita · Visita svolta" in side and "Direzione" not in side
    clock.set(datetime(2026, 10, 6, 15, 10, tzinfo=timezone.utc))  # l'incontro futuro è iniziato: ora è il più recente
    assert not summary(app, full_case["appointment"], clock).latest


def test_v1_report_summary_is_shown_as_before(app, client, fake, clock, db):
    family_id = create_family(client, display_name="Famiglia Esempio Resoconto V1")
    appointment_id = meeting(app, client, fake, family_id)
    client.post(f"/appuntamenti/{appointment_id}/resoconto", data={
        "visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:00", "visit_report": "Resoconto libero (esempio)",
        "next_action": "RICHIAMARE", "next_due_on": "2026-10-04", "next_new_id": str(uuid.uuid4()),
        "revision": revision(db, "Appointment", appointment_id)})
    result = summary(app, appointment_id, clock)
    assert not result.structured
    assert [(line.label, line.text) for line in result.lines][:2] == [
        ("Resoconto", "Visita svolta · mer 30/09/2026 15:00"), ("Sintesi", "Resoconto libero (esempio)")]
    assert result.lines[2].text.startswith("Richiamare entro 04/10")


def test_family_page_shows_the_last_colloquio_in_four_lines(client, full_case):
    page = client.get(f"/famiglie/{full_case['family']}").get_data(as_text=True)
    side = page.split('id="ultimo-colloquio"')[1].split("</section>")[0]
    items = re.findall(r"<li>(.*?)</li>", side, re.S)
    assert len(items) == 4
    assert "mer 30/09/2026 15:05 · Prima visita · Visita svolta" in items[0]
    assert items[1] == "Interesse alto · decisione: alcune settimane" and items[2] == "Ostacolo: trasporto"
    assert items[3].startswith("Prossimi passi: Inviare informazioni entro 02/10")
    assert page.index('id="ultimo-colloquio"') < page.index("<h2>Prossimo passo</h2>")


# --- IW09: storico ----------------------------------------------------------------------------

def test_three_meetings_keep_three_distinct_colloqui(app, client, fake, clock, db):
    family_id = create_family(client, display_name="Famiglia Esempio Tre Incontri")
    lead_id = create_lead(client, family_id, display_name="Luca Esempio", school_year="2027/2028")
    cases = (("evt-a", "2026-09-20T10:00:00+02:00", "PRIMA_VISITA", "ALTO", "TRASPORTO"),
             ("evt-b", "2026-09-25T17:00:00+02:00", "INCONTRO_ECONOMICO", "ALTO", "COSTO"),
             ("evt-c", "2026-09-30T10:00:00+02:00", "INCONTRO_DIREZIONE", "MOLTO_ALTO", "NESSUNO"))
    ids = []
    for event_id, start, kind, interest, obstacle in cases:
        appointment_id = meeting(app, client, fake, family_id, lead_id, event_id=event_id, start=start)
        post(client, appointment_id, "conclude", {"kind": kind, "interest": interest, "obstacle": obstacle,
                                                  "outcome": "SVOLTA", "visited_at": start[:16],
                                                  "step1_action": "RICHIAMARE", "step1_due_on": "2026-10-05"})
        ids.append(appointment_id)
    before = {appointment_id: db.scalar("SELECT interview FROM Appointment WHERE id = ?", (appointment_id,))
              for appointment_id in ids}
    post(client, ids[1], "save:j", obstacle="ORARI")
    for appointment_id in (ids[0], ids[2]):
        assert db.scalar("SELECT interview FROM Appointment WHERE id = ?", (appointment_id,)) == before[appointment_id]
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    table = page.split("Appuntamenti e colloqui")[1].split("</table>")[0]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)[1:]
    assert len(rows) == 3
    assert "Incontro con la Direzione" in rows[0] and "Molto alto" in rows[0] and "Nessuno evidente" in rows[0]
    assert "Incontro economico" in rows[1] and "Orari" in rows[1] and "Richiamare entro 05/10/2026" in rows[1]
    assert "Prima visita" in rows[2] and "Trasporto" in rows[2] and "Visita svolta" in rows[2]

    saved_at = json.loads(before[ids[2]])["saved_at"]
    fake.put(CAL, timed("evt-c", "2026-09-30T11:00:00+02:00", title="Titolo cambiato dalla segreteria"))
    clock.advance(hours=1)
    refresh(app, client)
    moved = db.one("SELECT * FROM Appointment WHERE id = ?", (ids[2],))
    assert moved["updated_by"] == "Calendar import" and json.loads(moved["interview"])["saved_at"] == saved_at


def test_timeline_shows_the_colloquio_title_and_detail(app, client, clock, db, full_case):
    items = [item for item in timeline_service.family_timeline(db, full_case["family"]) if item.kind == "Resoconto"]
    assert len(items) == 1 and items[0].title == "Colloquio · Prima visita: Visita svolta"
    assert items[0].detail.startswith("interesse alto · ostacolo trasporto · prossimi passi: Inviare informazioni")
    assert items[0].link == f"/appuntamenti/{full_case['appointment']}#colloquio"


def test_today_links_to_the_interview_and_shows_the_assignee(app, client, fake, clock, db):
    clock.set(datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc))
    family_id = create_family(client, display_name="Famiglia Esempio Oggi")
    lead_id = create_lead(client, family_id)
    today = meeting(app, client, fake, family_id, lead_id, event_id="evt-oggi", start="2026-10-01T16:00:00+02:00")
    past = meeting(app, client, fake, family_id, lead_id, event_id="evt-ieri", start="2026-09-30T16:00:00+02:00")
    client.post(f"/famiglie/{family_id}/follow-up/nuovo", data={"new_id": str(uuid.uuid4()), "action": "INVIARE_INFORMAZIONI",
                                                               "due_on": "2026-10-01", "assignee": "Segreteria"})
    page = client.get("/").get_data(as_text=True)
    appointments = page.split("Appuntamenti di oggi")[1].split("Da richiamare")[0]
    assert f"/appuntamenti/{today}/colloquio" in appointments and ">Colloquio</a>" in appointments
    missing = page.split("Resoconti da completare")[1].split("Pratiche senza prossimo passo")[0]
    assert f"/appuntamenti/{past}/colloquio?sezione=k" in missing and "Registra l&#39;esito" in missing
    due = page.split("Follow-up scaduti e di oggi")[1]
    assert "Segreteria" in due.split("</section>")[0]


# --- Export, eliminazione, privacy ------------------------------------------------------------

def test_export_contains_the_new_columns_and_explains_the_json(app, client, full_case):
    client.post("/dati/export")
    package = next(app.extensions["kite"].paths.exports_dir.glob("*.zip"))
    with zipfile.ZipFile(package) as archive:
        document = json.loads(archive.read("kite-admissions.json"))
        appointment_csv = archive.read("Appointment.csv").decode("utf-8").lstrip("﻿")
        readme = archive.read("LEGGIMI.txt").decode("utf-8")
    appointment = next(row for row in document["tables"]["Appointment"] if row["id"] == full_case["appointment"])
    assert json.loads(appointment["interview"])["assessment"]["interest"] == "ALTO"
    followups = document["tables"]["FollowUp"]
    assert {row["appointment_id"] for row in followups} == {full_case["appointment"]}
    assert "Segreteria" in {row["assignee"] for row in followups}
    rows = list(csv.DictReader(io.StringIO(appointment_csv), delimiter=";"))
    assert "interview" in rows[0] and "motivations" in rows[0]["interview"]
    assert "Appointment.interview" in readme and "catalog.py" in readme


def test_deletion_counts_an_interview_without_outcome(app, client, fake, db):
    family_id = create_family(client, display_name="Famiglia Esempio Elimina Colloquio")
    appointment_id = meeting(app, client, fake, family_id)
    post(client, appointment_id, "save:c", motivations=["LINGUE"])
    assert db.scalar("SELECT visit_outcome FROM Appointment WHERE id = ?", (appointment_id,)) is None
    page = client.get(f"/famiglie/{family_id}/elimina").get_data(as_text=True)
    assert '<tr><td>Resoconti</td><td class="num">1</td></tr>' in page


def test_interview_page_carries_the_privacy_help_texts_and_no_sensitive_fields(client, full_case):
    html = client.get(f"/appuntamenti/{full_case['appointment']}/colloquio").get_data(as_text=True)
    for text in ("Niente giudizi sulla scuola attuale, sugli insegnanti o su altre persone",
                 "<strong>non si scrivono, nemmeno se la famiglia li cita</strong>",
                 "Indica solo l'azione: nessuna diagnosi",
                 "non sono una valutazione linguistica né una certificazione",
                 "Allergie, intolleranze o diete non si registrano qui",
                 "Una risposta senza domanda non si salva",
                 "come se la famiglia potesse leggerla", "GDPR, art. 15",
                 "Nessun altro dato della famiglia che vi ha segnalati"):
        assert text in html, text
    names = set(re.findall(r'name="([^"]+)"', html)) | set(re.findall(r'<option value="([^"]+)"', html))
    for name in names:
        assert not any(stem in name.casefold() for stem in FORBIDDEN_STEMS), name
    assert "TUTORE" not in html and "Tutore" not in html


def test_form_values_never_reach_the_logs(app, client, fake, clock, caplog):
    family_id = create_family(client, display_name="Famiglia Esempio Log")
    lead_id = create_lead(client, family_id)
    appointment_id = meeting(app, client, fake, family_id, lead_id)
    secret = "Valore-riservato-di-prova-7351"
    with caplog.at_level(logging.DEBUG):
        post(client, appointment_id, "save:c", motivations_note=secret, local_observations=secret)
        post(client, appointment_id, "conclude", outcome="SVOLTA", step1_action="RICHIAMARE",
             step1_due_on="2026-10-04", step1_note=secret)
    assert all(secret not in record.getMessage() for record in caplog.records)


def test_closing_takes_at_most_three_actions(app, client, fake, clock, db):
    """IW14: esito (1), scelta rapida (2), «Concludi colloquio» (3); la scelta rapida precompila come app.js."""
    family_id = create_family(client, display_name="Famiglia Esempio Tre Azioni")
    lead_id = create_lead(client, family_id)
    appointment_id = meeting(app, client, fake, family_id, lead_id)
    html = client.get(f"/appuntamenti/{appointment_id}/colloquio?sezione=k").get_data(as_text=True)
    fields = read_form(html, "colloquio")
    quick = re.search(r'data-quick data-action="([A-Z_]*)" data-due="([0-9-]*)" data-note="([^"]*)"[^>]*>Richiamo<', html)
    fields.update(outcome="SVOLTA")  # azione 1
    fields.update(step1_action=quick.group(1), step1_due_on=quick.group(2), step1_note=quick.group(3))  # azione 2
    fields["action"] = "conclude"  # azione 3
    response = client.post(f"/appuntamenti/{appointment_id}/colloquio", data=fields)
    assert response.status_code == 302
    step = db.one("SELECT * FROM FollowUp")
    assert (step["action"], step["due_on"]) == ("RICHIAMARE", "2026-10-04")
    assert db.scalar("SELECT visit_outcome FROM Appointment WHERE id = ?", (appointment_id,)) == "SVOLTA"
