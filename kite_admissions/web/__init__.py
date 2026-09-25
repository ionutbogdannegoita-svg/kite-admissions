"""Viste HTML: Oggi, Appuntamenti, Famiglie, Scheda famiglia e pannello Dati."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from flask import current_app, flash, g, redirect, request, url_for

from ..db import Database

if TYPE_CHECKING:
    from ..app import AppState


def state() -> "AppState":
    return current_app.extensions["kite"]


def get_db() -> Database:
    if "db" not in g:
        g.db = state().open_db()
    return g.db


def form_value(name: str, default: str = "") -> str:
    return request.form.get(name, default)


def safe_next(target: str | None, fallback: str) -> str:
    """Solo percorsi interni: mai redirect verso siti esterni."""
    if not target:
        return fallback
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return fallback
    return target


def back(fallback_endpoint: str, **values: Any):
    return redirect(safe_next(request.form.get("next"), url_for(fallback_endpoint, **values)))


def flash_error(message: str) -> None:
    flash(message, "error")


def flash_ok(message: str) -> None:
    flash(message, "ok")


def flash_warning(message: str) -> None:
    flash(message, "warning")
