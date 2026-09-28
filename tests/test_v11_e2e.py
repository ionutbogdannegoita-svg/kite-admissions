"""v1.1 — collaudo end-to-end su dati sintetici: migrazione V1, colloquio completo, economia, chiusura,
riepilogo e storico, «Cambia collegamento», backup V1 → ripristino → migrazione, export (IW01–IW14)."""

from __future__ import annotations

import json
import re
import uuid
import zipfile
from datetime import datetime, timezone

from kite_admissions import db as dbmod
from kite_admissions.db import Database
from kite_admissions.gcal.fake import FakeCalendarSource
from kite_admissions.paths import Paths
from kite_admissions.schema import TABLES
from kite_admissions.services import backups
from kite_admissions.settings import SettingsStore

from . import v1_data
from .calendar_data import CAL, timed
from .forms import read_form, with_changes
from .helpers import location_id


def rev(app, table, row_id):
    database = Database(app.extensions["kite"].paths.db_path)
    try:
        return database.scalar(f"SELECT revision FROM {table} WHERE id = ?", (row_id,))
    finally:
        database.close()


def query(app, sql, params=()):
    database = Database(app.extensions["kite"].paths.db_path)
    try:
        return [dict(row) for row in database.all(sql, params)]
    finally:
        database.close()


def page_fields(client, appointment_id, section=""):
    html = client.get(f"/appuntamenti/{appointment_id}/colloquio" + (f"?sezione={section}" if section else "")
                      ).get_data(as_text=True)
    return html, read_form(html, "colloquio")


def send(client, appointment_id, fields, action, changes=None, **more):
    data = with_changes(fields, changes, **more)
    data["action"] = action
    return client.post(f"/appuntamenti/{appointment_id}/colloquio", data=data)


def test_interview_workflow_end_to_end(make_app, clock):
    fake = FakeCalendarSource()
    app = make_app(calendar_source=fake)
    client = app.test_client()
    state = app.extensions["kite"]
    clock.set(datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc))

    # Famiglia, due figli, un follow-up già aperto prima della visita.
    family = location_id(client.post("/famiglie/nuova", data={
        "new_id": str(uuid.uuid4()), "display_name": "Famiglia Esempio E2E", "primary_adult_name": "Adulto Esempio",
        "primary_phone": "+39 0773 000 991", "first_contact_on": "2026-09-20",
        "preliminary_notes": "Preferiscono incontri dopo le 17 (esempio)"}), "/famiglie")
    luca, anna = str(uuid.uuid4()), str(uuid.uuid4())
    for lead_id, name, year, grade in ((luca, "Luca Esempio", "2027/2028", "Primaria 1ª"),
                                       (anna, "Anna Esempio", "2028/2029", "Infanzia")):
        client.post(f"/famiglie/{family}/richieste/nuova", data={"new_id": lead_id, "display_name": name,
                                                                "school_year": year, "grade": grade})
    old_step = str(uuid.uuid4())
    client.post(f"/famiglie/{family}/follow-up/nuovo", data={"new_id": old_step, "action": "FISSARE_VISITA",
                                                            "due_on": "2026-09-29"})

    # Import dell'evento e collegamento.
    fake.put(CAL, timed("evt-e2e", "2026-09-30T15:00:00+02:00", title="Visita Famiglia Esempio E2E"))
    client.get("/dati/google/calendari")
    client.post("/dati/google/calendario", data={"calendar_id": CAL})
    client.post("/appuntamenti/aggiorna")
    state.import_runner.wait(10)
    client.post("/appuntamenti/importa", data={"event_id": ["evt-e2e"]})
    appointment = query(app, "SELECT id FROM Appointment")[0]["id"]
    client.post(f"/appuntamenti/{appointment}/collega", data={"family_id": family, "lead_id": luca,
                                                             "revision": rev(app, "Appointment", appointment)})

    # Prepara incontro (il giorno prima).
    html, fields = page_fields(client, appointment)
    assert re.search(r'id="sez-prepara"\s+open', html) and "Da chiedere o confermare" in html
    assert send(client, appointment, fields, "save:prepara", kind="PRIMA_VISITA",
                preparation="Mostrare il laboratorio di inglese (esempio)").status_code == 302

    # Durante l'incontro: sezioni A–J con salvataggi intermedi.
    clock.set(datetime(2026, 9, 30, 13, 20, tzinfo=timezone.utc))
    _, fields = page_fields(client, appointment, "a")
    assert send(client, appointment, fields, "save:a", attendees=["MADRE", "PADRE", "ALUNNO"],
                s1_birth_date="2021-03-14", s1_current_school="Infanzia Esempio", s1_current_grade="Infanzia",
                contact_source="Altra famiglia KITE").status_code == 302
    _, fields = page_fields(client, appointment, "c")
    assert send(client, appointment, fields, "save:e", motivations=["LINGUE", "AMBIENTE_INTERNAZIONALE"],
                s1_lang_IT="MADRELINGUA", s1_lang_EN="BASE", needs_services=["MENSA"],
                needs_schedule="uscita 16:30", q_1="C'è il trasporto dalla zona Esempio?", verify_1="1",
                presented=["PROGETTO_EDUCATIVO", "COSTI"]).status_code == 302

    # Economia: nuova proposta dal colloquio (salva e vai → modulo dell'offerta → ritorno).
    _, fields = page_fields(client, appointment, "i")
    location = send(client, appointment, fields, f"go:offer_new:{luca}").headers["Location"]
    offer_page = client.get(location).get_data(as_text=True)
    assert f'name="next" value="/appuntamenti/{appointment}/colloquio?sezione=i"' in offer_page
    offer = str(uuid.uuid4())
    back = client.post(f"/famiglie/{family}/offerte/nuova", data={
        "new_id": offer, "student_lead_id": luca, "standard_fee": "5.400", "reduction_reason_1": "PROMOZIONE",
        "reduction_amount_1": "540", "authorized_by": "Direzione", "proposed_fee": "4.860", "enrollment_fee": "300",
        "periodicity": "ANNUALE", "next": f"/appuntamenti/{appointment}/colloquio?sezione=i"})
    assert back.headers["Location"].endswith(f"/appuntamenti/{appointment}/colloquio?sezione=i")
    _, fields = page_fields(client, appointment, "i")
    first = send(client, appointment, fields, f"offer_communicate:{offer}")
    second = send(client, appointment, fields, f"offer_communicate:{offer}")  # doppio clic
    assert first.status_code == second.status_code == 302
    communicated = query(app, "SELECT * FROM Offer WHERE id = ?", (offer,))[0]
    assert communicated["status"] == "COMUNICATA" and communicated["authorized_by"] == "Direzione"

    # J a famiglia uscita, poi K: chiusura con materiale, verifica e follow-up vecchio completato.
    clock.set(datetime(2026, 9, 30, 14, 10, tzinfo=timezone.utc))
    _, fields = page_fields(client, appointment, "k")
    response = send(client, appointment, fields, "conclude", {f"complete_{old_step}": "1"},
                    interest="ALTO", timing="ALCUNE_SETTIMANE", driver="LINGUE", obstacle="TRASPORTO",
                    local_observations="Interessati al percorso bilingue (esempio)",
                    outcome="SVOLTA", visited_at="2026-09-30T15:05", step1_action="INVIARE_INFORMAZIONI",
                    step1_due_on="2026-10-02", step1_assignee="Segreteria", materials=["LISTINO", "MODULO_ISCRIZIONE"],
                    verify_step="1", verify_due_on="2026-10-03")
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/appuntamenti/{appointment}#colloquio")
    steps = query(app, "SELECT * FROM FollowUp WHERE appointment_id = ? ORDER BY due_on", (appointment,))
    assert [(s["action"], s["assignee"]) for s in steps] == [("INVIARE_INFORMAZIONI", "Segreteria"), ("ALTRO", "Ionut")]
    assert query(app, "SELECT status FROM FollowUp WHERE id = ?", (old_step,))[0]["status"] == "COMPLETATO"
    assert query(app, "SELECT count(*) AS n FROM Interaction")[0]["n"] == 0  # nessuna riga per il colloquio

    # Riepilogo → storico → cronologia → Oggi.
    detail = client.get(f"/appuntamenti/{appointment}").get_data(as_text=True)
    summary = detail.split('class="summary"')[1].split("</dl>")[0]
    assert summary.count("<dt") <= 12 and "Prima visita" in summary and "interesse ALTO" in summary
    assert "v1 comunicata il 30/09" in summary and "Materiale: Rette e servizi" in summary
    family_page = client.get(f"/famiglie/{family}").get_data(as_text=True)
    assert "Appuntamenti e colloqui" in family_page and "Ultimo colloquio" in family_page
    assert "Colloquio · Prima visita: Visita svolta" in family_page and "Nato/a il 14/03/2021" in family_page
    clock.set(datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc))
    today = client.get("/").get_data(as_text=True)
    assert "Segreteria" in today.split("Follow-up scaduti e di oggi")[1]

    # Backup, poi «Cambia collegamento» (sposta il colloquio, stacca i passi) e ripristino.
    client.post("/dati/backup")
    backup = next(state.paths.manual_backups_dir.glob("*.zip"))
    other = location_id(client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "confirm_distinct": "1",
                                                             "display_name": "Famiglia Esempio E2E Giusta"}), "/famiglie")
    warning = client.get(f"/appuntamenti/{appointment}/cambia?famiglia={other}").get_data(as_text=True)
    assert "insieme al colloquio" in warning and "si staccano dal colloquio" in warning
    client.post(f"/appuntamenti/{appointment}/cambia", data={"family_id": other,
                                                            "revision": rev(app, "Appointment", appointment)})
    assert query(app, "SELECT count(*) AS n FROM FollowUp WHERE appointment_id IS NOT NULL")[0]["n"] == 0
    client.post(f"/dati/ripristino/manuale/{backup.name}", data={"confirm_text": "RIPRISTINA"})
    client.post("/dati/verifica-ripristino", data={"checked": "1"})
    restored = query(app, "SELECT family_id, interview FROM Appointment WHERE id = ?", (appointment,))[0]
    assert restored["family_id"] == family and json.loads(restored["interview"])["assessment"]["interest"] == "ALTO"
    assert query(app, "SELECT count(*) AS n FROM FollowUp WHERE appointment_id = ?", (appointment,))[0]["n"] == 2

    # Export completo.
    client.post("/dati/export")
    package = next(state.paths.exports_dir.glob("*.zip"))
    with zipfile.ZipFile(package) as archive:
        document = json.loads(archive.read("kite-admissions.json"))
    assert document["schema_version"] == 2 and set(document["tables"]) == set(TABLES)
    assert all(call[0] in ("list_calendars", "list_events", "get_event") for call in fake.calls)


def test_v1_database_and_backup_work_in_v11(home, make_app, clock):
    """Database V1 migrato all'avvio; backup V1 ripristinato e migrato; colloquio usabile sui dati V1."""
    paths = Paths.resolve(home)
    paths.ensure()
    v1_data.build(paths.db_path)
    backup = backups.create_backup(paths, SettingsStore(paths.settings_path).load(), backups.KIND_MANUAL,
                                   now=datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc))
    assert backups.read_manifest(backup.path)["schema_version"] == 1
    fake = FakeCalendarSource()
    app = make_app(calendar_source=fake)
    client = app.test_client()
    assert dbmod.inspect(paths.db_path).version == 2

    # Il resoconto V1 resta leggibile, nel riepilogo e nel colloquio (fase «corretta»).
    detail = client.get(f"/appuntamenti/{v1_data.A1}").get_data(as_text=True)
    assert "Resoconto V1 (esempio)" in detail and "Visita svolta" in detail
    html, fields = page_fields(client, v1_data.A1, "k")
    assert fields["a_phase"] == "done" and fields["visit_report"] == "Resoconto V1 (esempio)"
    assert fields["local_observations"] == "Osservazioni V1" and fields["preparation"] == "Preparazione V1"
    assert send(client, v1_data.A1, fields, "save:c", motivations=["LINGUE"]).status_code == 302
    after = query(app, "SELECT * FROM Appointment WHERE id = ?", (v1_data.A1,))[0]
    assert (after["visit_report"], after["local_observations"], after["visit_outcome"]) == (
        "Resoconto V1 (esempio)", "Osservazioni V1", "SVOLTA")  # i campi V1 non si perdono
    assert json.loads(after["interview"])["motivations"] == ["LINGUE"]

    # Ripristino del backup V1: torna lo stato V1, migrato allo schema 2.
    client.post(f"/dati/ripristino/manuale/{backup.name}", data={"confirm_text": "RIPRISTINA"})
    assert dbmod.inspect(paths.db_path).version == 2
    client.post("/dati/verifica-ripristino", data={"checked": "1"})
    assert query(app, "SELECT interview FROM Appointment WHERE id = ?", (v1_data.A1,))[0]["interview"] == "{}"
    _, fields = page_fields(client, v1_data.A1)
    assert send(client, v1_data.A1, fields, "save:j", interest="MEDIO").status_code == 302
    for table in TABLES:
        assert query(app, f"SELECT count(*) AS n FROM {table}")[0]["n"] >= 1
