"""Collegamento Google: client OAuth desktop, autorizzazione in lettura, scelta sorgente.

Le credenziali stanno in `<dati>/google/` cifrate con DPAPI: fuori da backup ed export.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from ..paths import Paths
from . import google as google_api
from .source import CalendarSource, SourceError

CLIENT_FILE = "client-desktop.bin"
AUTHORIZED_FILE = "autorizzazione.bin"


def _write_atomic(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


class GoogleConnection:
    def __init__(self, paths: Paths, *, store: Any = None,
                 flow_runner: Callable[[dict[str, Any]], str] | None = None,
                 session_factory: Callable[[str], tuple[Any, Any]] | None = None,
                 revoker: Callable[[str], bool] | None = None):
        self.folder = paths.google_dir
        self._store = store
        self._flow_runner = flow_runner or google_api.run_installed_app_flow
        self._session_factory = session_factory or google_api.build_session
        self._revoker = revoker or google_api.revoke
        self._lock = threading.Lock()

    @property
    def store(self):
        if self._store is None:
            from .protected import WindowsProtectedStore

            self._store = WindowsProtectedStore()
        return self._store

    @property
    def client_path(self) -> Path:
        return self.folder / CLIENT_FILE

    @property
    def authorized_path(self) -> Path:
        return self.folder / AUTHORIZED_FILE

    def _read(self, path: Path) -> str | None:
        try:
            blob = path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            return self.store.unprotect(blob).decode("utf-8")
        except Exception:  # noqa: BLE001 - file di un altro utente/PC o danneggiato
            return None

    def _write(self, path: Path, text: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, self.store.protect(text.encode("utf-8")))

    # Client OAuth -----------------------------------------------------------------
    def save_client_config(self, raw: bytes) -> None:
        config = google_api.validate_client_config(raw)
        self._write(self.client_path, json.dumps(config))

    def client_config(self) -> dict[str, Any] | None:
        text = self._read(self.client_path)
        return json.loads(text) if text else None

    def client_summary(self) -> dict[str, str] | None:
        config = self.client_config()
        if not config:
            return None
        installed = config["installed"]
        client_id = installed.get("client_id", "")
        return {"client_id": client_id[:12] + "…" if len(client_id) > 12 else client_id,
                "project_id": installed.get("project_id", "")}

    # Autorizzazione ---------------------------------------------------------------
    def is_connected(self) -> bool:
        return self._read(self.authorized_path) is not None

    def connect(self) -> None:
        config = self.client_config()
        if not config:
            raise SourceError("config", "Carica prima il file del client OAuth «App desktop».")
        if not self._lock.acquire(blocking=False):
            raise SourceError("config", "Un'autorizzazione Google è già in corso.")
        try:
            authorized = self._flow_runner(config)
            self._write(self.authorized_path, authorized)
        finally:
            self._lock.release()

    def disconnect(self) -> bool:
        """Elimina le credenziali locali e prova a revocarle presso Google."""
        text = self._read(self.authorized_path)
        revoked = bool(text) and self._revoker(text)
        try:
            self.authorized_path.unlink()
        except FileNotFoundError:
            pass
        return revoked

    def source(self) -> CalendarSource:
        text = self._read(self.authorized_path)
        if not text:
            raise SourceError("config")
        try:
            session, credentials = self._session_factory(text)
        except (ValueError, KeyError) as exc:
            raise SourceError("auth") from exc
        last_token = {"value": getattr(credentials, "token", None)}

        def persist_refresh() -> None:
            token = getattr(credentials, "token", None)
            if token and token != last_token["value"]:
                last_token["value"] = token
                self._write(self.authorized_path, credentials.to_json())

        return google_api.GoogleCalendarSource(session, on_refresh=persist_refresh)
