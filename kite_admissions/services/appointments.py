"""Appuntamenti: copia degli eventi Calendar con dati locali separati (SPEC §3, §4.3, §5).

I dati Google (`src_*`) si aggiornano solo dall'import; qui si gestiscono soltanto i campi
locali: collegamento a famiglia/figlio, preparazione, esito e resoconto della visita.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Mapping

from .. import AUTHOR
from ..db import Database
from ..timeutil import local_input_to_utc, rome_date_of, rome_today
from . import families as family_service
from . import leads as lead_service
from .common import (
    AlreadySavedError,
    NotFoundError,
    StaleWriteError,
    ValidationError,
    clean,
    stamp,
)

OUTCOMES = ("SVOLTA", "NON_PRESENTATA", "ANNULLATA")
_TITLE_NOISE = re.compile(
    r"^\s*(visita|open\s*day|colloquio|appuntamento|incontro|primo\s+contatto|presentazione)\b[\s:–\-|,.]*",
    re.IGNORECASE,
)

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


def suggest_label(title: str | None) -> str:
    """Etichetta proposta dal titolo dell'evento: un suggerimento da verificare, mai un dato certo."""
    if not title:
        return ""
    text = _TITLE_NOISE.sub("", title).strip(" -–:|,.")
    return text or title.strip()


def suggestions(db: Database, appointment: Mapping[str, Any]) -> list[family_service.DuplicateMatch]:
    """Famiglie che corrispondono ai recapiti dell'evento o al titolo (SPEC §5)."""
    found = contacts(appointment)
    phones = [item["normalized"] for item in found if item.get("type") == "phone" and item.get("normalized")]
    emails = [item["normalized"] for item in found if item.get("type") == "email" and item.get("normalized")]
    label = suggest_label(appointment["src_title"])
    return family_service.find_duplicates(db, {"display_name": label}, phones=phones, emails=emails,
                                          check_name=bool(label))


def _check_revision(current: Mapping[str, Any], revision: int) -> None:
    if current["revision"] != revision:
        raise StaleWriteError()


def _update_local(db: Database, appointment_id: str, values: dict[str, Any], revision: int, now: datetime) -> None:
    assignments = ", ".join(f"{column} = ?" for column in values)
    cursor = db.execute(
        f"UPDATE Appointment SET {assignments}, updated_at = ?, updated_by = ?, revision = revision + 1 "
        "WHERE id = ? AND revision = ?",
        [*values.values(), stamp(now), AUTHOR, appointment_id, revision],
    )
    if cursor.rowcount != 1:
        raise StaleWriteError()


def _checked_lead(db: Database, family_id: str, lead_id: str | None) -> str | None:
    if not lead_id:
        return None
    try:
        lead_service.get_lead(db, lead_id, family_id)
    except NotFoundError:
        raise ValidationError("La richiesta scelta non appartiene a questa famiglia.", "lead_id") from None
    return lead_id


def link_family(db: Database, appointment_id: str, family_id: str, lead_id: str | None, *, revision: int,
                now: datetime) -> None:
    """Collega un evento non ancora collegato a una famiglia esistente (ed eventualmente a un figlio)."""
    with db.transaction():
        current = get_appointment(db, appointment_id)
        if current["family_id"] == family_id and current["student_lead_id"] == (lead_id or None):
            return  # doppio invio: già collegato così
        _check_revision(current, revision)
        if current["family_id"] is not None:
            raise ValidationError("L'appuntamento è già collegato: usa «Cambia collegamento».")
        family_service.get_family(db, family_id)
        _update_local(db, appointment_id, {"family_id": family_id,
                                           "student_lead_id": _checked_lead(db, family_id, lead_id)},
                      revision, now)


def set_lead(db: Database, appointment_id: str, lead_id: str | None, *, revision: int, now: datetime) -> None:
    """Riferisce l'incontro a un figlio della famiglia collegata, o alla famiglia intera."""
    with db.transaction():
        current = get_appointment(db, appointment_id)
        _check_revision(current, revision)
        if current["family_id"] is None:
            raise ValidationError("Collega prima una famiglia.")
        _update_local(db, appointment_id, {"student_lead_id": _checked_lead(db, current["family_id"], lead_id)},
                      revision, now)


def change_link(db: Database, appointment_id: str, new_family_id: str, *, revision: int, now: datetime) -> None:
    """Sposta l'appuntamento (con il suo resoconto) su un'altra famiglia.

    Offerte e follow-up restano dove sono: non vengono trasferiti automaticamente (SPEC §5).
    """
    with db.transaction():
        current = get_appointment(db, appointment_id)
        _check_revision(current, revision)
        if current["family_id"] == new_family_id:
            raise ValidationError("L'appuntamento è già collegato a questa famiglia.")
        family_service.get_family(db, new_family_id)
        _update_local(db, appointment_id, {"family_id": new_family_id, "student_lead_id": None}, revision, now)


def has_report(appointment: Mapping[str, Any]) -> bool:
    return bool(appointment["visit_outcome"] or appointment["visit_report"] or appointment["local_observations"])


def create_family_from_event(
    db: Database,
    appointment_id: str,
    family_values: Mapping[str, Any],
    first_lead: Mapping[str, Any] | None,
    *,
    family_id: str,
    revision: int,
    now: datetime,
    confirm_distinct: bool = False,
) -> str:
    """Famiglia, eventuale richiesta e collegamento all'evento in una sola transazione (SPEC §5)."""
    with db.transaction():
        if db.one("SELECT 1 FROM Family WHERE id = ?", (family_id,)):
            raise AlreadySavedError(family_id)
        current = get_appointment(db, appointment_id)
        _check_revision(current, revision)
        if current["family_id"] is not None:
            raise ValidationError("L'appuntamento è già collegato a una famiglia.")
        family_service.create_family(db, family_values, now=now, family_id=family_id,
                                     confirm_distinct=confirm_distinct, first_lead=first_lead)
        lead_id = None
        if first_lead:
            lead_id = db.scalar("SELECT id FROM StudentLead WHERE family_id = ?", (family_id,))
        _update_local(db, appointment_id, {"family_id": family_id, "student_lead_id": lead_id}, revision, now)
    return family_id


def remove_imported(db: Database, appointment_id: str, now: datetime) -> None:
    """Toglie un evento importato per errore e lo esclude come «Ignorato» (revocabile).

    Ammesso solo se l'appuntamento non ha collegamenti, dati locali o cronologia.
    """
    with db.transaction():
        current = get_appointment(db, appointment_id)
        if current["family_id"] is not None or current["preparation"] or has_report(current):
            raise ValidationError("L'appuntamento ha già dati locali: non si può togliere.")
        if db.scalar("SELECT count(*) FROM Interaction WHERE appointment_id = ?", (appointment_id,)):
            raise ValidationError("L'appuntamento ha già una cronologia: non si può togliere.")
        db.execute("DELETE FROM Appointment WHERE id = ?", (appointment_id,))
        db.execute(
            "INSERT INTO CalendarExclusion (calendar_id, google_event_id, reason, excluded_at) VALUES (?, ?, 'IGNORED', ?) "
            "ON CONFLICT (calendar_id, google_event_id) DO NOTHING",
            (current["calendar_id"], current["google_event_id"], stamp(now)),
        )


def save_preparation(db: Database, appointment_id: str, preparation: str | None, *, revision: int,
                     now: datetime) -> None:
    with db.transaction():
        current = get_appointment(db, appointment_id)
        _check_revision(current, revision)
        _update_local(db, appointment_id, {"preparation": preparation}, revision, now)


def parse_report(form: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    outcome = form.get("visit_outcome") or None
    if outcome is not None and outcome not in OUTCOMES:
        raise ValidationError("Esito della visita non valido.", "visit_outcome")
    visited_raw = clean(form.get("visited_at"), 20)
    visited_at = None
    if visited_raw:
        try:
            moment = local_input_to_utc(visited_raw)
        except ValueError:
            raise ValidationError("Data e ora della visita non valide.", "visited_at") from None
        if moment > now + timedelta(minutes=5):
            raise ValidationError("La data della visita non può essere nel futuro.", "visited_at")
        visited_at = stamp(moment)
    if outcome == "SVOLTA" and visited_at is None:
        raise ValidationError("Per una visita svolta indica data e ora effettive.", "visited_at")
    return {
        "visit_outcome": outcome,
        "visited_at": visited_at,
        "visit_report": clean(form.get("visit_report"), 8000),
        "local_observations": clean(form.get("local_observations"), 4000),
    }


def save_report(db: Database, appointment_id: str, values: Mapping[str, Any], *, revision: int,
                now: datetime) -> None:
    """Resoconto della visita: fatto registrato da Ionut, mai dedotto dal calendario (SPEC §4.3)."""
    with db.transaction():
        current = get_appointment(db, appointment_id)
        _check_revision(current, revision)
        _update_local(db, appointment_id, dict(values), revision, now)


def date_discrepancy(appointment: Mapping[str, Any]) -> bool:
    """Visita registrata in un giorno diverso da quello attuale su Calendar (SPEC §4.3)."""
    if not appointment["visited_at"]:
        return False
    visited_day = rome_date_of(appointment["visited_at"]).isoformat()
    calendar_day = appointment["src_start_date"] or (
        rome_date_of(appointment["src_start_at"]).isoformat() if appointment["src_start_at"] else None)
    return calendar_day is not None and visited_day != calendar_day


def needs_report(appointment: Mapping[str, Any], now: datetime) -> bool:
    """Appuntamento passato, non annullato, senza esito: «Resoconto da completare» (SPEC §7)."""
    if appointment["src_status"] == "CANCELLED_SOURCE" or appointment["visit_outcome"]:
        return False
    if appointment["src_end_at"]:
        return appointment["src_end_at"] < stamp(now)
    return bool(appointment["src_end_date"]) and appointment["src_end_date"] <= rome_today(now).isoformat()


def family_appointments(db: Database, family_id: str) -> list[sqlite3.Row]:
    return db.all(f"{SELECT} WHERE a.family_id = ? ORDER BY {START_KEY} DESC", (family_id,))


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
