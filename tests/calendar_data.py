"""Eventi sintetici nel formato Google Calendar v3 (nomi e recapiti fittizi)."""

from __future__ import annotations

from datetime import datetime, timedelta

CAL = "segreteria-test@example.org"
OTHER_CAL = "altro-calendario-test@example.org"


def timed(event_id: str, start: str, *, minutes: int = 60, title: str = "Visita Famiglia Esempio",
          status: str = "confirmed", description: str | None = None, updated: str = "2026-09-20T10:00:00.000Z",
          **extra) -> dict:
    begin = datetime.fromisoformat(start)
    event = {
        "id": event_id,
        "status": status,
        "summary": title,
        "start": {"dateTime": begin.isoformat(), "timeZone": "Europe/Rome"},
        "end": {"dateTime": (begin + timedelta(minutes=minutes)).isoformat(), "timeZone": "Europe/Rome"},
        "updated": updated,
        "htmlLink": f"https://www.google.com/calendar/event?eid={event_id}",
        "iCalUID": f"{event_id}@google.com",
        "organizer": {"email": CAL, "self": True},
        "creator": {"email": "segreteria.persona@example.org"},
    }
    if description is not None:
        event["description"] = description
    event.update(extra)
    return event


def all_day(event_id: str, day: str, end_day: str, *, title: str = "Open day (esempio)", **extra) -> dict:
    event = {
        "id": event_id,
        "status": "confirmed",
        "summary": title,
        "start": {"date": day},
        "end": {"date": end_day},
        "updated": "2026-09-20T10:00:00.000Z",
        "htmlLink": f"https://www.google.com/calendar/event?eid={event_id}",
        "organizer": {"email": CAL, "self": True},
    }
    event.update(extra)
    return event


def occurrence(series_id: str, start: str, **extra) -> dict:
    """Occorrenza di un evento ricorrente, come restituita con singleEvents=true."""
    begin = datetime.fromisoformat(start)
    event = timed(f"{series_id}_{begin.strftime('%Y%m%dT%H%M%S')}", start, title="Colloquio ricorrente (esempio)",
                  **extra)
    event["recurringEventId"] = series_id
    event["originalStartTime"] = {"dateTime": begin.isoformat(), "timeZone": "Europe/Rome"}
    return event
