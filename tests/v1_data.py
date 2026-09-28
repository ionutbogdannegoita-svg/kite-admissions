"""Database della V1 (schema 1) con dati sintetici in tutte e sette le tabelle, per le prove di migrazione."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kite_admissions.schema import APPLICATION_ID, SCHEMA_V1, TABLE_ORDER, TABLES

TS = "2026-09-26T08:00:00.000000Z"
AUDIT = {"created_at": TS, "updated_at": TS, "created_by": "Ionut", "updated_by": "Ionut"}
CAL = "segreteria-test@example.org"

F1 = "11111111-1111-4111-8111-111111111111"
F2 = "22222222-2222-4222-8222-222222222222"
L1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
L2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
A1 = "cccccccc-cccc-4ccc-8ccc-ccccccccccc1"
A2 = "cccccccc-cccc-4ccc-8ccc-ccccccccccc2"
O1 = "dddddddd-dddd-4ddd-8ddd-ddddddddddd1"
O2 = "dddddddd-dddd-4ddd-8ddd-ddddddddddd2"
U1 = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1"
U2 = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee2"
I1 = "ffffffff-ffff-4fff-8fff-fffffffffff1"
I2 = "ffffffff-ffff-4fff-8fff-fffffffffff2"
I3 = "ffffffff-ffff-4fff-8fff-fffffffffff3"


def _insert(conn: sqlite3.Connection, table: str, **values) -> None:
    values = {**AUDIT, **values}
    conn.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                 list(values.values()))


def build(path: Path) -> None:
    """Crea il file come lo lascia la V1: schema 1, `user_version = 1`, dati in ogni tabella."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_V1
                           + f"\nPRAGMA application_id = {APPLICATION_ID};\nPRAGMA user_version = 1;\nCOMMIT;")
        conn.execute("BEGIN IMMEDIATE")
        _insert(conn, "Family", id=F1, display_name="Famiglia Esempio V1", primary_adult_name="Adulto Esempio V1",
                primary_phone="+39 0773 000 901", primary_phone_norm="+390773000901",
                contact_source="Open day", first_contact_on="2026-09-20", preliminary_notes="Note V1 (esempio)")
        _insert(conn, "Family", id=F2, display_name="Famiglia Esempio Seconda V1")
        _insert(conn, "StudentLead", id=L1, family_id=F1, display_name="Alunno Esempio V1", school_year="2027/2028",
                grade="Primaria 1ª", origin="Latina (esempio)", birth_year=2021, status="IN_CORSO")
        _insert(conn, "StudentLead", id=L2, family_id=F1, display_name="Sorella Esempio V1", school_year="2026/2027",
                status="ISCRITTO", closed_on="2026-09-25", enrollment_ref="DOMANDA-V1-1")
        _insert(conn, "Appointment", id=A1, calendar_id=CAL, google_event_id="evt-v1-1", src_title="Visita V1",
                src_start_at="2026-09-24T13:00:00.000000Z", src_end_at="2026-09-24T14:00:00.000000Z",
                src_status="CONFIRMED", verification_state="VERIFIED", last_synced_at=TS, last_checked_at=TS,
                family_id=F1, student_lead_id=L1, preparation="Preparazione V1", visit_outcome="SVOLTA",
                visited_at="2026-09-24T13:05:00.000000Z", visit_report="Resoconto V1 (esempio)",
                local_observations="Osservazioni V1", created_by="Calendar import", updated_by="Ionut")
        _insert(conn, "Appointment", id=A2, calendar_id=CAL, google_event_id="evt-v1-2", src_title="Evento V1",
                src_start_date="2026-10-10", src_end_date="2026-10-11", src_status="CANCELLED_SOURCE",
                verification_state="VERIFIED", last_synced_at=TS, last_checked_at=TS,
                created_by="Calendar import", updated_by="Calendar import")
        _insert(conn, "Offer", id=O1, family_id=F1, student_lead_id=L1, version_no=1, standard_fee_cents=540000,
                proposed_fee_cents=500000, periodicity="ANNUALE",
                services='[{"description": "Mensa", "amount_cents": 8000, "periodicity": "MENSILE", "included": false}]',
                conditions="Condizioni V1", status="COMUNICATA", communicated_at=TS, communicated_by="Ionut",
                communication_channel="Di persona")
        _insert(conn, "Offer", id=O2, family_id=F1, student_lead_id=L1, version_no=2, previous_offer_id=O1,
                proposed_fee_cents=490000, periodicity="ANNUALE", status="BOZZA")
        _insert(conn, "FollowUp", id=U1, family_id=F1, student_lead_id=L1, action="RICHIAMARE", due_on="2026-10-02")
        _insert(conn, "FollowUp", id=U2, family_id=F1, action="INVIARE_INFORMAZIONI", due_on="2026-09-27",
                status="COMPLETATO", closed_on="2026-09-26", outcome="Inviato (esempio)")
        _insert(conn, "Interaction", id=I1, family_id=F1, type="TELEFONATA", occurred_at=TS,
                text="Telefonata V1 (esempio)", origin="Ionut")
        _insert(conn, "Interaction", id=I2, family_id=F1, student_lead_id=L2, type="LEAD_ENROLLED", occurred_at=TS,
                text="Iscrizione V1", origin="Ionut", previous_state="IN_CORSO", next_state="ISCRITTO")
        _insert(conn, "Interaction", id=I3, appointment_id=A2, type="CALENDAR_CANCELLED", occurred_at=TS,
                text="Annullato su Calendar", origin="Calendar import", previous_state="CONFIRMED",
                next_state="CANCELLED_SOURCE", created_by="Calendar import", updated_by="Calendar import")
        conn.execute("INSERT INTO CalendarExclusion (calendar_id, google_event_id, reason, excluded_at) "
                     "VALUES (?, 'evt-ignorato', 'IGNORED', ?)", (CAL, TS))
        conn.execute("COMMIT")
    finally:
        conn.close()


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def snapshot(path: Path, only: dict[str, list[str]] | None = None) -> dict[str, list[dict]]:
    """Righe di ogni tabella in ordine stabile; con `only` si confrontano soltanto quelle colonne."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        result = {}
        for table in TABLES:
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {TABLE_ORDER[table]}")]
            if only is not None:
                rows = [{key: row[key] for key in only[table]} for row in rows]
            result[table] = rows
        return result
    finally:
        conn.close()


def v1_columns(path: Path) -> dict[str, list[str]]:
    conn = sqlite3.connect(path)
    try:
        return {table: columns(conn, table) for table in TABLES}
    finally:
        conn.close()
