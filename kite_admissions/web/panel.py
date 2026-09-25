"""Pannello tecnico «Dati e collegamento Google» (SPEC §3, §4.1, §9)."""

from __future__ import annotations

import logging
import os
import sys

from flask import Blueprint, abort, redirect, render_template, request, url_for

from .. import db as dbmod
from ..gcal.source import SourceError
from ..schema import SCHEMA_VERSION
from ..services import backups
from ..settings import DEFAULT_FUTURE_DAYS, DEFAULT_PAST_DAYS
from . import back, flash_error, flash_ok, flash_warning, get_db, state

log = logging.getLogger("kite_admissions")

bp = Blueprint("panel", __name__, url_prefix="/dati")

_FOLDERS = {
    "dati": lambda paths: paths.data_dir,
    "backup": lambda paths: paths.backups_dir,
    "export": lambda paths: paths.exports_dir,
}


@bp.get("/")
def index():
    app_state = state()
    settings = app_state.settings.load()
    window = settings.get("import_window") or {}
    exclusions = {row[0]: row[1] for row in get_db().all(
        "SELECT reason, count(*) FROM CalendarExclusion GROUP BY reason")}
    return render_template(
        "panel/index.html",
        active="panel",
        paths=app_state.paths,
        counts=dbmod.table_counts(get_db().conn),
        schema_version=SCHEMA_VERSION,
        location_warning=app_state.paths.location_warning(),
        settings=settings,
        window={"past_days": window.get("past_days", DEFAULT_PAST_DAYS),
                "future_days": window.get("future_days", DEFAULT_FUTURE_DAYS)},
        google_client=_safe(app_state.google.client_summary),
        google_connected=_safe(app_state.google.is_connected),
        simulated=app_state.calendar_is_simulated,
        simulated_label=app_state.calendar_override.label if app_state.calendar_override else "",
        exclusions=exclusions,
    )


def _safe(function):
    try:
        return function()
    except Exception:  # noqa: BLE001 - es. DPAPI non disponibile: lo stato risulta «non configurato»
        log.warning("Lettura delle credenziali Google non riuscita", exc_info=True)
        return None


@bp.get("/ripristino")
def recovery():
    app_state = state()
    return render_template(
        "panel/recovery.html",
        active="panel",
        recovery=app_state.recovery,
        paths=app_state.paths,
        backups=backups.list_backups(app_state.paths),
    )


@bp.post("/apri-cartella")
def open_folder():
    target = _FOLDERS.get(request.form.get("folder", ""))
    if target is None:
        abort(400)
    folder = target(state().paths)
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606 - apre Esplora file sulla cartella locale
    else:
        flash_warning(f"Apri manualmente la cartella: {folder}")
    return back("panel.index")


# Collegamento Google -------------------------------------------------------------------

@bp.post("/google/client")
def google_client():
    upload = request.files.get("client_file")
    if upload is None or not upload.filename:
        flash_error("Scegli il file JSON del client OAuth scaricato da Google Cloud.")
        return redirect(url_for("panel.index") + "#google")
    try:
        state().google.save_client_config(upload.read(256 * 1024))
    except ValueError as exc:
        flash_error(str(exc))
        return redirect(url_for("panel.index") + "#google")
    flash_ok("Client OAuth salvato in forma protetta. Ora premi «Collega Google».")
    return redirect(url_for("panel.index") + "#google")


@bp.post("/google/collega")
def google_connect():
    try:
        state().google.connect()
    except SourceError as exc:
        flash_error(exc.message)
        return redirect(url_for("panel.index") + "#google")
    except Exception as exc:  # noqa: BLE001 - autorizzazione annullata, scaduta o rifiutata
        log.warning("Autorizzazione Google non completata (%s)", exc.__class__.__name__)
        flash_error(f"Autorizzazione Google non completata: {exc}")
        return redirect(url_for("panel.index") + "#google")
    flash_ok("Google Calendar collegato in sola lettura. Ora scegli il calendario sorgente.")
    return redirect(url_for("panel.google_calendars"))


@bp.post("/google/scollega")
def google_disconnect():
    revoked = state().google.disconnect()
    if revoked:
        flash_ok("Collegamento rimosso da questo PC e accesso revocato presso Google.")
    else:
        flash_ok("Collegamento rimosso da questo PC. Puoi revocare l'accesso anche dall'account Google "
                 "(Sicurezza › Connessioni a terze parti).")
    return redirect(url_for("panel.index") + "#google")


@bp.get("/google/calendari")
def google_calendars():
    try:
        calendars = state().calendar_source().list_calendars()
    except SourceError as exc:
        flash_error(exc.message)
        return redirect(url_for("panel.index") + "#google")
    current = (state().settings.load().get("calendar") or {}).get("id")
    return render_template("panel/calendars.html", active="panel", calendars=calendars, current=current)


@bp.post("/google/calendario")
def google_select_calendar():
    calendar_id = (request.form.get("calendar_id") or "").strip()
    try:
        calendars = state().calendar_source().list_calendars()
    except SourceError as exc:
        flash_error(exc.message)
        return redirect(url_for("panel.index") + "#google")
    chosen = next((item for item in calendars if item["id"] == calendar_id), None)
    if chosen is None:
        flash_error("Scegli un calendario dall'elenco di quelli accessibili.")
        return redirect(url_for("panel.google_calendars"))
    summary = (chosen.get("summary") or calendar_id)[:300]
    state().settings.update(lambda data: data.__setitem__("calendar", {"id": calendar_id, "summary": summary}))
    flash_ok(f"Calendario sorgente: {summary}. Gli eventi si importano solo selezionandoli dall'anteprima.")
    return redirect(url_for("panel.index") + "#google")


@bp.post("/google/finestra")
def google_window():
    try:
        past = int(request.form.get("past_days", ""))
        future = int(request.form.get("future_days", ""))
    except ValueError:
        past = future = -1
    if not (0 <= past <= 365 and 1 <= future <= 730):
        flash_error("Finestra non valida: fino a 365 giorni precedenti e 730 successivi.")
        return redirect(url_for("panel.index") + "#google")
    state().settings.update(
        lambda data: data.__setitem__("import_window", {"past_days": past, "future_days": future}))
    flash_ok("Finestra predefinita aggiornata.")
    return redirect(url_for("panel.index") + "#google")


@bp.post("/chiudi")
def shutdown():
    state().request_shutdown()
    return render_template("panel/closed.html")
