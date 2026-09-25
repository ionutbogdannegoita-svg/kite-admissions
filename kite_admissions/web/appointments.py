"""Vista Appuntamenti e aggiornamento da Google Calendar (SPEC §3, §4.2)."""

from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..gcal.source import SourceError
from ..services import appointments as appointment_service
from ..services import calendar_import
from ..services.common import NotFoundError, valid_uuid
from ..services.import_runner import RunnerBusy
from ..settings import DEFAULT_FUTURE_DAYS, DEFAULT_PAST_DAYS
from ..textutil import html_to_text
from ..timeutil import import_window, rome_today
from . import flash_error, flash_ok, flash_warning, get_db, state

bp = Blueprint("appointments", __name__, url_prefix="/appuntamenti")

PREVIEW_MAX_AGE = timedelta(minutes=60)


@bp.get("/")
def index():
    view = request.args.get("vista", "prossimi")
    if view not in appointment_service.VIEWS:
        view = "prossimi"
    db = get_db()
    now = state().now()
    return render_template(
        "appointments/index.html", active="appointments", view=view, views=appointment_service.VIEWS,
        appointments=appointment_service.list_appointments(db, view, now),
        counts=appointment_service.counts(db, now), window=_window_defaults(),
    )


def _appointment_or_404(appointment_id: str):
    if not valid_uuid(appointment_id):
        abort(404)
    try:
        return appointment_service.get_appointment(get_db(), appointment_id)
    except NotFoundError:
        abort(404)


@bp.get("/<appointment_id>")
def detail(appointment_id: str):
    appointment = _appointment_or_404(appointment_id)
    return render_template(
        "appointments/detail.html", active="appointments", a=appointment,
        contacts=appointment_service.contacts(appointment),
        description=html_to_text(appointment["src_description"]),
    )


def _window_defaults() -> dict[str, int]:
    window = state().settings.load().get("import_window") or {}
    return {"past_days": int(window.get("past_days", DEFAULT_PAST_DAYS)),
            "future_days": int(window.get("future_days", DEFAULT_FUTURE_DAYS))}


def _window_from_form() -> tuple[int, int]:
    defaults = _window_defaults()
    try:
        past = int(request.form.get("past_days", defaults["past_days"]))
        future = int(request.form.get("future_days", defaults["future_days"]))
    except ValueError:
        raise ValueError("Finestra non valida: indica un numero di giorni.") from None
    if not (0 <= past <= 365 and 1 <= future <= 730):
        raise ValueError("Finestra non valida: fino a 365 giorni precedenti e 730 successivi.")
    return past, future


def _refresh_job(app_state, calendar_id: str, past: int, future: int):
    def job(report):
        now = app_state.now()
        time_min, time_max = import_window(rome_today(now), past, future)
        db = app_state.open_db()
        try:
            try:
                source = app_state.calendar_source()
            except SourceError as exc:
                source = calendar_import.UnavailableSource(exc)
            summary, preview = calendar_import.refresh(
                db, source, calendar_id=calendar_id, time_min=time_min, time_max=time_max, now=now,
                clock=app_state.clock, progress=report)
        finally:
            db.close()
        app_state.settings.update(
            lambda data: data.__setitem__("last_import", calendar_import.summary_record(summary, data.get("last_import"))))
        app_state.after_calendar_write()
        return summary, preview

    return job


@bp.post("/aggiorna")
def refresh():
    app_state = state()
    settings = app_state.settings.load()
    calendar = settings.get("calendar")
    if not calendar:
        flash_error("Scegli prima il calendario sorgente nel pannello «Dati e Google».")
        return redirect(url_for("panel.index"))
    if settings.get("post_restore"):
        flash_error("Dopo un ripristino completa la verifica indicata prima di riprendere gli aggiornamenti.")
        return redirect(url_for("panel.index"))
    try:
        past, future = _window_from_form()
    except ValueError as exc:
        flash_error(str(exc))
        return redirect(url_for("appointments.index"))
    started = app_state.import_runner.start(_refresh_job(app_state, calendar["id"], past, future),
                                            started_at=app_state.now())
    if not started:
        flash_warning("Un aggiornamento da Google Calendar è già in corso.")
    return redirect(url_for("appointments.refresh_status"))


@bp.get("/aggiornamento")
def refresh_status():
    runner = state().import_runner
    preview = runner.preview
    stale = preview is not None and state().now() - preview.created_at > PREVIEW_MAX_AGE
    return render_template(
        "appointments/refresh.html", active="appointments", runner=runner, running=runner.running,
        summary=runner.summary, preview=preview, preview_stale=stale,
        last_import=state().settings.load().get("last_import"),
    )


def _usable_preview():
    preview = state().import_runner.preview
    if preview is None or state().now() - preview.created_at > PREVIEW_MAX_AGE:
        flash_warning("L'anteprima non è più attuale: premi di nuovo «Aggiorna da Google Calendar».")
        return None
    return preview


@bp.post("/importa")
def import_selected():
    event_ids = request.form.getlist("event_id")
    if not event_ids:
        flash_warning("Nessun evento selezionato: nulla è stato importato.")
        return redirect(url_for("appointments.refresh_status"))
    preview = _usable_preview()
    if preview is None:
        return redirect(url_for("appointments.refresh_status"))
    app_state = state()
    try:
        with app_state.import_runner.exclusive():
            imported, already = calendar_import.import_selected(get_db(), preview, event_ids, app_state.now())
    except RunnerBusy as exc:
        flash_warning(str(exc))
        return redirect(url_for("appointments.refresh_status"))
    if imported:
        flash_ok(f"Importati {imported} {'evento' if imported == 1 else 'eventi'}: ora sono tra «Da collegare».")
    if already:
        flash_warning(f"{already} eventi erano già stati importati: nessuna riga duplicata.")
    return redirect(url_for("appointments.index", vista="da_collegare"))


@bp.post("/ignora")
def ignore():
    preview = _usable_preview()
    if preview is None:
        return redirect(url_for("appointments.refresh_status"))
    try:
        calendar_import.ignore_event(get_db(), preview, request.form.get("event_id", ""), state().now())
    except NotFoundError:
        abort(404)
    flash_ok("Evento ignorato: non verrà più proposto. La scelta è revocabile dall'anteprima.")
    return redirect(url_for("appointments.refresh_status") + "#ignorati")


@bp.post("/non-ignorare")
def unignore():
    preview = state().import_runner.preview
    calendar_id = preview.calendar_id if preview else (state().settings.load().get("calendar") or {}).get("id", "")
    if calendar_import.unignore_event(get_db(), preview, calendar_id, request.form.get("event_id", "")):
        flash_ok("L'evento torna tra quelli selezionabili.")
    return redirect(url_for("appointments.refresh_status"))
