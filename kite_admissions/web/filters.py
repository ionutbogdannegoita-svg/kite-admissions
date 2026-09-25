"""Filtri Jinja: date in Europe/Rome, importi in euro, etichette in italiano."""

from __future__ import annotations

from flask import Flask

from .. import timeutil

LABELS = {
    # Stati richiesta (SPEC §7)
    "IN_CORSO": "In corso",
    "IN_PAUSA": "In pausa",
    "ISCRITTO": "Iscritto",
    "NON_PROSEGUE": "Non prosegue",
    # Stato Google dell'appuntamento
    "CONFIRMED": "Confermato",
    "TENTATIVE": "Da confermare",
    "CANCELLED_SOURCE": "Annullato su Calendar",
    "VERIFIED": "Verificato",
    "NOT_VERIFIED": "Non verificato",
    # Esito visita
    "SVOLTA": "Visita svolta",
    "NON_PRESENTATA": "Famiglia non presentata",
    "ANNULLATA": "Annullata o rinviata",
    # Offerte
    "BOZZA": "Bozza",
    "COMUNICATA": "Comunicata",
    "RITIRATA": "Ritirata",
    "ANNUALE": "annuale",
    "MENSILE": "mensile",
    "UNA_TANTUM": "una tantum",
    # Follow-up
    "APERTO": "Aperto",
    "COMPLETATO": "Completato",
    "ANNULLATO": "Annullato",
    "RICHIAMARE": "Richiamare",
    "INVIARE_INFORMAZIONI": "Inviare informazioni",
    "FISSARE_VISITA": "Far fissare la visita",
    "ALTRO": "Altro",
    # Interaction
    "NOTA": "Nota",
    "TELEFONATA": "Telefonata",
    "EMAIL": "Email",
    "RETTIFICA": "Rettifica",
    "CALENDAR_CANCELLED": "Evento annullato su Calendar",
    "CALENDAR_REACTIVATED": "Evento riattivato su Calendar",
    "LEAD_STATUS_CHANGED": "Cambio di stato",
    "LEAD_ENROLLED": "Iscrizione",
    "LEAD_NOT_CONTINUING": "Non prosecuzione",
    "LEAD_REOPENED": "Riapertura",
    # Esclusioni
    "IGNORED": "Ignorato",
    "FAMILY_DELETED": "Famiglia eliminata",
}


def label(value: object) -> str:
    if value is None:
        return ""
    return LABELS.get(str(value), str(value))


def euro(cents: int | None) -> str:
    """1234567 -> '12.345,67 €'."""
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    whole, rest = divmod(cents, 100)
    grouped = f"{whole:,}".replace(",", ".")
    return f"{sign}{grouped},{rest:02d} €"


def euro_input(cents: int | None) -> str:
    """Valore per un campo modulo: '1000,50'."""
    if cents is None:
        return ""
    whole, rest = divmod(int(cents), 100)
    return f"{whole},{rest:02d}"


def register(app: Flask) -> None:
    app.jinja_env.filters.update(
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
