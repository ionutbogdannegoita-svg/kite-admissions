"""Sorgenti Calendar simulate, con eventi nel formato dell'API Google v3.

Non sono una funzionalità del prodotto: servono ai test automatici e al collaudo senza
credenziali. La sorgente da file si attiva soltanto con la variabile d'ambiente
KITE_ADMISSIONS_CALENDAR_FIXTURE e l'interfaccia la segnala in ogni pagina.
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from ..timeutil import parse_rfc3339, rome_midnight
from .source import SourceError

ENV_FIXTURE = "KITE_ADMISSIONS_CALENDAR_FIXTURE"
DEFAULT_CALENDAR = {"id": "segreteria-test@example.org", "summary": "Segreteria (calendario di prova)",
                    "primary": False, "access_role": "reader"}


def _bounds(raw: dict[str, Any]) -> tuple[datetime, datetime] | None:
    start, end = raw.get("start") or {}, raw.get("end") or {}
    if "dateTime" in start:
        begin = parse_rfc3339(start["dateTime"])
        return begin, parse_rfc3339(end.get("dateTime", start["dateTime"]))
    if "date" in start:
        return (rome_midnight(date.fromisoformat(start["date"])),
                rome_midnight(date.fromisoformat(end.get("date", start["date"]))))
    return None


class FakeCalendarSource:
    """Calendario in memoria: eventi, annullamenti, errori e registro delle chiamate."""

    label = "Calendario simulato"

    def __init__(self, calendars: list[dict[str, Any]] | None = None):
        self.calendars = copy.deepcopy(calendars or [DEFAULT_CALENDAR])
        self.events: dict[str, dict[str, dict[str, Any]]] = {}
        self.minimal_cancelled: set[str] = set()
        self.calls: list[tuple[str, ...]] = []
        self.list_error: SourceError | None = None
        self.get_errors: dict[str, SourceError] = {}
        self.pages = 1

    # Preparazione dei casi -------------------------------------------------------
    def put(self, calendar_id: str, event: dict[str, Any]) -> None:
        self.events.setdefault(calendar_id, {})[event["id"]] = copy.deepcopy(event)
        self.minimal_cancelled.discard(event["id"])

    def cancel(self, calendar_id: str, event_id: str, *, minimal: bool = True, updated: str | None = None) -> None:
        event = self.events[calendar_id][event_id]
        event["status"] = "cancelled"
        if updated:
            event["updated"] = updated
        if minimal:
            self.minimal_cancelled.add(event_id)

    def delete(self, calendar_id: str, event_id: str) -> None:
        self.events[calendar_id].pop(event_id, None)

    # Contratto CalendarSource ----------------------------------------------------
    def _view(self, event: dict[str, Any]) -> dict[str, Any]:
        if event.get("status") == "cancelled" and event["id"] in self.minimal_cancelled:
            view = {"id": event["id"], "status": "cancelled"}
            if event.get("updated"):
                view["updated"] = event["updated"]
            return view
        return copy.deepcopy(event)

    def list_calendars(self) -> list[dict[str, Any]]:
        self.calls.append(("list_calendars",))
        return copy.deepcopy(self.calendars)

    def list_events(self, calendar_id: str, time_min: datetime, time_max: datetime,
                    on_page: Callable[[int], None] | None = None) -> list[dict[str, Any]]:
        self.calls.append(("list_events", calendar_id))
        if self.list_error is not None:
            raise self.list_error
        found = []
        for event in self.events.get(calendar_id, {}).values():
            bounds = _bounds(event)
            if bounds is None or (bounds[1] > time_min and bounds[0] < time_max):
                found.append(self._view(event))
        for page in range(1, self.pages + 1):
            if on_page is not None:
                on_page(page)
        return found

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        self.calls.append(("get_event", calendar_id, event_id))
        if event_id in self.get_errors:
            raise self.get_errors[event_id]
        event = self.events.get(calendar_id, {}).get(event_id)
        if event is None:
            raise SourceError.from_status(404)
        return self._view(event)


class FixtureCalendarSource(FakeCalendarSource):
    """Calendario di prova letto da un file JSON a ogni richiesta (collaudo manuale).

    Formato: {"calendars": [...], "events": {"<calendar_id>": [evento Google, ...]},
    "minimal_cancelled": ["<event_id>"], "get_errors": {"<event_id>": 404}, "list_error": 503}
    """

    label = "Calendario di prova da file (dati sintetici)"

    def __init__(self, path: str | Path):
        super().__init__()
        self.path = Path(path)

    def _reload(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.calendars = data.get("calendars") or [DEFAULT_CALENDAR]
        self.events = {cal: {event["id"]: event for event in events}
                       for cal, events in (data.get("events") or {}).items()}
        self.minimal_cancelled = set(data.get("minimal_cancelled") or [])
        self.get_errors = {event_id: SourceError.from_status(int(status))
                           for event_id, status in (data.get("get_errors") or {}).items()}
        list_error = data.get("list_error")
        self.list_error = SourceError.from_status(int(list_error)) if list_error else None

    def list_calendars(self) -> list[dict[str, Any]]:
        self._reload()
        return super().list_calendars()

    def list_events(self, *args, **kwargs):
        self._reload()
        return super().list_events(*args, **kwargs)

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        self._reload()
        return super().get_event(calendar_id, event_id)
