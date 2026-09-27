"""Follow-up: azione, scadenza, nota; APERTO / COMPLETATO / ANNULLATO (SPEC §3, §7).

Responsabile implicito: Ionut. «Scaduto» deriva dalla data locale di Roma. Completare un
richiamo non inventa una telefonata riuscita: l'esito è quello scritto da Ionut.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any, Mapping

from .. import AUTHOR
from ..db import Database
from ..schema import FOLLOWUP_ACTIONS
from ..timeutil import rome_today
from .common import AlreadySavedError, NotFoundError, StaleWriteError, ValidationError, clean, stamp
from .families import parse_date_field


def parse_followup(form: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    action = form.get(prefix + "action") or ""
    if action not in FOLLOWUP_ACTIONS:
        raise ValidationError("Scegli l'azione del follow-up.", prefix + "action")
    due_on = parse_date_field(form, prefix + "due_on", "Scadenza")
    if due_on is None:
        raise ValidationError("La scadenza del follow-up è obbligatoria.", prefix + "due_on")
    note = clean(form.get(prefix + "note"), 2000)
    if action == "ALTRO" and not note:
        raise ValidationError("Per «Altro» descrivi l'azione nella nota.", prefix + "note")
    return {"action": action, "due_on": due_on, "note": note,
            "student_lead_id": form.get(prefix + "student_lead_id") or None}


def _check_lead(db: Database, family_id: str, lead_id: str | None) -> None:
    if lead_id and db.one("SELECT 1 FROM StudentLead WHERE id = ? AND family_id = ?", (lead_id, family_id)) is None:
        raise ValidationError("La richiesta scelta non appartiene a questa famiglia.", "student_lead_id")


def insert_followup(db: Database, family_id: str, values: Mapping[str, Any], *, followup_id: str,
                    now: datetime) -> str:
    _check_lead(db, family_id, values.get("student_lead_id"))
    db.execute(
        "INSERT INTO FollowUp (id, family_id, student_lead_id, action, due_on, note, status, created_at, updated_at, "
        "created_by, updated_by) VALUES (?, ?, ?, ?, ?, ?, 'APERTO', ?, ?, ?, ?)",
        (followup_id, family_id, values.get("student_lead_id"), values["action"], values["due_on"], values["note"],
         stamp(now), stamp(now), AUTHOR, AUTHOR),
    )
    return followup_id


def create_followup(db: Database, family_id: str, values: Mapping[str, Any], *, followup_id: str,
                    now: datetime) -> str:
    with db.transaction():
        if db.one("SELECT 1 FROM FollowUp WHERE id = ?", (followup_id,)):
            raise AlreadySavedError(followup_id)
        if db.one("SELECT 1 FROM Family WHERE id = ?", (family_id,)) is None:
            raise NotFoundError(family_id)
        insert_followup(db, family_id, values, followup_id=followup_id, now=now)
    return followup_id


def get_followup(db: Database, followup_id: str, family_id: str) -> sqlite3.Row:
    row = db.one("SELECT * FROM FollowUp WHERE id = ? AND family_id = ?", (followup_id, family_id))
    if row is None:
        raise NotFoundError(followup_id)
    return row


def update_followup(db: Database, followup_id: str, family_id: str, values: Mapping[str, Any], *, revision: int,
                    now: datetime) -> None:
    with db.transaction():
        current = get_followup(db, followup_id, family_id)
        if current["status"] != "APERTO":
            raise ValidationError("Il follow-up è già chiuso.")
        if current["revision"] != revision:
            raise StaleWriteError()
        _check_lead(db, family_id, values.get("student_lead_id"))
        cursor = db.execute(
            "UPDATE FollowUp SET action = ?, due_on = ?, note = ?, student_lead_id = ?, updated_at = ?, updated_by = ?, "
            "revision = revision + 1 WHERE id = ? AND revision = ?",
            (values["action"], values["due_on"], values["note"], values.get("student_lead_id"), stamp(now), AUTHOR,
             followup_id, revision),
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()


def close_followup(db: Database, followup_id: str, family_id: str, *, status: str, outcome: str | None,
                   revision: int, now: datetime) -> bool:
    """Completa o annulla. Idempotente se già chiuso nello stesso modo."""
    if status not in ("COMPLETATO", "ANNULLATO"):
        raise ValidationError("Chiusura non valida.")
    with db.transaction():
        current = get_followup(db, followup_id, family_id)
        if current["status"] == status:
            return False
        if current["status"] != "APERTO":
            raise ValidationError("Il follow-up è già chiuso.")
        if current["revision"] != revision:
            raise StaleWriteError()
        db.execute(
            "UPDATE FollowUp SET status = ?, closed_on = ?, outcome = ?, updated_at = ?, updated_by = ?, "
            "revision = revision + 1 WHERE id = ?",
            (status, rome_today(now).isoformat(), outcome, stamp(now), AUTHOR, followup_id),
        )
    return True


def family_followups(db: Database, family_id: str) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    rows = db.all(
        "SELECT f.*, s.display_name AS lead_name FROM FollowUp f LEFT JOIN StudentLead s ON s.id = f.student_lead_id "
        "WHERE f.family_id = ? ORDER BY f.due_on, f.created_at", (family_id,))
    open_rows = [row for row in rows if row["status"] == "APERTO"]
    closed = sorted((row for row in rows if row["status"] != "APERTO"), key=lambda row: row["updated_at"], reverse=True)
    return open_rows, closed


def is_overdue(row: Mapping[str, Any], today: date) -> bool:
    return row["status"] == "APERTO" and date.fromisoformat(row["due_on"]) < today
