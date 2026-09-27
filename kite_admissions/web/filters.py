"""Filtri Jinja: date in Europe/Rome, importi in euro, etichette in italiano."""

from __future__ import annotations

from flask import Flask

from .. import catalog, timeutil
from ..labels import LABELS, euro, euro_input, label
from ..services import leads as lead_service

__all__ = ["LABELS", "euro", "euro_input", "label", "register"]


def rfc_datetime(value: str | None) -> str:
    """Orario RFC 3339 di Google mostrato in Europe/Rome; il testo originale se non leggibile."""
    if not value:
        return ""
    try:
        return timeutil.format_datetime(timeutil.parse_rfc3339(value))
    except ValueError:
        return value


def catalog_label(code: str | None, list_name: str) -> str:
    """{{ code|cat('OBSTACLES') }}: etichetta del catalogo, anche per voci non più in elenco."""
    return catalog.label(getattr(catalog, list_name), code)


def catalog_labels(codes: list[str] | None, list_name: str) -> list[str]:
    entries = getattr(catalog, list_name)
    return [catalog.label(entries, code) for code in codes or []]


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
        cat=catalog_label,
        cats=catalog_labels,
        languages=lead_service.languages_summary,
    )
