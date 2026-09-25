"""Vista Appuntamenti e aggiornamento da Google Calendar (SPEC §3, §4.2)."""

from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..gcal.source import SourceError
from ..services import appointments as appointment_service
from ..services import calendar_import
from ..services import families as family_service
from ..services import leads as lead_service
from ..services.common import (
    AlreadySavedError,
    NotFoundError,
    StaleWriteError,
    ValidationError,
    clean,
    creation_id,
    new_id,
    valid_uuid,
)
from ..services.common import stamp
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


def _revision() -> int:
    try:
        return int(request.form.get("revision", ""))
    except ValueError:
        raise StaleWriteError("Modulo incompleto: ricarica la pagina.") from None


def _to_detail(appointment_id: str, anchor: str = ""):
    return redirect(url_for("appointments.detail", appointment_id=appointment_id) + anchor)


@bp.get("/<appointment_id>")
def detail(appointment_id: str):
    appointment = _appointment_or_404(appointment_id)
    db = get_db()
    query = request.args.get("q", "").strip()
    linked = appointment["family_id"] is not None
    found = appointment_service.contacts(appointment)
    family = family_service.get_family(db, appointment["family_id"]) if linked else None
    new_contacts = []
    if family is not None:
        known = {family[key] for key in ("primary_phone_norm", "secondary_phone_norm", "primary_email_norm",
                                         "secondary_email_norm") if family[key]}
        new_contacts = [item for item in found if item.get("normalized") not in known]
    default_visited = appointment["visited_at"]
    if not default_visited and appointment["src_start_at"] and appointment["src_start_at"] <= stamp(state().now()):
        default_visited = appointment["src_start_at"]
    return render_template(
        "appointments/detail.html", active="appointments", a=appointment, family=family,
        new_contacts=new_contacts, default_visited=default_visited,
        contacts=found,
        description=html_to_text(appointment["src_description"]),
        suggestions=[] if linked else appointment_service.suggestions(db, appointment),
        query=query,
        results=family_service.search_families(db, query=query, archived="tutte") if query and not linked else [],
        leads=lead_service.family_leads(db, appointment["family_id"]) if linked else [],
        has_report=appointment_service.has_report(appointment),
        discrepancy=appointment_service.date_discrepancy(appointment),
        outcomes=appointment_service.OUTCOMES,
        interactions=db.all("SELECT * FROM Interaction WHERE appointment_id = ? ORDER BY occurred_at DESC, rowid DESC",
                            (appointment_id,)),
    )


@bp.post("/<appointment_id>/collega")
def link(appointment_id: str):
    _appointment_or_404(appointment_id)
    family_id = request.form.get("family_id", "")
    if not valid_uuid(family_id):
        abort(400)
    try:
        appointment_service.link_family(get_db(), appointment_id, family_id, request.form.get("lead_id") or None,
                                        revision=_revision(), now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _to_detail(appointment_id)
    except NotFoundError:
        flash_error("Famiglia non trovata.")
        return _to_detail(appointment_id)
    flash_ok("Appuntamento collegato alla famiglia. I dati di Google restano separati dall'anagrafica.")
    return _to_detail(appointment_id, "#locale")


@bp.post("/<appointment_id>/richiesta")
def choose_lead(appointment_id: str):
    _appointment_or_404(appointment_id)
    try:
        appointment_service.set_lead(get_db(), appointment_id, request.form.get("lead_id") or None,
                                     revision=_revision(), now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _to_detail(appointment_id)
    flash_ok("Riferimento dell'incontro aggiornato.")
    return _to_detail(appointment_id, "#locale")


@bp.get("/<appointment_id>/cambia")
def change_link_form(appointment_id: str):
    appointment = _appointment_or_404(appointment_id)
    if appointment["family_id"] is None:
        return _to_detail(appointment_id)
    db = get_db()
    query = request.args.get("q", "").strip()
    target = None
    target_id = request.args.get("famiglia", "")
    if valid_uuid(target_id):
        try:
            target = family_service.get_family(db, target_id)
        except NotFoundError:
            target = None
    results = [row for row in family_service.search_families(db, query=query, archived="tutte")
               if row["id"] != appointment["family_id"]] if query else []
    return render_template(
        "appointments/change_link.html", active="appointments", a=appointment, query=query, results=results,
        target=target, has_report=appointment_service.has_report(appointment),
        offers_count=db.scalar("SELECT count(*) FROM Offer WHERE family_id = ?", (appointment["family_id"],)),
        followups_count=db.scalar("SELECT count(*) FROM FollowUp WHERE family_id = ? AND status = 'APERTO'",
                                  (appointment["family_id"],)),
    )


@bp.post("/<appointment_id>/cambia")
def change_link(appointment_id: str):
    _appointment_or_404(appointment_id)
    family_id = request.form.get("family_id", "")
    if not valid_uuid(family_id):
        abort(400)
    try:
        appointment_service.change_link(get_db(), appointment_id, family_id, revision=_revision(), now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _to_detail(appointment_id)
    except NotFoundError:
        flash_error("Famiglia non trovata.")
        return _to_detail(appointment_id)
    flash_ok("Collegamento cambiato. Offerte e follow-up della famiglia precedente non sono stati spostati.")
    return _to_detail(appointment_id, "#locale")


def _render_create_family(appointment, form, *, errors=None, duplicates=None, code: int = 200):
    return render_template(
        "appointments/create_family.html", active="appointments", a=appointment, form=form, errors=errors or [],
        duplicates=duplicates or [], contacts=appointment_service.contacts(appointment),
        years=lead_service.school_year_options(rome_today(state().now())), grades=lead_service.GRADE_SUGGESTIONS,
        sources=family_service.CONTACT_SOURCES,
    ), code


@bp.route("/<appointment_id>/nuova-famiglia", methods=["GET", "POST"])
def create_family(appointment_id: str):
    appointment = _appointment_or_404(appointment_id)
    if appointment["family_id"] is not None:
        flash_warning("L'appuntamento è già collegato a una famiglia.")
        return _to_detail(appointment_id)
    if request.method == "GET":
        found = appointment_service.contacts(appointment)
        phones = [c for c in found if c.get("type") == "phone"]
        emails = [c for c in found if c.get("type") == "email"]
        adult = next((c.get("label") for c in emails if c.get("label")), "")
        form = {"new_id": new_id(), "revision": appointment["revision"],
                "display_name": appointment_service.suggest_label(appointment["src_title"]),
                "primary_adult_name": adult,
                "primary_phone": phones[0]["value"] if phones else "",
                "primary_email": emails[0]["value"] if emails else ""}
        return _render_create_family(appointment, form)
    form = request.form
    try:
        values = family_service.parse_family(form)
        first_lead = lead_service.parse_lead(form, prefix="lead_") if form.get("lead_display_name", "").strip() else None
        family_id = appointment_service.create_family_from_event(
            get_db(), appointment_id, values, first_lead, family_id=creation_id(form.get("new_id")),
            revision=_revision(), now=state().now(), confirm_distinct=form.get("confirm_distinct") == "1")
    except ValidationError as exc:
        return _render_create_family(appointment, form, errors=[exc.message], code=422)
    except StaleWriteError as exc:
        return _render_create_family(appointment, form, errors=[exc.message], code=409)
    except family_service.DuplicateWarning as warning:
        return _render_create_family(appointment, form, duplicates=warning.matches)
    except AlreadySavedError as saved:
        flash_warning("La famiglia era già stata creata: nessun doppione.")
        return redirect(url_for("families.detail", family_id=saved.record_id))
    flash_ok("Famiglia creata dall'evento e collegata all'appuntamento.")
    return _to_detail(appointment_id, "#locale")


@bp.post("/<appointment_id>/preparazione")
def preparation(appointment_id: str):
    _appointment_or_404(appointment_id)
    try:
        appointment_service.save_preparation(get_db(), appointment_id, clean(request.form.get("preparation"), 8000),
                                             revision=_revision(), now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _to_detail(appointment_id, "#preparazione")
    flash_ok("Preparazione salvata.")
    return _to_detail(appointment_id, "#preparazione")


@bp.post("/<appointment_id>/resoconto")
def report(appointment_id: str):
    _appointment_or_404(appointment_id)
    try:
        values = appointment_service.parse_report(request.form, state().now())
        appointment_service.save_report(get_db(), appointment_id, values, revision=_revision(), now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _to_detail(appointment_id, "#resoconto")
    flash_ok("Resoconto salvato.")
    return _to_detail(appointment_id, "#resoconto")


@bp.post("/<appointment_id>/rimuovi")
def remove(appointment_id: str):
    _appointment_or_404(appointment_id)
    try:
        appointment_service.remove_imported(get_db(), appointment_id, state().now())
    except ValidationError as exc:
        flash_error(exc.message)
        return _to_detail(appointment_id)
    flash_ok("Evento tolto dagli appuntamenti e segnato come «Ignorato» (revocabile dall'anteprima).")
    return redirect(url_for("appointments.index", vista="da_collegare"))


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
