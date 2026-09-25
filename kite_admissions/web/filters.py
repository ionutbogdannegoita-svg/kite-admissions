"""Filtri Jinja: date in Europe/Rome, importi in euro, etichette in italiano."""

from __future__ import annotations

from flask import Flask

from .. import timeutil
from ..labels import LABELS, euro, euro_input, label

__all__ = ["LABELS", "euro", "euro_input", "label", "register"]


def rfc_datetime(value: str | None) -> str:
    """Orario RFC 3339 di Google mostrato in Europe/Rome; il testo originale se non leggibile."""
    if not value:
        return ""
    try:
        return timeutil.format_datetime(timeutil.parse_rfc3339(value))
    except ValueError:
        return value


def register(app: Flask) -> None:
    app.jinja_env.filters.update(
        rfc_dt=rfc_datetime,
        label=label,
        euro=euro,
        euro_input=euro_input,
        dt=timeutil.format_datetime,
        dt_short=lambda value: timeutil.format_datetime(value, weekday=False),
        d=timeutil.format_date,
        dw=lambda value: timeutil.format_date(value, weekday=True),
        t=timeutil.format_time,
        local_input=timeutil.utc_iso_to_local_input,
    )
