"""Vista Appuntamenti e aggiornamento da Google Calendar (SPEC §3, §4.2)."""

from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, abort, redirect, render_template, request, url_for

from .. import catalog
from ..gcal.source import SourceError
from ..labels import label
from ..schema import FOLLOWUP_ACTIONS
from ..services import appointments as appointment_service
from ..services import calendar_import
from ..services import families as family_service
from ..services import followups as followup_service
from ..services import interview as interview_service
from ..services import leads as lead_service
from ..services import offers as offer_service
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
from ..timeutil import format_datetime, import_window, rome_today
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
        followup_actions=FOLLOWUP_ACTIONS,
        today_iso=rome_today(state().now()).isoformat(),
        interactions=db.all("SELECT * FROM Interaction WHERE appointment_id = ? ORDER BY occurred_at DESC, rowid DESC",
                            (appointment_id,)),
        interview=interview_service.loads(appointment["interview"]),
        summary=interview_service.summary(db, appointment, state().now()) if linked else None,
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
        has_interview=interview_service.has_content(interview_service.loads(appointment["interview"])),
        offers_count=db.scalar("SELECT count(*) FROM Offer WHERE family_id = ?", (appointment["family_id"],)),
        followups_count=db.scalar("SELECT count(*) FROM FollowUp WHERE family_id = ? AND status = 'APERTO'",
                                  (appointment["family_id"],)),
        linked_followups=db.scalar("SELECT count(*) FROM FollowUp WHERE appointment_id = ?", (appointment_id,)),
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
    flash_ok("Collegamento cambiato. Offerte e follow-up della famiglia precedente non sono stati spostati; "
             "i follow-up nati da questo incontro si sono staccati dal colloquio.")
    return _to_detail(appointment_id, "#locale")


def _render_create_family(appointment, form, *, errors=None, duplicates=None, code: int = 200):
    return render_template(
        "appointments/create_family.html", active="appointments", a=appointment, form=form, errors=errors or [],
        duplicates=duplicates or [], contacts=appointment_service.contacts(appointment),
        years=lead_service.school_year_options(rome_today(state().now())), grades=catalog.GRADE_SUGGESTIONS,
        sources=catalog.CONTACT_SOURCES,
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
    next_followup = None
    try:
        values = appointment_service.parse_report(request.form, state().now())
        if request.form.get("next_action"):
            next_followup = followup_service.parse_followup(request.form, prefix="next_")
        appointment_service.save_report(get_db(), appointment_id, values, revision=_revision(), now=state().now(),
                                        next_followup=next_followup,
                                        followup_id=creation_id(request.form.get("next_new_id")))
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _to_detail(appointment_id, "#resoconto")
    flash_ok("Resoconto salvato" + (" con il prossimo passo." if next_followup else "."))
    return _to_detail(appointment_id, "#resoconto")


def _interview_url(appointment_id: str, section: str = "") -> str:
    return url_for("appointments.interview", appointment_id=appointment_id, sezione=section or None)


def _render_interview(ctx, form, *, errors=(), conflicts=(), code: int = 200, section: str = ""):
    db = get_db()
    page = interview_service.page_data(db, ctx, form)
    open_section = section if section in interview_service.SECTIONS else page.default_section
    today = rome_today(ctx.now)
    return render_template(
        "appointments/interview.html", active="appointments", a=ctx.appointment, linked=ctx.linked, ctx=ctx,
        family=ctx.family, form=form, page=page, errors=list(errors), conflicts=list(conflicts),
        open_section=open_section, catalog=catalog, followup_actions=FOLLOWUP_ACTIONS,
        years=lead_service.school_year_options(today), grades=catalog.GRADE_SUGGESTIONS,
        current_grades=catalog.CURRENT_GRADE_SUGGESTIONS, today_iso=today.isoformat(),
        other_rows=range(1, catalog.MAX_OTHER_LANGUAGES + 1), outcomes=appointment_service.OUTCOMES,
        discrepancy=appointment_service.date_discrepancy(ctx.appointment),
    ), code


def _go_destination(ctx, action) -> str:
    """URL della pagina V1 da aprire dopo il salvataggio, con ritorno al colloquio (salva e vai)."""
    family_id = ctx.appointment["family_id"]
    back = _interview_url(ctx.appointment["id"], action.section)
    if action.target == "offer_new":
        lead_id = None if action.arg in ("", "famiglia") else action.arg
        if lead_id is not None and ctx.lead(lead_id) is None:
            raise ValidationError("La richiesta scelta non appartiene a questa famiglia.")
        return url_for("offers.create", family_id=family_id, ambito=lead_id, next=back)
    if action.target == "offer_edit":
        try:
            offer = offer_service.get_offer(get_db(), action.arg, family_id)
        except NotFoundError:
            raise ValidationError("Proposta non trovata in questa famiglia: ricarica la pagina.") from None
        if offer["status"] != "BOZZA":
            raise ValidationError("La proposta è già stata comunicata: crea una nuova versione.")
        return url_for("offers.edit", family_id=family_id, offer_id=offer["id"], next=back)
    if action.target == "new_lead":
        return url_for("families.create_lead", family_id=family_id, next=back)
    return url_for("families.edit", family_id=family_id, next=back)


@bp.route("/<appointment_id>/colloquio", methods=["GET", "POST"])
def interview(appointment_id: str):
    """Pagina del colloquio: Prepara incontro e sezioni A–K in un solo modulo (v1.1, SPEC §14)."""
    _appointment_or_404(appointment_id)
    db = get_db()
    now = state().now()
    ctx = interview_service.load_context(db, appointment_id, now)
    if not ctx.linked:
        if request.method == "POST":
            flash_error("Collega prima l'appuntamento a una famiglia: poi si conduce il colloquio.")
            return _to_detail(appointment_id, "#locale")
        return render_template("appointments/interview.html", active="appointments", a=ctx.appointment,
                               linked=False, ctx=ctx), 200
    if request.method == "GET":
        return _render_interview(ctx, interview_service.initial_form(db, ctx), section=request.args.get("sezione", ""))
    form = request.form
    action = None
    try:
        action = interview_service.parse_action(form.get("action"))
        destination = _go_destination(ctx, action) if action.kind == "go" else None
        submission = interview_service.parse_submission(form, ctx)
        result = interview_service.save(db, appointment_id, submission, action, now)
    except interview_service.ConflictError as exc:
        return _render_interview(ctx, form, conflicts=exc.conflicts, code=409,
                                 section=action.section if action else "")
    except StaleWriteError as exc:
        return _render_interview(ctx, form, errors=[exc.message], code=409, section=action.section if action else "")
    except ValidationError as exc:
        section = interview_service.section_of(exc.field) if exc.field else (action.section if action else "")
        return _render_interview(ctx, form, errors=[exc.message], code=422, section=section)
    if result.kept_current:
        flash_warning("Mantenuti i valori attuali per: " + ", ".join(result.kept_current) + ".")
    if result.repeated_conclusion:
        flash_warning("Il colloquio risultava già concluso con questo modulo: nulla è cambiato.")
        return _to_detail(appointment_id, "#colloquio")
    if result.concluded:
        outcome = request.form.get("outcome")
        steps = len(result.followups)
        flash_ok(f"Colloquio concluso: {label(outcome)}."
                 + (f" {steps} {'prossimo passo creato' if steps == 1 else 'prossimi passi creati'}." if steps else ""))
        return _to_detail(appointment_id, "#colloquio")
    if action.kind == "offer_communicate":
        offer = offer_service.get_offer(db, result.offer_id)
        if result.offer_changed:
            flash_ok(f"Colloquio salvato. Proposta v{offer['version_no']} segnata come comunicata il "
                     f"{format_datetime(offer['communicated_at'])}: il contenuto ora è congelato.")
        else:
            flash_warning(f"La proposta v{offer['version_no']} risultava già comunicata il "
                          f"{format_datetime(offer['communicated_at'])}: nulla è cambiato.")
        return redirect(_interview_url(appointment_id, "i"))
    if action.kind == "offer_version":
        if result.offer_changed:
            flash_ok("Colloquio salvato. Nuova versione creata come bozza: la proposta comunicata resta la corrente "
                     "finché non comunichi questa.")
        else:
            flash_warning("Esiste già una bozza in questo ambito: modifica quella.")
        return redirect(url_for("offers.edit", family_id=ctx.appointment["family_id"], offer_id=result.offer_id,
                                next=_interview_url(appointment_id, "i")))
    if destination is not None:
        if result.written:
            flash_ok("Colloquio salvato.")
        return redirect(destination)
    flash_ok("Colloquio salvato." if result.written else "Nessuna modifica da salvare.")
    return redirect(_interview_url(appointment_id, action.section))


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
        except Exception:
            app_state.settings.update(lambda data: data.__setitem__("last_import", calendar_import.failed_record(
                stamp(now), "Errore imprevisto durante l'aggiornamento: nessun dato è stato cancellato.",
                data.get("last_import"))))
            raise
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
