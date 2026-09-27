"""Contratto della sorgente Calendar: errori, evento normalizzato, recapiti proposti.

Le sorgenti (Google reale o simulata) restituiscono eventi nel formato dell'API Google
Calendar v3; la normalizzazione è una sola e vale per tutte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Protocol

from ..textutil import find_emails, find_phones, html_to_text, normalize_email
from ..timeutil import parse_rfc3339, to_iso

# Errori che rendono indisponibile l'intera sorgente: inutile interrogare gli altri eventi.
SOURCE_WIDE = frozenset({"auth", "network", "timeout", "config"})

_STATUS_KINDS = {401: "auth", 403: "forbidden", 404: "not_found", 410: "gone", 429: "server"}
_KIND_MESSAGES = {
    "auth": "Accesso Google non valido, revocato o scaduto: ricollega Google dal pannello Dati.",
    "forbidden": "Accesso negato da Google (403).",
    "not_found": "Evento o calendario non trovato su Google (404).",
    "gone": "Google segnala la risorsa come non più disponibile (410).",
    "server": "Servizio Google momentaneamente non disponibile.",
    "network": "Rete non disponibile: impossibile contattare Google.",
    "timeout": "Google non ha risposto in tempo.",
    "config": "Google Calendar non è ancora collegato.",
    "invalid": "Risposta di Google non valida.",
}


class SourceError(Exception):
    """Errore della sorgente. Nessun errore equivale alla cancellazione di un evento (SPEC §4.4)."""

    def __init__(self, kind: str, message: str | None = None, status: int | None = None):
        self.kind = kind
        self.status = status
        self.message = message or _KIND_MESSAGES.get(kind, "Errore della sorgente Calendar.")
        super().__init__(self.message)

    @classmethod
    def from_status(cls, status: int) -> "SourceError":
        kind = _STATUS_KINDS.get(status, "server" if status >= 500 else "invalid")
        message = _KIND_MESSAGES[kind]
        if kind in ("server", "invalid"):
            message = f"{message} (HTTP {status})"
        return cls(kind, message, status)

    @property
    def source_wide(self) -> bool:
        return self.kind in SOURCE_WIDE


class CalendarSource(Protocol):
    label: str

    def list_calendars(self) -> list[dict[str, Any]]: ...

    def list_events(self, calendar_id: str, time_min: datetime, time_max: datetime,
                    on_page: Callable[[int], None] | None = None) -> list[dict[str, Any]]: ...

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]: ...


@dataclass
class SourceEvent:
    """Evento Google normalizzato: date in UTC o giorni interi, recapiti con provenienza."""

    event_id: str
    status: str  # confirmed | tentative | cancelled
    title: str | None = None
    description: str | None = None
    location: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    time_zone: str | None = None
    updated: str | None = None
    html_link: str | None = None
    ical_uid: str | None = None
    recurring_event_id: str | None = None
    original_start: str | None = None
    contacts: list[dict[str, Any]] = field(default_factory=list)
    invalid_reason: str | None = None

    @property
    def valid(self) -> bool:
        return self.invalid_reason is None

    @property
    def all_day(self) -> bool:
        return self.start_date is not None

    @property
    def sort_key(self) -> str:
        return self.start_at or (self.start_date or "") + "T00"

    @property
    def local_status(self) -> str:
        return {"cancelled": "CANCELLED_SOURCE", "tentative": "TENTATIVE"}.get(self.status, "CONFIRMED")

    def source_columns(self) -> dict[str, Any]:
        """Colonne `src_*` aggiornabili dall'import (SPEC §4.3): mai i campi locali."""
        import json

        return {
            "src_title": self.title,
            "src_start_at": self.start_at,
            "src_end_at": self.end_at,
            "src_start_date": self.start_date,
            "src_end_date": self.end_date,
            "src_time_zone": self.time_zone,
            "src_location": self.location,
            "src_description": self.description,
            "src_contacts": json.dumps(self.contacts, ensure_ascii=False, sort_keys=True),
            "src_status": self.local_status,
            "src_updated_at": self.updated,
            "src_html_link": self.html_link,
            "src_ical_uid": self.ical_uid,
            "src_recurring_event_id": self.recurring_event_id,
            "src_original_start": self.original_start,
        }


def _text(value: Any, limit: int = 20000) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _parse_bound(bound: Any) -> tuple[str | None, str | None, str | None]:
    """(istante UTC, data, fuso) da un campo start/end di Google."""
    if not isinstance(bound, dict):
        return None, None, None
    zone = bound.get("timeZone") if isinstance(bound.get("timeZone"), str) else None
    if isinstance(bound.get("dateTime"), str):
        return to_iso(parse_rfc3339(bound["dateTime"])), None, zone
    if isinstance(bound.get("date"), str):
        return None, date.fromisoformat(bound["date"]).isoformat(), zone
    return None, None, zone


def extract_contacts(raw: dict[str, Any], calendar_id: str, description_text: str) -> list[dict[str, Any]]:
    """Recapiti proposti, con provenienza. Organizzatore, creatore e calendario stesso esclusi (SPEC §4.3)."""
    excluded = {calendar_id.lower()}
    for person_key in ("organizer", "creator"):
        person = raw.get(person_key)
        if isinstance(person, dict) and isinstance(person.get("email"), str):
            excluded.add(person["email"].lower())
    contacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str, normalized: str, source: str, label: str | None = None) -> None:
        key = (kind, normalized)
        if key in seen:
            return
        seen.add(key)
        item = {"type": kind, "value": value, "normalized": normalized, "source": source}
        if label:
            item["label"] = label
        contacts.append(item)

    for attendee in raw.get("attendees") or []:
        if not isinstance(attendee, dict):
            continue
        email = normalize_email(attendee.get("email"))
        if (not email or email in excluded or attendee.get("organizer") or attendee.get("self")
                or attendee.get("resource")):
            continue
        add("email", email, email, "partecipante", _text(attendee.get("displayName"), 200))
    for source, text in (("titolo", _text(raw.get("summary"))), ("descrizione", description_text),
                         ("luogo", _text(raw.get("location")))):
        for written, normalized in find_phones(text):
            add("phone", written, normalized, source)
        for email in find_emails(text):
            if email not in excluded and normalize_email(email):
                add("email", email, email, source)
    return contacts


def normalize_event(raw: dict[str, Any], calendar_id: str) -> SourceEvent:
    event_id = raw.get("id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("evento senza id")
    status = raw.get("status") if raw.get("status") in ("confirmed", "tentative", "cancelled") else "confirmed"
    event = SourceEvent(event_id=event_id, status=status)
    event.updated = _text(raw.get("updated"), 64)
    if status == "cancelled":
        # Una cancellazione può contenere soltanto l'ID: i dati precedenti restano quelli salvati.
        return event
    event.title = _text(raw.get("summary"), 500)
    event.description = _text(raw.get("description"))
    event.location = _text(raw.get("location"), 500)
    link = raw.get("htmlLink")
    if isinstance(link, str) and link.startswith(("https://www.google.com/calendar/", "https://calendar.google.com/")):
        event.html_link = link
    event.ical_uid = _text(raw.get("iCalUID"), 500)
    event.recurring_event_id = _text(raw.get("recurringEventId"), 500)
    original = raw.get("originalStartTime")
    if isinstance(original, dict):
        event.original_start = _text(original.get("dateTime") or original.get("date"), 64)
    try:
        start_at, start_date, zone = _parse_bound(raw.get("start"))
        end_at, end_date, _ = _parse_bound(raw.get("end"))
    except ValueError:
        event.invalid_reason = "Data o ora dell'evento non leggibile."
        return event
    event.time_zone = zone
    if start_at and end_at:
        if end_at < start_at:
            event.invalid_reason = "L'evento termina prima di iniziare."
        event.start_at, event.end_at = start_at, end_at
    elif start_date and end_date:
        if end_date <= start_date:
            event.invalid_reason = "Date dell'evento di un giorno intero non coerenti."
        event.start_date, event.end_date = start_date, end_date
    else:
        event.invalid_reason = "Evento privo di un orario utilizzabile."
    event.contacts = extract_contacts(raw, calendar_id, html_to_text(event.description))
    return event
