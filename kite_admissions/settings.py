"""Configurazione locale non segreta (SPEC §8): un piccolo file JSON, mai token o credenziali."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from . import AUTHOR, SCHOOL_NAME
from .schema import SCHEMA_VERSION

DEFAULT_PAST_DAYS = 30
DEFAULT_FUTURE_DAYS = 180

# Chiavi che descrivono i dati: seguono il backup nel ripristino. Le altre riguardano il PC.
DATA_KEYS = ("school_name", "author", "calendar", "import_window")


def default_settings() -> dict[str, Any]:
    return {
        "school_name": SCHOOL_NAME,
        "author": AUTHOR,
        "schema_version": SCHEMA_VERSION,
        "calendar": None,
        "import_window": {"past_days": DEFAULT_PAST_DAYS, "future_days": DEFAULT_FUTURE_DAYS},
        "last_import": None,
        "last_backup": None,
        "post_restore": None,
    }


def write_json_atomic(path: Path, data: Any) -> None:
    """Scrive su file temporaneo nella stessa cartella e poi sostituisce atomicamente."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


class SettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.load_warning: str | None = None

    def load(self) -> dict[str, Any]:
        with self._lock:
            data = default_settings()
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return data
            except (OSError, ValueError):
                self.load_warning = "Il file settings.json non è leggibile: uso i valori predefiniti."
                return data
            if isinstance(raw, dict):
                for key in data:
                    if key in raw:
                        data[key] = raw[key]
            data["schema_version"] = SCHEMA_VERSION
            return data

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            write_json_atomic(self.path, data)

    def update(self, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            mutate(data)
            self.save(data)
            return data
