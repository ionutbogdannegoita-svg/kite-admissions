"""Richieste alunno (StudentLead): una riga per figlio/anno, quattro stati (SPEC §7, §8).

Ogni variazione di stato inserisce una sola Interaction nella stessa transazione (B1).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from .. import AUTHOR
from ..db import Database, fold
from ..timeutil import format_date, rome_today
from .common import (
    AlreadySavedError,
    NotFoundError,
    StaleWriteError,
    ValidationError,
    clean,
    new_id,
    stamp,
)
from .families import parse_date_field

LEAD_FIELDS = ("display_name", "school_year", "grade", "origin", "birth_year", "notes")
STATUSES = ("IN_CORSO", "IN_PAUSA", "ISCRITTO", "NON_PROSEGUE")
OPEN_STATUSES = ("IN_CORSO", "IN_PAUSA")
CLOSED_STATUSES = ("ISCRITTO", "NON_PROSEGUE")
GRADE_SUGGESTIONS = (
    "Nido", "Infanzia",
    "Primaria 1ª", "Primaria 2ª", "Primaria 3ª", "Primaria 4ª", "Primaria 5ª",
    "Secondaria I grado 1ª", "Secondaria I grado 2ª", "Secondaria I grado 3ª",
    "Secondaria II grado",
)


class SimilarLeadWarning(Exception):
    """Richiesta simile già presente nella stessa famiglia e nello stesso anno."""

    def __init__(self, leads: list[sqlite3.Row]):
        super().__init__("richiesta simile")
        self.leads = leads


def school_year_of(day: date) -> str:
    start = day.year if day.month >= 9 else day.year - 1
    return f"{start}/{start + 1}"


def school_year_options(today: date) -> list[str]:
    start = int(school_year_of(today)[:4])
    return [f"{year}/{year + 1}" for year in range(start - 1, start + 4)]


def parse_lead(form: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    name = clean(form.get(prefix + "display_name"), 200)
    if name is None:
        raise ValidationError("Il nome o un riferimento provvisorio del bambino è obbligatorio.",
                              prefix + "display_name")
    school_year = clean(form.get(prefix + "school_year"), 9)
    if school_year is not None:
        try:
            first, second = (int(part) for part in school_year.split("/"))
        except ValueError:
            raise ValidationError("Anno scolastico nel formato 2026/2027.", prefix + "school_year") from None
        if second != first + 1:
            raise ValidationError("Anno scolastico nel formato 2026/2027.", prefix + "school_year")
    birth_raw = clean(form.get(prefix + "birth_year"), 4)
    birth_year = None
    if birth_raw is not None:
        if not birth_raw.isdigit() or not 1990 <= int(birth_raw) <= 2100:
            raise ValidationError("Anno di nascita non valido.", prefix + "birth_year")
        birth_year = int(birth_raw)
    return {
        "display_name": name,
        "school_year": school_year,
        "grade": clean(form.get(prefix + "grade"), 80),
        "origin": clean(form.get(prefix + "origin"), 200),
        "birth_year": birth_year,
        "notes": clean(form.get(prefix + "notes"), 4000),
    }


def get_lead(db: Database, lead_id: str, family_id: str | None = None) -> sqlite3.Row:
    row = db.one("SELECT * FROM StudentLead WHERE id = ?", (lead_id,))
    if row is None or (family_id is not None and row["family_id"] != family_id):
        raise NotFoundError(lead_id)
    return row


def family_leads(db: Database, family_id: str) -> list[sqlite3.Row]:
    return db.all(
        "SELECT * FROM StudentLead WHERE family_id = ? ORDER BY school_year, fold(display_name), created_at",
        (family_id,),
    )


def similar_leads(db: Database, family_id: str, values: Mapping[str, Any], exclude_id: str | None = None) -> list[sqlite3.Row]:
    """Stessa famiglia, nome simile e stesso anno: segnalate, mai fuse (SPEC §8)."""
    key = fold(values["display_name"])
    found = []
    for row in family_leads(db, family_id):
        if row["id"] == exclude_id or row["school_year"] != values.get("school_year"):
            continue
        other = fold(row["display_name"])
        if other == key or (min(len(other), len(key)) >= 3 and (other in key or key in other)):
            found.append(row)
    return found


def insert_lead(db: Database, family_id: str, values: Mapping[str, Any], *, now: datetime,
                lead_id: str | None = None) -> str:
    lead_id = lead_id or new_id()
    columns = ["id", "family_id", *LEAD_FIELDS, "status", "created_at", "updated_at", "created_by", "updated_by"]
    params = [lead_id, family_id, *(values.get(name) for name in LEAD_FIELDS), "IN_CORSO",
              stamp(now), stamp(now), AUTHOR, AUTHOR]
    db.execute(f"INSERT INTO StudentLead ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", params)
    return lead_id


def create_lead(db: Database, family_id: str, values: Mapping[str, Any], *, now: datetime, lead_id: str,
                confirm_similar: bool = False) -> str:
    with db.transaction():
        if db.one("SELECT 1 FROM StudentLead WHERE id = ?", (lead_id,)):
            raise AlreadySavedError(lead_id)
        if db.one("SELECT 1 FROM Family WHERE id = ?", (family_id,)) is None:
            raise NotFoundError(family_id)
        if not confirm_similar:
            similar = similar_leads(db, family_id, values)
            if similar:
                raise SimilarLeadWarning(similar)
        insert_lead(db, family_id, values, now=now, lead_id=lead_id)
    return lead_id


def update_lead(db: Database, lead_id: str, family_id: str, values: Mapping[str, Any], *, revision: int,
                now: datetime, review_on: str | None = None) -> None:
    """Modifica ordinaria dei campi: nessuna Interaction (SPEC §7)."""
    with db.transaction():
        current = get_lead(db, lead_id, family_id)
        if current["revision"] != revision:
            raise StaleWriteError()
        updates = dict(values)
        if current["status"] == "IN_PAUSA":
            if review_on is None:
                raise ValidationError("Una richiesta in pausa richiede la data di riesame.", "review_on")
            updates["review_on"] = review_on
        assignments = ", ".join(f"{column} = ?" for column in updates)
        cursor = db.execute(
            f"UPDATE StudentLead SET {assignments}, updated_at = ?, updated_by = ?, revision = revision + 1 "
            "WHERE id = ? AND revision = ?",
            list(updates.values()) + [stamp(now), AUTHOR, lead_id, revision],
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()


def transition_type(previous: str, new: str) -> str:
    if new == "ISCRITTO":
        return "LEAD_ENROLLED"
    if new == "NON_PROSEGUE":
        return "LEAD_NOT_CONTINUING"
    if previous in CLOSED_STATUSES:
        return "LEAD_REOPENED"
    return "LEAD_STATUS_CHANGED"


@dataclass
class StatusChange:
    new_status: str
    review_on: str | None = None
    closed_on: str | None = None
    enrollment_ref: str | None = None
    closure_reason: str | None = None
    note: str | None = None


def parse_status_change(form: Mapping[str, Any]) -> StatusChange:
    status = form.get("new_status", "")
    if status not in STATUSES:
        raise ValidationError("Scegli il nuovo stato della richiesta.", "new_status")
    return StatusChange(
        new_status=status,
        review_on=parse_date_field(form, "review_on", "Data di riesame"),
        closed_on=parse_date_field(form, "closed_on", "Data"),
        enrollment_ref=clean(form.get("enrollment_ref"), 200),
        closure_reason=clean(form.get("closure_reason"), 500),
        note=clean(form.get("note"), 2000),
    )


def _transition_text(previous: str, change: StatusChange) -> str:
    parts: list[str] = []
    new = change.new_status
    if new == "ISCRITTO":
        parts.append(f"Iscrizione confermata il {format_date(change.closed_on)} (riferimento: {change.enrollment_ref}).")
    elif new == "NON_PROSEGUE":
        parts.append(f"Non prosegue dal {format_date(change.closed_on)}.")
        if change.closure_reason:
            parts.append(f"Motivo: {change.closure_reason}.")
    elif new == "IN_PAUSA":
        parts.append(f"In pausa, riesame il {format_date(change.review_on)}.")
    elif previous in CLOSED_STATUSES:
        parts.append("Richiesta riaperta.")
    else:
        parts.append("Richiesta di nuovo in corso.")
    if change.note:
        parts.append(f"Nota: {change.note}")
    return " ".join(parts)


def change_status(db: Database, lead_id: str, family_id: str, change: StatusChange, *, revision: int,
                  now: datetime) -> str | None:
    """Cambia stato e registra la transizione nella stessa transazione.

    Restituisce l'id della Interaction, oppure None se lo stato non cambia (nessuna riga).
    """
    today = rome_today(now)
    with db.transaction():
        current = get_lead(db, lead_id, family_id)
        previous = current["status"]
        if change.new_status == previous:
            return None
        if current["revision"] != revision:
            raise StaleWriteError()
        new = change.new_status
        fields: dict[str, Any] = {"review_on": None, "closed_on": None, "enrollment_ref": None, "closure_reason": None}
        if new == "IN_PAUSA":
            if not change.review_on:
                raise ValidationError("Per mettere in pausa serve la data di riesame.", "review_on")
            if date.fromisoformat(change.review_on) < today:
                raise ValidationError("La data di riesame non può essere nel passato.", "review_on")
            fields["review_on"] = change.review_on
        elif new in CLOSED_STATUSES:
            if not change.closed_on:
                raise ValidationError("Indica la data.", "closed_on")
            if date.fromisoformat(change.closed_on) > today:
                raise ValidationError("La data non può essere nel futuro.", "closed_on")
            fields["closed_on"] = change.closed_on
            if new == "ISCRITTO":
                if not change.enrollment_ref:
                    raise ValidationError("L'iscrizione richiede un riferimento amministrativo effettivo.",
                                          "enrollment_ref")
                fields["enrollment_ref"] = change.enrollment_ref
            else:
                fields["closure_reason"] = change.closure_reason
        if previous in CLOSED_STATUSES and not change.note:
            raise ValidationError("Per riaprire o rettificare una richiesta chiusa serve una breve nota.", "note")
        assignments = ", ".join(f"{column} = ?" for column in fields)
        cursor = db.execute(
            f"UPDATE StudentLead SET status = ?, {assignments}, updated_at = ?, updated_by = ?, "
            "revision = revision + 1 WHERE id = ? AND revision = ?",
            [new, *fields.values(), stamp(now), AUTHOR, lead_id, revision],
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()
        interaction_id = new_id()
        db.execute(
            "INSERT INTO Interaction (id, family_id, student_lead_id, type, occurred_at, text, origin, "
            "previous_state, next_state, created_at, updated_at, created_by, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (interaction_id, family_id, lead_id, transition_type(previous, new), stamp(now),
             _transition_text(previous, change), AUTHOR, previous, new, stamp(now), stamp(now), AUTHOR, AUTHOR),
        )
    return interaction_id


def copy_for_new_year(lead: Mapping[str, Any]) -> dict[str, Any]:
    """Dati essenziali per una nuova richiesta in un altro anno: la pratica conclusa non si sovrascrive."""
    next_year = None
    if lead["school_year"]:
        first = int(lead["school_year"][:4]) + 1
        next_year = f"{first}/{first + 1}"
    return {
        "display_name": lead["display_name"],
        "school_year": next_year,
        "grade": None,
        "origin": lead["origin"],
        "birth_year": lead["birth_year"],
        "notes": None,
    }
