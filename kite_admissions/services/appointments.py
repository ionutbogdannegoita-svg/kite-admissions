"""Appuntamenti: copia degli eventi Calendar con dati locali separati (SPEC §3, §4.3, §5)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from ..db import Database
from ..timeutil import rome_today
from .common import NotFoundError, stamp

VIEWS = {
    "prossimi": "Prossimi",
    "da_collegare": "Da collegare",
    "passati": "Passati",
    "annullati": "Annullati su Calendar",
    "non_verificati": "Non verificati",
    "tutti": "Tutti",
}

SELECT = (
    "SELECT a.*, f.display_name AS family_name, f.archived_at AS family_archived_at, "
    "s.display_name AS lead_name FROM Appointment a "
    "LEFT JOIN Family f ON f.id = a.family_id LEFT JOIN StudentLead s ON s.id = a.student_lead_id"
)
START_KEY = "coalesce(a.src_start_at, a.src_start_date || 'T00:00:00.000000Z')"


def upcoming_condition() -> str:
    """In corso o futuro: fine (esclusiva per i giorni interi) non ancora passata."""
    return "(a.src_end_at >= :now OR a.src_end_date > :today)"


def list_appointments(db: Database, view: str, now: datetime) -> list[sqlite3.Row]:
    params = {"now": stamp(now), "today": rome_today(now).isoformat()}
    upcoming = upcoming_condition()
    past = "(a.src_end_at < :now OR a.src_end_date <= :today)"
    active = "a.src_status <> 'CANCELLED_SOURCE'"
    queries = {
        "prossimi": (f"{upcoming} AND {active}", "ASC"),
        "da_collegare": (f"a.family_id IS NULL AND {active}", "ASC"),
        "passati": (past, "DESC"),
        "annullati": ("a.src_status = 'CANCELLED_SOURCE'", "DESC"),
        "non_verificati": ("a.verification_state = 'NOT_VERIFIED'", "ASC"),
        "tutti": ("1 = 1", "DESC"),
    }
    where, order = queries.get(view, queries["prossimi"])
    return db.all(f"{SELECT} WHERE {where} ORDER BY {START_KEY} {order}", params)


def get_appointment(db: Database, appointment_id: str) -> sqlite3.Row:
    row = db.one(f"{SELECT} WHERE a.id = ?", (appointment_id,))
    if row is None:
        raise NotFoundError(appointment_id)
    return row


def contacts(row: Any) -> list[dict[str, Any]]:
    try:
        value = json.loads(row["src_contacts"] or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def counts(db: Database, now: datetime) -> dict[str, int]:
    params = {"now": stamp(now), "today": rome_today(now).isoformat()}
    return {
        "da_collegare": db.scalar(
            "SELECT count(*) FROM Appointment a WHERE a.family_id IS NULL AND a.src_status <> 'CANCELLED_SOURCE'"),
        "non_verificati": db.scalar("SELECT count(*) FROM Appointment a WHERE a.verification_state = 'NOT_VERIFIED'"),
        "prossimi": db.scalar(
            f"SELECT count(*) FROM Appointment a WHERE {upcoming_condition()} AND a.src_status <> 'CANCELLED_SOURCE'",
            params),
    }
