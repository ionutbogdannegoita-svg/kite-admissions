"""Note e comunicazioni manuali in Interaction (SPEC §7, §8).

Le transizioni automatiche (B1) si scrivono solo nei servizi che le generano e restano
immutate; qui si gestiscono soltanto note, telefonate, email e rettifiche.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Mapping

from .. import AUTHOR
from ..db import Database
from ..schema import MANUAL_INTERACTIONS
from ..timeutil import local_input_to_utc
from .common import AlreadySavedError, NotFoundError, StaleWriteError, ValidationError, clean, stamp


def parse_note(form: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    kind = form.get("type") or "NOTA"
    if kind not in MANUAL_INTERACTIONS:
        raise ValidationError("Tipo di annotazione non valido.", "type")
    text = clean(form.get("text"), 4000)
    if text is None:
        raise ValidationError("Scrivi il testo della nota.", "text")
    occurred_raw = clean(form.get("occurred_at"), 20)
    occurred = now
    if occurred_raw:
        try:
            occurred = local_input_to_utc(occurred_raw)
        except ValueError:
            raise ValidationError("Data e ora non valide.", "occurred_at") from None
        if occurred > now + timedelta(minutes=5):
            raise ValidationError("Una comunicazione registrata non può essere nel futuro.", "occurred_at")
    return {
        "type": kind,
        "text": text,
        "occurred_at": stamp(occurred),
        "student_lead_id": form.get("student_lead_id") or None,
        "offer_id": form.get("offer_id") or None,
    }


def _check_refs(db: Database, family_id: str, values: Mapping[str, Any]) -> None:
    if values.get("student_lead_id") and db.one(
            "SELECT 1 FROM StudentLead WHERE id = ? AND family_id = ?", (values["student_lead_id"], family_id)) is None:
        raise ValidationError("La richiesta scelta non appartiene a questa famiglia.", "student_lead_id")
    if values.get("offer_id") and db.one(
            "SELECT 1 FROM Offer WHERE id = ? AND family_id = ?", (values["offer_id"], family_id)) is None:
        raise ValidationError("La proposta scelta non appartiene a questa famiglia.", "offer_id")


def create_note(db: Database, family_id: str, values: Mapping[str, Any], *, note_id: str, now: datetime) -> str:
    with db.transaction():
        if db.one("SELECT 1 FROM Interaction WHERE id = ?", (note_id,)):
            raise AlreadySavedError(note_id)
        if db.one("SELECT 1 FROM Family WHERE id = ?", (family_id,)) is None:
            raise NotFoundError(family_id)
        _check_refs(db, family_id, values)
        db.execute(
            "INSERT INTO Interaction (id, family_id, student_lead_id, offer_id, type, occurred_at, text, origin, "
            "created_at, updated_at, created_by, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (note_id, family_id, values.get("student_lead_id"), values.get("offer_id"), values["type"],
             values["occurred_at"], values["text"], AUTHOR, stamp(now), stamp(now), AUTHOR, AUTHOR),
        )
    return note_id


def get_note(db: Database, family_id: str, note_id: str) -> sqlite3.Row:
    row = db.one("SELECT * FROM Interaction WHERE id = ? AND family_id = ?", (note_id, family_id))
    if row is None or row["type"] not in MANUAL_INTERACTIONS:
        raise NotFoundError(note_id)
    return row


def update_note(db: Database, family_id: str, note_id: str, values: Mapping[str, Any], *, revision: int,
                now: datetime) -> None:
    with db.transaction():
        current = get_note(db, family_id, note_id)
        if current["revision"] != revision:
            raise StaleWriteError()
        _check_refs(db, family_id, values)
        cursor = db.execute(
            "UPDATE Interaction SET type = ?, text = ?, occurred_at = ?, student_lead_id = ?, offer_id = ?, "
            "updated_at = ?, updated_by = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
            (values["type"], values["text"], values["occurred_at"], values.get("student_lead_id"),
             values.get("offer_id"), stamp(now), AUTHOR, note_id, revision),
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()


def delete_note(db: Database, family_id: str, note_id: str) -> None:
    """Solo annotazioni manuali: le transizioni registrate non si eliminano singolarmente."""
    with db.transaction():
        get_note(db, family_id, note_id)
        db.execute("DELETE FROM Interaction WHERE id = ?", (note_id,))
