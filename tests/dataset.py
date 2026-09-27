"""Dataset sintetico piccolo e rappresentativo, creato attraverso l'interfaccia (dati fittizi)."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from kite_admissions.db import Database
from kite_admissions.schema import TABLE_ORDER, TABLES

from .calendar_data import CAL, timed
from .helpers import create_family, create_lead, revision


def snapshot(db_path: Path) -> dict[str, list[dict]]:
    """Contenuto completo delle sette tabelle, in ordine stabile: confronto di valori e relazioni."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {TABLE_ORDER[table]}")]
                for table in TABLES}
    finally:
        conn.close()


def integrity(db_path: Path) -> tuple[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0], len(conn.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        conn.close()


def refresh(app, client):
    client.post("/appuntamenti/aggiorna")
    app.extensions["kite"].import_runner.wait(10)


def build(app, client, fake, clock) -> dict[str, str]:
    """Due famiglie, fratelli con anni diversi, appuntamenti, visita, offerte versionate, follow-up, note."""
    state = app.extensions["kite"]
    state.settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Segreteria (test)"}))
    ids: dict[str, str] = {}
    ids["rossi"] = create_family(client, display_name="Famiglia Esempio Rossi-Test", primary_adult_name="Adulto Esempio Rossi",
                                 primary_phone="+39 0773 000 801", primary_email="rossi.test@example.org",
                                 contact_source="Passaparola", first_contact_on="2026-09-20")
    ids["luca"] = create_lead(client, ids["rossi"], display_name="Luca Esempio", school_year="2027/2028", grade="Primaria 1ª")
    ids["anna"] = create_lead(client, ids["rossi"], display_name="Anna Esempio", school_year="2028/2029", grade="Infanzia")
    ids["bianchi"] = create_family(client, display_name="Famiglia Esempio Bianchi-Test", primary_phone="+39 0773 000 802")
    ids["marco"] = create_lead(client, ids["bianchi"], display_name="Marco Esempio", school_year="2027/2028")

    fake.put(CAL, timed("evt-rossi", "2026-09-30T15:00:00+02:00", title="Visita Famiglia Esempio Rossi-Test",
                        description="tel 0773 000 801"))
    fake.put(CAL, timed("evt-bianchi", "2026-10-06T10:00:00+02:00", title="Visita Famiglia Esempio Bianchi-Test"))
    fake.put(CAL, timed("evt-fornitore", "2026-10-07T12:00:00+02:00", title="Incontro fornitore (non Admissions)"))
    refresh(app, client)
    client.post("/appuntamenti/importa", data={"event_id": ["evt-rossi", "evt-bianchi"]})
    client.post("/appuntamenti/ignora", data={"event_id": "evt-fornitore"})
    db = Database(state.paths.db_path)
    try:
        ids["appt_rossi"] = db.scalar("SELECT id FROM Appointment WHERE google_event_id = 'evt-rossi'")
        ids["appt_bianchi"] = db.scalar("SELECT id FROM Appointment WHERE google_event_id = 'evt-bianchi'")
        # Evento modificato, annullato e riattivato mentre non è ancora collegato.
        fake.put(CAL, timed("evt-bianchi", "2026-10-08T10:00:00+02:00", title="Visita Famiglia Esempio Bianchi-Test"))
        clock.advance(minutes=1)
        refresh(app, client)
        fake.cancel(CAL, "evt-bianchi")
        clock.advance(minutes=1)
        refresh(app, client)
        fake.put(CAL, timed("evt-bianchi", "2026-10-08T10:00:00+02:00", title="Visita Famiglia Esempio Bianchi-Test"))
        clock.advance(minutes=1)
        refresh(app, client)

        client.post(f"/appuntamenti/{ids['appt_rossi']}/collega", data={"family_id": ids["rossi"], "lead_id": ids["luca"],
                                                                      "revision": revision(db, "Appointment", ids["appt_rossi"])})
        client.post(f"/appuntamenti/{ids['appt_rossi']}/resoconto", data={
            "visit_outcome": "SVOLTA", "visited_at": "2026-09-30T15:05", "visit_report": "Visita svolta (esempio)",
            "local_observations": "Osservazione di esempio", "revision": revision(db, "Appointment", ids["appt_rossi"])})
        client.post(f"/appuntamenti/{ids['appt_bianchi']}/collega", data={
            "family_id": ids["bianchi"], "revision": revision(db, "Appointment", ids["appt_bianchi"])})

        ids["offer_v1"] = str(uuid.uuid4())
        client.post(f"/famiglie/{ids['rossi']}/offerte/nuova", data={
            "new_id": ids["offer_v1"], "student_lead_id": ids["luca"], "standard_fee": "5.400,00",
            "proposed_fee": "1.000,00", "periodicity": "ANNUALE", "service_description_1": "Mensa",
            "service_amount_1": "80", "service_periodicity_1": "MENSILE", "service_included_1": "0"})
        client.post(f"/famiglie/{ids['rossi']}/offerte/{ids['offer_v1']}/comunica",
                    data={"revision": revision(db, "Offer", ids["offer_v1"]), "channel": "Di persona"})
        ids["offer_v2"] = str(uuid.uuid4())
        client.post(f"/famiglie/{ids['rossi']}/offerte/{ids['offer_v1']}/nuova-versione", data={"new_id": ids["offer_v2"]})
        client.post(f"/famiglie/{ids['rossi']}/offerte/{ids['offer_v2']}/modifica", data={
            "proposed_fee": "900", "periodicity": "ANNUALE", "revision": revision(db, "Offer", ids["offer_v2"])})

        ids["followup_open"] = str(uuid.uuid4())
        client.post(f"/famiglie/{ids['rossi']}/follow-up/nuovo", data={
            "new_id": ids["followup_open"], "action": "RICHIAMARE", "due_on": "2026-10-03", "student_lead_id": ids["anna"]})
        ids["followup_done"] = str(uuid.uuid4())
        client.post(f"/famiglie/{ids['bianchi']}/follow-up/nuovo", data={
            "new_id": ids["followup_done"], "action": "INVIARE_INFORMAZIONI", "due_on": "2026-10-01"})
        client.post(f"/famiglie/{ids['bianchi']}/follow-up/{ids['followup_done']}/completa",
                    data={"revision": 1, "outcome": "Brochure inviata (esempio)"})
        client.post(f"/famiglie/{ids['bianchi']}/richieste/{ids['marco']}/stato", data={
            "new_status": "ISCRITTO", "closed_on": "2026-10-01", "enrollment_ref": "DOMANDA-ESEMPIO-1",
            "revision": revision(db, "StudentLead", ids["marco"])})
        client.post(f"/famiglie/{ids['rossi']}/note", data={"new_id": str(uuid.uuid4()), "type": "TELEFONATA",
                                                          "text": "Telefonata di esempio"})
    finally:
        db.close()
    return ids
