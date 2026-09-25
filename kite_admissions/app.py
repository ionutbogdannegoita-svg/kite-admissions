"""Applicazione Flask locale: un processo, SQLite, pagine HTML generate dal server."""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from . import APP_ID, APP_NAME, SCHOOL_NAME, __version__
from . import db as dbmod
from .gcal.connection import GoogleConnection
from .gcal.fake import ENV_FIXTURE, FixtureCalendarSource
from .gcal.source import CalendarSource
from .paths import Paths
from .schema import SCHEMA_VERSION
from .security import RequestGate, apply_security_headers, csrf_token, protect_request
from .services import backups
from .services.import_runner import ImportRunner
from .settings import SettingsStore
from .timeutil import utc_now

log = logging.getLogger("kite_admissions")

DEFAULT_PORT = 8765

# Endpoint utilizzabili anche in modalità ripristino (database assente o danneggiato).
RECOVERY_ENDPOINTS = frozenset({
    "static",
    "health",
    "panel.recovery",
    "panel.restore_confirm",
    "panel.restore_run",
    "panel.create_empty_database",
    "panel.open_folder",
    "panel.shutdown",
})
GATE_EXEMPT_ENDPOINTS = frozenset({"static", "health", "panel.restore_run"})

_ERROR_MESSAGES = {
    400: "Richiesta non valida.",
    403: "Richiesta respinta.",
    404: "Pagina o elemento non trovato.",
    405: "Operazione non consentita con questo metodo.",
    413: "File troppo grande.",
    500: "Si è verificato un errore interno. I dati già salvati non sono stati modificati.",
    503: "Operazione di manutenzione in corso: riprova tra qualche secondo.",
}
_ERROR_DETAILS = {
    "csrf": "La pagina era scaduta (per esempio dopo un riavvio dell'applicazione). Ricarica la pagina e ripeti l'operazione.",
    "host": "Indirizzo non previsto: apri l'applicazione dal collegamento «Avvia KITE Admissions».",
    "origin": "La richiesta proviene da una pagina esterna ed è stata bloccata.",
    "non_local": "Sono ammessi soltanto accessi da questo PC.",
}


@dataclass
class Recovery:
    reason: str
    detail: str = ""


class AppState:
    """Stato del processo: percorsi, configurazione, gate e modalità ripristino."""

    def __init__(self, paths: Paths, port: int, clock: Callable[[], datetime], services: dict[str, Any]):
        self.paths = paths
        self.port = port
        self.clock = clock
        self.settings = SettingsStore(paths.settings_path)
        self.gate = RequestGate()
        self.recovery: Recovery | None = None
        self.shutdown_callback: Callable[[], None] | None = None
        self.import_runner = ImportRunner()
        self.google = GoogleConnection(
            paths,
            store=services.get("protected_store"),
            flow_runner=services.get("oauth_flow"),
            session_factory=services.get("session_factory"),
            revoker=services.get("revoker"),
        )
        # Sorgente sostitutiva: sorgente simulata nei test o file di prova per il collaudo.
        self.calendar_override: CalendarSource | None = services.get("calendar_source")
        fixture = os.environ.get(ENV_FIXTURE)
        if self.calendar_override is None and fixture:
            self.calendar_override = FixtureCalendarSource(fixture)

    def now(self) -> datetime:
        return self.clock()

    @property
    def calendar_is_simulated(self) -> bool:
        return self.calendar_override is not None

    def calendar_ready(self) -> bool:
        return self.calendar_override is not None or self.google.is_connected()

    def calendar_source(self) -> CalendarSource:
        """Sorgente attiva; solleva SourceError se Google non è collegato."""
        if self.calendar_override is not None:
            return self.calendar_override
        return self.google.source()

    def open_db(self) -> dbmod.Database:
        return dbmod.Database(self.paths.db_path)

    def preop_backup(self, operation: str) -> backups.BackupInfo:
        """Copia separata obbligatoria prima di operazioni distruttive: se fallisce, solleva."""
        return backups.create_backup(
            self.paths, self.settings.load(), backups.KIND_PREOP, operation=operation, now=self.now()
        )

    def start(self) -> None:
        """Controllo all'avvio: crea il database, lo migra o entra in modalità ripristino."""
        inspection = dbmod.inspect(self.paths.db_path)
        status = inspection.status
        if status in ("missing", "empty"):
            if status == "missing" and backups.any_backup(self.paths):
                self.recovery = Recovery(
                    "missing",
                    "Il database non è presente ma esistono backup: ripristinane uno oppure crea un database vuoto.",
                )
                return
            dbmod.initialize(self.paths.db_path)
            log.info("Database creato (schema %s)", SCHEMA_VERSION)
            return
        if status == "needs_migration":
            try:
                applied = dbmod.migrate(self.paths.db_path, lambda: self.preop_backup("migrazione"))
                log.info("Schema aggiornato: %s migrazioni", applied)
            except (backups.BackupError, dbmod.DatabaseError, sqlite3.Error) as exc:
                self.recovery = Recovery("migration", f"Aggiornamento dello schema non eseguito: {exc}")
            return
        if status == "ok":
            return
        self.recovery = Recovery(status, inspection.detail)
        log.warning("Avvio in modalità ripristino: %s", status)

    def after_calendar_write(self) -> None:
        """Dopo un aggiornamento o un import da Calendar."""

    def request_shutdown(self) -> None:
        if self.shutdown_callback is not None:
            self.shutdown_callback()


def create_app(
    home: Any = None,
    *,
    port: int = DEFAULT_PORT,
    clock: Callable[[], datetime] = utc_now,
    testing: bool = False,
    **services: Any,
) -> Flask:
    paths = Paths.resolve(home)
    paths.ensure()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        SESSION_COOKIE_NAME="kite_admissions_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=1024 * 1024,
        TESTING=testing,
    )
    state = AppState(paths, port, clock, services)
    app.extensions["kite"] = state
    state.start()

    _register_hooks(app, state)
    _register_filters(app)
    _register_blueprints(app)

    @app.get("/health")
    def health():
        return jsonify(
            app=APP_ID,
            version=__version__,
            status="recovery" if state.recovery else "ok",
            schema_version=SCHEMA_VERSION,
        )

    return app


def _register_hooks(app: Flask, state: AppState) -> None:
    @app.before_request
    def guard():
        protect_request(state.port)
        if request.endpoint in GATE_EXEMPT_ENDPOINTS:
            return None
        if not state.gate.enter():
            return render_template("error.html", code=503, message=_ERROR_MESSAGES[503],
                                   detail=state.gate.reason or ""), 503
        g.gate_entered = True
        if state.recovery is not None and request.endpoint not in RECOVERY_ENDPOINTS:
            return redirect(url_for("panel.recovery"))
        return None

    @app.after_request
    def headers(response):
        return apply_security_headers(response, static=request.endpoint == "static")

    @app.teardown_request
    def release(_exc):
        database = g.pop("db", None)
        if database is not None:
            database.close()
        if g.pop("gate_entered", False):
            state.gate.exit()

    from .services import offers as offer_service
    from .services.common import new_id

    # Globali Jinja: disponibili anche nelle macro importate.
    app.jinja_env.globals.update(
        csrf_token=csrf_token,
        app_name=APP_NAME,
        school_name=SCHOOL_NAME,
        app_version=__version__,
        kite=state,
        new_uuid=new_id,
        offer_services=offer_service.services_of,
        offer_discount=offer_service.discount_cents,
        offer_channels=offer_service.CHANNELS,
    )

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        code = exc.code or 500
        reason = str(exc.description)
        detail = _ERROR_DETAILS.get(reason, "")
        if reason in ("host", "origin", "non_local"):
            # Richiesta respinta dalle protezioni: pagina minima, senza sessione né token.
            return render_template("error_plain.html", code=code, detail=detail), code
        return render_template("error.html", code=code, message=_ERROR_MESSAGES.get(code, exc.name),
                               detail=detail), code

    @app.errorhandler(Exception)
    def unexpected_error(exc: Exception):
        if app.config.get("TESTING") and not app.config.get("RENDER_UNEXPECTED_ERRORS"):
            raise exc
        # Tipo di errore e traceback nel log tecnico; nessun dato dei moduli.
        log.exception("Errore non gestito (%s)", exc.__class__.__name__)
        return render_template("error.html", code=500, message=_ERROR_MESSAGES[500], detail=""), 500


def _register_filters(app: Flask) -> None:
    from .web import filters

    filters.register(app)


def _register_blueprints(app: Flask) -> None:
    from .web import appointments, families, offers, panel, today

    app.register_blueprint(today.bp)
    app.register_blueprint(appointments.bp)
    app.register_blueprint(families.bp)
    app.register_blueprint(offers.bp)
    app.register_blueprint(panel.bp)
