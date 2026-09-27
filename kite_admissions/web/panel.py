"""Pannello tecnico «Dati e collegamento Google» (SPEC §3, §4.1, §9)."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, send_file, url_for

from .. import db as dbmod
from ..gcal.source import SourceError
from ..schema import SCHEMA_VERSION
from ..security import GateBusy
from ..services import backups
from ..services import export as export_service
from ..services import restore as restore_service
from ..services.import_runner import RunnerBusy
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
        backup_list=backups.list_backups(app_state.paths),
        export_list=export_service.list_exports(app_state.paths)[:10],
        retention_days=backups.RETENTION_DAYS,
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
        inspection=dbmod.inspect(app_state.paths.db_path),
    )


def _backup_or_404(kind: str, name: str):
    try:
        return backups.find_backup(state().paths, kind, name)
    except backups.BackupError:
        abort(404)


@bp.get("/ripristino/<kind>/<name>")
def restore_confirm(kind: str, name: str):
    app_state = state()
    path = _backup_or_404(kind, name)
    workdir = tempfile.mkdtemp(prefix=".tmp-verifica-", dir=app_state.paths.backups_dir)
    try:
        manifest = backups.validate_backup(path, Path(workdir)).manifest
        problem = None
    except backups.BackupError as exc:
        manifest, problem = backups.read_manifest_safe(path), str(exc)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return render_template("panel/restore_confirm.html", active="panel", kind=kind, name=name, manifest=manifest,
                           problem=problem, inspection=dbmod.inspect(app_state.paths.db_path))


@bp.post("/ripristino/<kind>/<name>")
def restore_run(kind: str, name: str):
    app_state = state()
    path = _backup_or_404(kind, name)
    if request.form.get("confirm_text", "").strip().upper() != "RIPRISTINA":
        flash_error("Per confermare digita RIPRISTINA.")
        return redirect(url_for("panel.restore_confirm", kind=kind, name=name))
    try:
        with app_state.import_runner.exclusive():
            with app_state.gate.exclusive("Ripristino dei dati in corso"):
                report = restore_service.restore(
                    app_state.paths, app_state.settings, path, now=app_state.now(),
                    preop_backup=lambda: app_state.preop_backup("ripristino"))
                app_state.recovery = None
                app_state.import_runner.clear_preview()
    except RunnerBusy as exc:
        flash_error(str(exc))
        return redirect(url_for("panel.restore_confirm", kind=kind, name=name))
    except GateBusy:
        flash_error("Altre operazioni sono in corso: riprova tra qualche secondo.")
        return redirect(url_for("panel.restore_confirm", kind=kind, name=name))
    except restore_service.RestoreError as exc:
        inspection = dbmod.inspect(app_state.paths.db_path)
        if not inspection.healthy or "Verifica dopo il ripristino" in str(exc):
            from ..app import Recovery

            app_state.recovery = Recovery("restore_failed", f"Ripristino non riuscito: {exc}")
        flash_error(str(exc))
        return redirect(url_for("panel.recovery"))
    log.info("Ripristino completato da %s", name)
    flash_ok("Ripristino completato e verificato. Controlla i dati indicati prima di riprendere l'uso normale.")
    return redirect(url_for("panel.restore_review"))


@bp.get("/verifica-ripristino")
def restore_review():
    review = state().settings.load().get("post_restore")
    return render_template("panel/restore_review.html", active="panel", review=review)


@bp.post("/verifica-ripristino")
def restore_review_done():
    state().settings.update(lambda data: data.__setitem__("post_restore", None))
    flash_ok("Verifica confermata: import e modifiche di nuovo disponibili.")
    return redirect(url_for("today.index"))


@bp.post("/database-vuoto")
def create_empty_database():
    """Solo in modalità ripristino: conserva il file esistente e crea un database vuoto."""
    app_state = state()
    if app_state.recovery is None:
        abort(400)
    if request.form.get("confirm_text", "").strip().upper() != "NUOVO":
        flash_error("Per confermare digita NUOVO.")
        return redirect(url_for("panel.recovery"))
    try:
        with app_state.gate.exclusive("Creazione di un nuovo database"):
            preserved = []
            if app_state.paths.db_path.exists():
                preserved = restore_service.preserve_current(app_state.paths, app_state.now())
                app_state.paths.db_path.unlink()
                for suffix in ("-journal", "-wal", "-shm"):
                    journal = Path(str(app_state.paths.db_path) + suffix)
                    if journal.exists():
                        journal.unlink()
            dbmod.initialize(app_state.paths.db_path)
            app_state.recovery = None
    except (OSError, GateBusy, dbmod.DatabaseError) as exc:
        flash_error(f"Operazione non riuscita: {exc}")
        return redirect(url_for("panel.recovery"))
    flash_ok("Nuovo database vuoto creato." + (" Il file precedente è stato conservato nella cartella backup/preservati."
                                               if preserved else ""))
    return redirect(url_for("panel.index"))


@bp.post("/backup")
def backup_now():
    app_state = state()
    try:
        info = backups.create_backup(app_state.paths, app_state.settings.load(), backups.KIND_MANUAL,
                                     now=app_state.now())
    except (backups.BackupError, OSError) as exc:
        flash_error(f"Backup non riuscito: {exc}")
        return redirect(url_for("panel.index") + "#backup")
    app_state.settings.update(lambda data: data.__setitem__(
        "last_backup", {"at": info.created_at, "name": info.name, "kind": info.kind, "error": None, "error_at": None}))
    flash_ok(f"Backup creato e verificato: {info.name}. Puoi scaricarlo e copiarlo su un supporto esterno cifrato.")
    return redirect(url_for("panel.index") + "#backup")


@bp.get("/backup/<kind>/<name>")
def backup_download(kind: str, name: str):
    return send_file(_backup_or_404(kind, name), as_attachment=True, download_name=name, mimetype="application/zip")


@bp.post("/export")
def export_run():
    app_state = state()
    try:
        path = export_service.export_package(get_db(), app_state.paths, app_state.settings.load(), app_state.now())
    except OSError as exc:
        flash_error(f"Export non riuscito: {exc}")
        return redirect(url_for("panel.index") + "#export")
    flash_ok(f"Export creato: {path.name}. Contiene dati personali: conservalo con cautela.")
    return redirect(url_for("panel.index") + "#export")


@bp.get("/export/<name>")
def export_download(name: str):
    path = export_service.find_export(state().paths, name)
    if path is None:
        abort(404)
    return send_file(path, as_attachment=True, download_name=name, mimetype="application/zip")


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
