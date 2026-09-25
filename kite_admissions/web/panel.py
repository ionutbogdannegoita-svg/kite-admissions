"""Pannello tecnico «Dati e collegamento Google» (SPEC §3, §9)."""

from __future__ import annotations

import os
import sys

from flask import Blueprint, abort, render_template, request

from .. import db as dbmod
from ..schema import SCHEMA_VERSION
from ..services import backups
from . import back, flash_error, get_db, state

bp = Blueprint("panel", __name__, url_prefix="/dati")

_FOLDERS = {
    "dati": lambda paths: paths.data_dir,
    "backup": lambda paths: paths.backups_dir,
    "export": lambda paths: paths.exports_dir,
}


@bp.get("/")
def index():
    app_state = state()
    counts = dbmod.table_counts(get_db().conn)
    return render_template(
        "panel/index.html",
        active="panel",
        paths=app_state.paths,
        counts=counts,
        schema_version=SCHEMA_VERSION,
        location_warning=app_state.paths.location_warning(),
    )


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
        flash_error(f"Apri manualmente la cartella: {folder}")
    return back("panel.index")


@bp.post("/chiudi")
def shutdown():
    state().request_shutdown()
    return render_template("panel/closed.html")
