"""Adapter Google Calendar API v3 in sola lettura.

Ogni richiesta passa da `_get`: l'adapter non contiene chiamate di creazione, modifica o
cancellazione (AC02). Gli scope richiesti sono soltanto di lettura (SPEC §4.1).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable
from urllib.parse import quote

from ..timeutil import rfc3339
from .source import SourceError

API_BASE = "https://www.googleapis.com/calendar/v3"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = (
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
)
DEFAULT_TIMEOUT = (10, 30)  # connessione, lettura (secondi)
MAX_PAGES = 400

SUCCESS_MESSAGE = (
    "Autorizzazione completata. Puoi chiudere questa scheda e tornare a KITE Admissions."
)


class GoogleCalendarSource:
    label = "Google Calendar"

    def __init__(self, session: Any, *, timeout: tuple[int, int] = DEFAULT_TIMEOUT, page_size: int = 250,
                 on_refresh: Callable[[], None] | None = None):
        self._session = session
        self._timeout = timeout
        self._page_size = page_size
        self._on_refresh = on_refresh

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        import requests
        from google.auth import exceptions as auth_exceptions

        try:
            response = self._session.get(API_BASE + path, params=params, timeout=self._timeout)
        except auth_exceptions.RefreshError as exc:
            raise SourceError("auth") from exc
        except auth_exceptions.TransportError as exc:
            raise SourceError("network") from exc
        except requests.Timeout as exc:
            raise SourceError("timeout") from exc
        except requests.RequestException as exc:
            raise SourceError("network") from exc
        if self._on_refresh is not None:
            self._on_refresh()
        if response.status_code != 200:
            raise SourceError.from_status(response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceError("invalid") from exc
        if not isinstance(data, dict):
            raise SourceError("invalid")
        return data

    def _paginate(self, path: str, params: dict[str, str], on_page: Callable[[int], None] | None = None) -> list[dict]:
        items: list[dict] = []
        token: str | None = None
        for page in range(1, MAX_PAGES + 1):
            query = dict(params)
            if token:
                query["pageToken"] = token
            data = self._get(path, query)
            page_items = data.get("items") or []
            items.extend(item for item in page_items if isinstance(item, dict))
            if on_page is not None:
                on_page(page)
            token = data.get("nextPageToken")
            if not token:
                return items
        raise SourceError("invalid", "Troppe pagine nella risposta di Google: restringi la finestra.")

    def list_calendars(self) -> list[dict[str, Any]]:
        items = self._paginate("/users/me/calendarList", {"minAccessRole": "reader", "maxResults": "250"})
        return [
            {
                "id": item.get("id"),
                "summary": item.get("summaryOverride") or item.get("summary") or item.get("id"),
                "primary": bool(item.get("primary")),
                "access_role": item.get("accessRole"),
            }
            for item in items
            if isinstance(item.get("id"), str)
        ]

    def list_events(self, calendar_id: str, time_min: datetime, time_max: datetime,
                    on_page: Callable[[int], None] | None = None) -> list[dict[str, Any]]:
        params = {
            "timeMin": rfc3339(time_min),
            "timeMax": rfc3339(time_max),
            "singleEvents": "true",  # singole occorrenze delle ricorrenze
            "showDeleted": "true",  # restituisce anche gli annullati (status=cancelled)
            "maxResults": str(self._page_size),
        }
        return self._paginate(f"/calendars/{quote(calendar_id, safe='')}/events", params, on_page)

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        return self._get(f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}")


def validate_client_config(raw: bytes) -> dict[str, Any]:
    """Il file scaricato da Google Cloud per un client OAuth di tipo «App desktop»."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("Il file non è un JSON valido.") from None
    if not isinstance(data, dict):
        raise ValueError("Il file non è un JSON valido.")
    if "web" in data and "installed" not in data:
        raise ValueError("Serve un client OAuth di tipo «App desktop», non «Applicazione web».")
    installed = data.get("installed")
    required = ("client_id", "client_secret", "auth_uri", "token_uri")
    if not isinstance(installed, dict) or any(not installed.get(key) for key in required):
        raise ValueError("Il file non contiene un client OAuth «App desktop» completo.")
    return {"installed": {key: installed[key] for key in installed}}


def run_installed_app_flow(client_config: dict[str, Any], scopes: tuple[str, ...] = SCOPES,
                           timeout_seconds: int = 300) -> str:
    """OAuth per applicazione desktop con il browser di sistema e redirect su loopback.

    Restituisce le credenziali autorizzate in JSON. Nessuna password viene vista o salvata.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(client_config, scopes=list(scopes))
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, open_browser=True, authorization_prompt_message="",
            success_message=SUCCESS_MESSAGE, timeout_seconds=timeout_seconds, prompt="consent",
        )
    except AttributeError as exc:  # nessuna risposta entro il tempo: last_request_uri è None
        raise RuntimeError("Autorizzazione non completata entro il tempo previsto.") from exc
    granted = set(getattr(credentials, "granted_scopes", None) or credentials.scopes or [])
    if SCOPES[0] not in granted:
        raise RuntimeError("Il permesso di lettura degli eventi del calendario non è stato concesso.")
    return credentials.to_json()


def build_session(authorized_json: str) -> tuple[Any, Any]:
    """Sessione HTTP autorizzata (google-auth + requests) e credenziali."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials

    info = json.loads(authorized_json)
    credentials = Credentials.from_authorized_user_info(info, scopes=list(SCOPES))
    return AuthorizedSession(credentials), credentials


def revoke(authorized_json: str, timeout: int = 10) -> bool:
    """Revoca il token presso Google (tentativo): non modifica alcun calendario."""
    import requests

    try:
        info = json.loads(authorized_json)
        token = info.get("refresh_token") or info.get("token")
        if not token:
            return False
        response = requests.post(REVOKE_URL, params={"token": token}, timeout=timeout,
                                 headers={"content-type": "application/x-www-form-urlencoded"})
        return response.status_code == 200
    except (ValueError, requests.RequestException):
        return False
