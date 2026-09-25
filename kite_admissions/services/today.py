"""Vista Oggi e prossimo passo delle pratiche (SPEC §3, §7, AC07).

Prossimo passo: follow-up aperto pertinente, prossimo appuntamento non annullato e verificato,
oppure data di riesame. Un'azione o un incontro della sola famiglia copre tutte le sue richieste
aperte; uno riferito a un figlio copre soltanto quella richiesta. Una famiglia senza alunni
identificati è evidenziata se non ha prossimo passo.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..db import Database
from ..labels import label
from ..timeutil import format_date, format_datetime, rome_midnight, rome_today
from . import appointments as appointment_service
from .common import stamp

__all__ = ["NextStep", "TodayView", "build", "next_steps"]

OPEN_LEAD_STATUSES = ("IN_CORSO", "IN_PAUSA")


@dataclass
class NextStep:
    family_id: str
    missing: bool
    label: str = ""
    when: str = ""
    uncovered_leads: list[str] = field(default_factory=list)
    closed: bool = False


def next_steps(db: Database, now: datetime, family_ids: list[str] | None = None) -> dict[str, NextStep]:
    params: dict[str, Any] = {"now": stamp(now), "today": rome_today(now).isoformat()}
    if family_ids is None:
        families = [row["id"] for row in db.all("SELECT id FROM Family WHERE archived_at IS NULL")]
    else:
        families = family_ids
    result: dict[str, NextStep] = {}
    if not families:
        return result
    marks = ", ".join("?" for _ in families)
    leads: dict[str, list[sqlite3.Row]] = {family_id: [] for family_id in families}
    for row in db.all(f"SELECT * FROM StudentLead WHERE family_id IN ({marks})", families):
        leads[row["family_id"]].append(row)
    steps: dict[str, list[tuple[str, str | None, str]]] = {family_id: [] for family_id in families}
    for row in db.all(
        f"SELECT family_id, student_lead_id, action, due_on FROM FollowUp WHERE status = 'APERTO' "
        f"AND family_id IN ({marks})", families):
        steps[row["family_id"]].append((row["due_on"] + "T00", row["student_lead_id"],
                                        f"{label(row['action'])} entro il {format_date(row['due_on'])}"))
    upcoming = appointment_service.upcoming_condition()
    for row in db.all(
        f"SELECT a.family_id, a.student_lead_id, a.src_start_at, a.src_start_date FROM Appointment a "
        f"WHERE a.family_id IN ({', '.join(':f' + str(i) for i in range(len(families)))}) "
        f"AND a.src_status <> 'CANCELLED_SOURCE' AND a.verification_state = 'VERIFIED' AND {upcoming}",
        {**params, **{f"f{i}": family_id for i, family_id in enumerate(families)}},
    ):
        when = row["src_start_at"] or row["src_start_date"] + "T00"
        text = (f"Appuntamento {format_datetime(row['src_start_at'])}" if row["src_start_at"]
                else f"Appuntamento {format_date(row['src_start_date'])}")
        steps[row["family_id"]].append((when, row["student_lead_id"], text))

    for family_id in families:
        family_steps = steps[family_id]
        family_leads = leads[family_id]
        open_leads = [lead for lead in family_leads if lead["status"] in OPEN_LEAD_STATUSES]
        reviews = [(lead["review_on"] + "T00", lead["id"], f"Riesame {lead['display_name']} il {format_date(lead['review_on'])}")
                   for lead in open_leads if lead["status"] == "IN_PAUSA" and lead["review_on"]]
        candidates = sorted(family_steps + reviews, key=lambda item: item[0])
        family_level = any(lead_id is None for _, lead_id, _ in family_steps)
        uncovered = []
        for lead in open_leads:
            covered = family_level or any(lead_id == lead["id"] for _, lead_id, _ in family_steps + reviews)
            if not covered:
                uncovered.append(lead["display_name"])
        if family_leads and not open_leads:
            result[family_id] = NextStep(family_id, missing=False, label="Richieste chiuse", closed=True)
            continue
        if not family_leads:
            missing = not family_steps
        else:
            missing = bool(uncovered)
        first = candidates[0] if candidates else None
        result[family_id] = NextStep(family_id, missing=missing, label=first[2] if first else "",
                                     when=first[0] if first else "", uncovered_leads=uncovered)
    return result


@dataclass
class TodayView:
    today: date
    today_appointments: list[sqlite3.Row]
    upcoming: list[sqlite3.Row]
    to_link: list[sqlite3.Row]
    reports_missing: list[sqlite3.Row]
    callbacks: list[sqlite3.Row]
    due_other: list[sqlite3.Row]
    reviews: list[sqlite3.Row]
    without_step: list[dict[str, Any]]
    not_verified: list[sqlite3.Row]


_FOLLOWUP_SELECT = (
    "SELECT f.*, fa.display_name AS family_name, fa.primary_phone, fa.secondary_phone, fa.primary_adult_name, "
    "s.display_name AS lead_name FROM FollowUp f JOIN Family fa ON fa.id = f.family_id "
    "LEFT JOIN StudentLead s ON s.id = f.student_lead_id "
    "WHERE f.status = 'APERTO' AND fa.archived_at IS NULL AND f.due_on <= ?"
)


def build(db: Database, now: datetime, upcoming_days: int = 7) -> TodayView:
    today = rome_today(now)
    start, end = rome_midnight(today), rome_midnight(today + timedelta(days=1))
    horizon = rome_midnight(today + timedelta(days=upcoming_days + 1))
    select = appointment_service.SELECT
    order = appointment_service.START_KEY
    active = "a.src_status <> 'CANCELLED_SOURCE'"
    today_appointments = db.all(
        f"{select} WHERE {active} AND ((a.src_start_at >= ? AND a.src_start_at < ?) "
        f"OR (a.src_start_date <= ? AND a.src_end_date > ?)) ORDER BY {order}",
        (stamp(start), stamp(end), today.isoformat(), today.isoformat()))
    upcoming = db.all(
        f"{select} WHERE {active} AND ((a.src_start_at >= ? AND a.src_start_at < ?) "
        f"OR (a.src_start_date > ? AND a.src_start_date < ?)) ORDER BY {order}",
        (stamp(end), stamp(horizon), today.isoformat(), (today + timedelta(days=upcoming_days + 1)).isoformat()))
    to_link = db.all(f"{select} WHERE {active} AND a.family_id IS NULL ORDER BY {order}")
    past = db.all(f"{select} WHERE {active} AND a.visit_outcome IS NULL ORDER BY {order} DESC")
    reports_missing = [row for row in past if appointment_service.needs_report(row, now)]
    due = db.all(_FOLLOWUP_SELECT + " ORDER BY f.due_on, fa.display_name", (today.isoformat(),))
    callbacks = [row for row in due if row["action"] == "RICHIAMARE"]
    due_other = [row for row in due if row["action"] != "RICHIAMARE"]
    reviews = db.all(
        "SELECT s.*, fa.display_name AS family_name FROM StudentLead s JOIN Family fa ON fa.id = s.family_id "
        "WHERE s.status = 'IN_PAUSA' AND s.review_on <= ? AND fa.archived_at IS NULL ORDER BY s.review_on",
        (today.isoformat(),))
    steps = next_steps(db, now)
    names = {row["id"]: row for row in db.all("SELECT id, display_name, primary_phone FROM Family WHERE archived_at IS NULL")}
    without_step = sorted(
        ({"family_id": family_id, "display_name": names[family_id]["display_name"],
          "phone": names[family_id]["primary_phone"], "uncovered": step.uncovered_leads}
         for family_id, step in steps.items() if step.missing and family_id in names),
        key=lambda item: item["display_name"].casefold())
    not_verified = db.all(f"{select} WHERE a.verification_state = 'NOT_VERIFIED' AND {active} ORDER BY {order}")
    return TodayView(today, today_appointments, upcoming, to_link, reports_missing, callbacks, due_other, reviews,
                     without_step, not_verified)
