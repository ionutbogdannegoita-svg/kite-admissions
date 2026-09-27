"""QA end-to-end della V1 sui dati sintetici: i passi 1-19 del collaudo finale, in ordine."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
import zipfile

import pytest

from kite_admissions.db import Database
from kite_admissions.gcal.fake import FakeCalendarSource

from . import dataset
from .calendar_data import CAL, all_day, timed
from .helpers import location_id


def rev(app, table, row_id):
    database = Database(app.extensions["kite"].paths.db_path)
    try:
        return database.scalar(f"SELECT revision FROM {table} WHERE id = ?", (row_id,))
    finally:
        database.close()


def test_final_qa_flow(make_app, home, clock):
    fake = FakeCalendarSource()
    # 1-2. Database vuoto e avvio.
    assert not (home / "data" / "admissions.sqlite3").exists()
    app = make_app(calendar_source=fake)
    client = app.test_client()
    state = app.extensions["kite"]
    assert client.get("/health").get_json()["status"] == "ok"
    assert all(not rows for rows in dataset.snapshot(state.paths.db_path).values())

    # 3. Crea una famiglia.
    family_id = location_id(client.post("/famiglie/nuova", data={
        "new_id": str(uuid.uuid4()), "display_name": "Famiglia Esempio Collaudo",
        "primary_adult_name": "Genitore Esempio Collaudo", "primary_phone": "+39 0773 000 901",
        "contact_source": "Open day", "first_contact_on": "2026-09-25"}), "/famiglie")

    # 4. Crea almeno una richiesta (due fratelli, anni diversi).
    for name, year in (("Primo Figlio Esempio", "2027/2028"), ("Secondo Figlio Esempio", "2028/2029")):
        assert client.post(f"/famiglie/{family_id}/richieste/nuova", data={
            "new_id": str(uuid.uuid4()), "display_name": name, "school_year": year}).status_code == 302

    # 5. Importa appuntamenti da un calendario misto (simulato).
    fake.put(CAL, timed("evt-collaudo", "2026-09-30T16:00:00+02:00", title="Visita Famiglia Esempio Collaudo",
                        description="Telefono 0773 000 901"))
    fake.put(CAL, timed("evt-altro", "2026-10-02T09:00:00+02:00", title="Riunione interna (non Admissions)"))
    fake.put(CAL, all_day("evt-openday", "2026-10-10", "2026-10-11", title="Open day (esempio)"))
    client.get("/dati/google/calendari")
    client.post("/dati/google/calendario", data={"calendar_id": CAL})
    client.post("/appuntamenti/aggiorna")
    state.import_runner.wait(10)
    preview = client.get("/appuntamenti/aggiornamento").get_data(as_text=True)
    assert "Riunione interna" in preview and re.search(r'type="checkbox"[^>]*checked', preview) is None
    client.post("/appuntamenti/importa", data={"event_id": ["evt-collaudo", "evt-openday"]})
    db = Database(state.paths.db_path)
    appointment_id = db.scalar("SELECT id FROM Appointment WHERE google_event_id = 'evt-collaudo'")
    assert db.scalar("SELECT count(*) FROM Appointment") == 2
    db.close()

    # 6. Collega appuntamento e famiglia (suggerimento per telefono).
    page = client.get(f"/appuntamenti/{appointment_id}").get_data(as_text=True)
    assert "Famiglia Esempio Collaudo" in page and "stesso telefono" in page
    client.post(f"/appuntamenti/{appointment_id}/collega", data={"family_id": family_id,
                                                                "revision": rev(app, "Appointment", appointment_id)})

    # 7. Registra la visita con un prossimo passo.
    client.post(f"/appuntamenti/{appointment_id}/resoconto", data={
        "visit_outcome": "SVOLTA", "visited_at": "2026-09-30T16:00", "visit_report": "Visita completa (esempio)",
        "next_action": "RICHIAMARE", "next_due_on": "2026-10-01", "next_note": "Dopo aver valutato la proposta",
        "next_new_id": str(uuid.uuid4()), "revision": rev(app, "Appointment", appointment_id)})

    # 8-9. Crea la proposta e segnala come comunicata.
    offer_v1 = str(uuid.uuid4())
    client.post(f"/famiglie/{family_id}/offerte/nuova", data={
        "new_id": offer_v1, "standard_fee": "5.400,00", "proposed_fee": "4.900,00", "periodicity": "ANNUALE",
        "conditions": "Proposta familiare di esempio"})
    client.post(f"/famiglie/{family_id}/offerte/{offer_v1}/comunica", data={"revision": rev(app, "Offer", offer_v1),
                                                                            "channel": "Di persona"})
    db = Database(state.paths.db_path)
    communicated = db.one("SELECT * FROM Offer WHERE id = ?", (offer_v1,))
    assert communicated["status"] == "COMUNICATA" and communicated["communicated_at"]

    # 10. Non modificabile dopo la comunicazione (backend e database).
    client.post(f"/famiglie/{family_id}/offerte/{offer_v1}/modifica", data={
        "proposed_fee": "1,00", "periodicity": "ANNUALE", "revision": communicated["revision"]})
    assert db.scalar("SELECT proposed_fee_cents FROM Offer WHERE id = ?", (offer_v1,)) == 490000
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE Offer SET proposed_fee_cents = 1 WHERE id = ?", (offer_v1,))
    db.close()

    # 11. Nuova versione: la v1 resta la corrente finché la v2 è bozza.
    offer_v2 = str(uuid.uuid4())
    client.post(f"/famiglie/{family_id}/offerte/{offer_v1}/nuova-versione", data={"new_id": offer_v2})
    client.post(f"/famiglie/{family_id}/offerte/{offer_v2}/modifica", data={
        "standard_fee": "5.400,00", "proposed_fee": "4.500,00", "periodicity": "ANNUALE",
        "revision": rev(app, "Offer", offer_v2)})
    detail = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert re.search(r"Proposta corrente: <strong>v1</strong> · 4\.900,00 €", detail)

    # 12. Crea un follow-up.
    client.post(f"/famiglie/{family_id}/follow-up/nuovo", data={
        "new_id": str(uuid.uuid4()), "action": "INVIARE_INFORMAZIONI", "due_on": "2026-10-01",
        "note": "Inviare il regolamento mensa (esempio)"})

    # 13. Vista Oggi.
    today = client.get("/").get_data(as_text=True)
    assert "Da richiamare" in today and "Famiglia Esempio Collaudo" in today.split("Da richiamare")[1]
    assert "Inviare il regolamento mensa" in today
    assert "Open day (esempio)" in today.split("Eventi da collegare")[1]
    assert "Ultimo tentativo" in today

    # 14. Cronologia.
    assert "Proposta v1 per la famiglia: 4.900,00 € annuale" in detail
    assert "Visita svolta" in detail and "Primo contatto (Open day)" in detail

    # 15. Crea backup.
    client.post("/dati/backup")
    backup = next(state.paths.manual_backups_dir.glob("*.zip"))
    expected = dataset.snapshot(state.paths.db_path)

    # 16. Modifica i dati.
    client.post(f"/famiglie/{family_id}/modifica", data={"display_name": "Famiglia Modificata Dopo il Backup",
                                                        "revision": rev(app, "Family", family_id)})
    client.post(f"/famiglie/{family_id}/offerte/{offer_v2}/comunica", data={"revision": rev(app, "Offer", offer_v2)})
    assert dataset.snapshot(state.paths.db_path) != expected

    # 17-18. Ripristino e verifica dei valori attesi.
    client.post(f"/dati/ripristino/manuale/{backup.name}", data={"confirm_text": "RIPRISTINA"})
    assert dataset.snapshot(state.paths.db_path) == expected
    assert dataset.integrity(state.paths.db_path) == ("ok", 0)
    client.post("/dati/verifica-ripristino", data={"checked": "1"})
    detail = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Famiglia Esempio Collaudo" in detail and "Famiglia Modificata" not in detail

    # 19. Export.
    client.post("/dati/export")
    package = next(state.paths.exports_dir.glob("*.zip"))
    with zipfile.ZipFile(package) as archive:
        document = json.loads(archive.read("kite-admissions.json"))
    assert document["tables"]["Family"][0]["display_name"] == "Famiglia Esempio Collaudo"
    assert {row["version_no"]: row["status"] for row in document["tables"]["Offer"]} == {1: "COMUNICATA", 2: "BOZZA"}
    assert all(call[0] in ("list_calendars", "list_events", "get_event") for call in fake.calls)
