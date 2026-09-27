"""Eccezioni e aiuti comuni ai servizi."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from ..timeutil import to_iso

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class ValidationError(Exception):
    """Dati non validi: il messaggio è mostrato all'utente."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class StaleWriteError(Exception):
    """Il record è cambiato dopo l'apertura del modulo (altra scheda del browser)."""

    def __init__(self, message: str = "I dati sono stati modificati in un'altra scheda: ricarica la pagina prima di salvare."):
        super().__init__(message)
        self.message = message


class NotFoundError(Exception):
    pass


class AlreadySavedError(Exception):
    """Invio ripetuto dello stesso modulo di creazione: il record esiste già."""

    def __init__(self, record_id: str):
        super().__init__(record_id)
        self.record_id = record_id


def new_id() -> str:
    return str(uuid.uuid4())


def valid_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def creation_id(value: Any) -> str:
    """ID generato al rendering del modulo: rende idempotente un doppio invio."""
    if value is None or value == "":
        return new_id()
    if not valid_uuid(value):
        raise ValidationError("Identificativo del modulo non valido: ricarica la pagina.")
    return value


def clean(value: Any, max_length: int = 4000) -> str | None:
    """Testo ripulito; None se vuoto."""
    if value is None:
        return None
    text = str(value).replace("\r\n", "\n").strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValidationError(f"Testo troppo lungo (massimo {max_length} caratteri).")
    return text


def stamp(now: datetime) -> str:
    return to_iso(now)
