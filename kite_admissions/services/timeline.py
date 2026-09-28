"""Cronologia essenziale della famiglia, ricavata dalle registrazioni esistenti (SPEC §7).

Nessuna copia: appuntamenti, resoconti, offerte e follow-up si leggono dalle loro tabelle;
Interaction fornisce note manuali e transizioni B1. Le transizioni di un appuntamento
compaiono tramite il collegamento dell'appuntamento, anche se registrate prima di esso.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import catalog
from ..db import Database
from ..labels import euro, label
from ..timeutil import format_date


@dataclass
class TimelineItem:
    at: str  # timestamp UTC o data YYYY-MM-DD
    kind: str
    title: str
    detail: str = ""
    link: str = ""
    author: str = ""
    date_only: bool = False
    item_id: str = ""
    editable: bool = False

    @property
    def sort_key(self) -> str:
        return self.at if not self.date_only else self.at + "T23:59:59.999999Z"


def _offer_scope(row: Any) -> str:
    return f" per {row['lead_name']}" if row["lead_name"] else " per la famiglia"


def _report_item(db: Database, row: Any) -> TimelineItem:
    """Voce «Resoconto»: per un colloquio v1.1 titolo «Colloquio · tipo: esito» e dettaglio
    «interesse · ostacolo · prossimi passi»; per un resoconto V1 resta l'estratto della sintesi."""
    from . import interview as interview_service

    content = interview_service.content_of(interview_service.loads(row["interview"]))
    link = f"/appuntamenti/{row['id']}#colloquio"
    at = row["visited_at"] or row["updated_at"]
    if not content:
        return TimelineItem(at, "Resoconto", label(row["visit_outcome"]), (row["visit_report"] or "")[:240], link,
                            row["updated_by"])
    kind = catalog.label(catalog.MEETING_KINDS, content.get("kind"))
    title = f"Colloquio · {kind}: {label(row['visit_outcome'])}" if kind else f"Colloquio: {label(row['visit_outcome'])}"
    assessment = content.get("assessment") or {}
    parts = []
    if assessment.get("interest"):
        parts.append("interesse " + catalog.label(catalog.INTEREST_LEVELS, assessment["interest"]).lower())
    if assessment.get("obstacle"):
        parts.append("ostacolo " + catalog.label(catalog.OBSTACLES, assessment["obstacle"]).lower())
    steps = [f"{interview_service.step_label(step)} entro il {format_date(step['due_on'])}"
             for step in interview_service.meeting_followups(db, row["id"])]
    if steps:
        parts.append("prossimi passi: " + ", ".join(steps))
    return TimelineItem(at, "Resoconto", title, " · ".join(parts), link, row["updated_by"])


def family_timeline(db: Database, family_id: str) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    family = db.one("SELECT first_contact_on, contact_source FROM Family WHERE id = ?", (family_id,))
    if family and family["first_contact_on"]:
        source = f" ({family['contact_source']})" if family["contact_source"] else ""
        items.append(TimelineItem(family["first_contact_on"], "Primo contatto", f"Primo contatto{source}",
                                  date_only=True))

    for row in db.all("SELECT * FROM Appointment WHERE family_id = ?", (family_id,)):
        at = row["src_start_at"] or row["src_start_date"]
        state = {"CANCELLED_SOURCE": " · annullato su Calendar", "TENTATIVE": " · da confermare"}.get(row["src_status"], "")
        items.append(TimelineItem(at, "Appuntamento", (row["src_title"] or "(senza titolo)") + state,
                                  link=f"/appuntamenti/{row['id']}", author="Google Calendar",
                                  date_only=row["src_start_at"] is None))
        if row["visit_outcome"]:
            items.append(_report_item(db, row))

    for row in db.all(
        "SELECT o.*, s.display_name AS lead_name FROM Offer o LEFT JOIN StudentLead s ON s.id = o.student_lead_id "
        "WHERE o.family_id = ? AND o.status <> 'BOZZA'", (family_id,)
    ):
        amount = f"{euro(row['proposed_fee_cents'])} {label(row['periodicity'])}"
        channel = f" · canale: {row['communication_channel']}" if row["communication_channel"] else ""
        items.append(TimelineItem(row["communicated_at"], "Proposta comunicata",
                                  f"Proposta v{row['version_no']}{_offer_scope(row)}: {amount}", channel.lstrip(" ·"),
                                  f"#offerta-{row['id']}", row["communicated_by"]))
        if row["status"] == "RITIRATA":
            items.append(TimelineItem(row["withdrawn_at"], "Proposta ritirata",
                                      f"Proposta v{row['version_no']}{_offer_scope(row)} ritirata",
                                      row["withdrawal_reason"], f"#offerta-{row['id']}", row["updated_by"]))

    for row in db.all("SELECT * FROM FollowUp WHERE family_id = ? AND status <> 'APERTO'", (family_id,)):
        verb = "completato" if row["status"] == "COMPLETATO" else "annullato"
        detail = row["outcome"] or ("esito non indicato" if row["status"] == "COMPLETATO" else "")
        items.append(TimelineItem(row["updated_at"], "Follow-up",
                                  f"{label(row['action'])} {verb} il {format_date(row['closed_on'])}", detail,
                                  "#follow-up", row["updated_by"]))

    for row in db.all(
        "SELECT i.*, s.display_name AS lead_name, a.src_title AS appointment_title FROM Interaction i "
        "LEFT JOIN StudentLead s ON s.id = i.student_lead_id LEFT JOIN Appointment a ON a.id = i.appointment_id "
        "WHERE i.family_id = ? OR i.appointment_id IN (SELECT id FROM Appointment WHERE family_id = ?)",
        (family_id, family_id),
    ):
        if row["previous_state"]:
            subject = row["lead_name"] or row["appointment_title"] or ""
            title = f"{label(row['type'])}: {subject} ({label(row['previous_state'])} → {label(row['next_state'])})"
            link = f"/appuntamenti/{row['appointment_id']}" if row["appointment_id"] else "#richieste"
            items.append(TimelineItem(row["occurred_at"], "Transizione", title, row["text"], link, row["origin"]))
        else:
            subject = f" · {row['lead_name']}" if row["lead_name"] else ""
            items.append(TimelineItem(row["occurred_at"], label(row["type"]), f"{label(row['type'])}{subject}",
                                      row["text"], "", row["origin"], item_id=row["id"], editable=True))

    items.sort(key=lambda item: item.sort_key, reverse=True)
    return items
