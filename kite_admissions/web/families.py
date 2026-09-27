"""Viste Famiglie e Scheda famiglia (SPEC §3)."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..schema import FOLLOWUP_ACTIONS, MANUAL_INTERACTIONS
from ..services import appointments as appointment_service
from ..services import backups
from ..services import families as family_service
from ..services import followups as followup_service
from ..services import today as today_service
from ..services import interactions as interaction_service
from ..services import leads as lead_service
from ..services import offers as offer_service
from ..services import timeline as timeline_service
from ..services.common import (
    AlreadySavedError,
    NotFoundError,
    StaleWriteError,
    ValidationError,
    creation_id,
    new_id,
    stamp,
    valid_uuid,
)
from ..timeutil import rome_today, utc_iso_to_local_input
from . import flash_error, flash_ok, flash_warning, get_db, state

bp = Blueprint("families", __name__, url_prefix="/famiglie")


def _family_or_404(family_id: str):
    if not valid_uuid(family_id):
        abort(404)
    try:
        return family_service.get_family(get_db(), family_id)
    except NotFoundError:
        abort(404)


def _lead_or_404(family_id: str, lead_id: str):
    if not valid_uuid(lead_id):
        abort(404)
    try:
        return lead_service.get_lead(get_db(), lead_id, family_id)
    except NotFoundError:
        abort(404)


def _revision() -> int:
    try:
        return int(request.form.get("revision", ""))
    except ValueError:
        raise StaleWriteError("Modulo incompleto: ricarica la pagina.") from None


def _year_options() -> list[str]:
    return lead_service.school_year_options(rome_today(state().now()))


@bp.get("/")
def index():
    db = get_db()
    query = request.args.get("q", "").strip()
    school_year = request.args.get("anno") or None
    status = request.args.get("stato") or None
    view = request.args.get("vista", "attive")
    if view not in ("attive", "archiviate", "tutte"):
        view = "attive"
    if status and status not in lead_service.STATUSES:
        status = None
    rows = family_service.search_families(db, query=query, school_year=school_year, status=status, archived=view)
    leads_map = family_service.leads_by_family(db, [row["id"] for row in rows])
    years = [row[0] for row in db.all(
        "SELECT DISTINCT school_year FROM StudentLead WHERE school_year IS NOT NULL ORDER BY school_year")]
    family_ids = [row["id"] for row in rows]
    return render_template(
        "family/index.html", active="families", families=rows, leads_map=leads_map, query=query,
        school_year=school_year, status=status, view=view, years=years,
        next_steps=today_service.next_steps(db, state().now(), family_ids),
    )


def _render_family_form(form: Any, *, family=None, errors=None, duplicates=None, code: int = 200):
    return render_template(
        "family/form.html", active="families", form=form, family=family, errors=errors or [],
        duplicates=duplicates or [], sources=family_service.CONTACT_SOURCES, years=_year_options(),
        grades=lead_service.GRADE_SUGGESTIONS,
    ), code


@bp.route("/nuova", methods=["GET", "POST"])
def create():
    if request.method == "GET":
        form = {"new_id": new_id(), "first_contact_on": rome_today(state().now()).isoformat(),
                "display_name": request.args.get("etichetta", "")}
        return _render_family_form(form)
    form = request.form
    try:
        values = family_service.parse_family(form)
        first_lead = lead_service.parse_lead(form, prefix="lead_") if form.get("lead_display_name", "").strip() else None
        family_id = family_service.create_family(
            get_db(), values, now=state().now(), family_id=creation_id(form.get("new_id")),
            confirm_distinct=form.get("confirm_distinct") == "1", first_lead=first_lead,
        )
    except ValidationError as exc:
        return _render_family_form(form, errors=[exc.message], code=422)
    except family_service.DuplicateWarning as warning:
        return _render_family_form(form, duplicates=warning.matches)
    except AlreadySavedError as saved:
        flash_warning("La famiglia era già stata salvata: nessun doppione creato.")
        return redirect(url_for("families.detail", family_id=saved.record_id))
    flash_ok("Famiglia creata.")
    return redirect(url_for("families.detail", family_id=family_id))


@bp.get("/<family_id>")
def detail(family_id: str):
    family = _family_or_404(family_id)
    db = get_db()
    now = state().now()
    appointments = appointment_service.family_appointments(db, family_id)
    return render_template(
        "family/detail.html", active="families", family=family,
        leads=lead_service.family_leads(db, family_id), has_contact=family_service.has_contact(family),
        years=_year_options(), grades=lead_service.GRADE_SUGGESTIONS, new_lead_id=new_id(),
        today=rome_today(now).isoformat(),
        appointments=appointments,
        needs_report={row["id"] for row in appointments if appointment_service.needs_report(row, now)},
        timeline=timeline_service.family_timeline(db, family_id),
        offer_scopes=offer_service.family_offer_scopes(db, family_id),
        followups=followup_service.family_followups(db, family_id),
        followup_actions=FOLLOWUP_ACTIONS,
        next_step=today_service.next_steps(db, now, [family_id])[family_id],
        new_followup_id=new_id(),
        new_note_id=new_id(), note_types=MANUAL_INTERACTIONS,
        now_local=utc_iso_to_local_input(stamp(now)),
    )


def _note_or_404(family_id: str, note_id: str):
    if not valid_uuid(note_id):
        abort(404)
    try:
        return interaction_service.get_note(get_db(), family_id, note_id)
    except NotFoundError:
        abort(404)


@bp.post("/<family_id>/note")
def create_note(family_id: str):
    _family_or_404(family_id)
    try:
        values = interaction_service.parse_note(request.form, state().now())
        interaction_service.create_note(get_db(), family_id, values, note_id=creation_id(request.form.get("new_id")),
                                        now=state().now())
    except ValidationError as exc:
        flash_error(exc.message)
        return redirect(url_for("families.detail", family_id=family_id) + "#note")
    except AlreadySavedError:
        flash_warning("La nota era già stata salvata.")
        return redirect(url_for("families.detail", family_id=family_id) + "#cronologia")
    flash_ok("Annotazione aggiunta alla cronologia.")
    return redirect(url_for("families.detail", family_id=family_id) + "#cronologia")


@bp.route("/<family_id>/note/<note_id>/modifica", methods=["GET", "POST"])
def edit_note(family_id: str, note_id: str):
    family = _family_or_404(family_id)
    note = _note_or_404(family_id, note_id)
    leads = lead_service.family_leads(get_db(), family_id)
    if request.method == "GET":
        form = dict(note)
        form["occurred_at"] = utc_iso_to_local_input(note["occurred_at"])
        return render_template("family/note_form.html", active="families", family=family, note=note, form=form,
                               leads=leads, note_types=MANUAL_INTERACTIONS, errors=[])
    form = request.form
    try:
        values = interaction_service.parse_note(form, state().now())
        interaction_service.update_note(get_db(), family_id, note_id, values, revision=_revision(), now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        code = 409 if isinstance(exc, StaleWriteError) else 422
        return render_template("family/note_form.html", active="families", family=family, note=note, form=form,
                               leads=leads, note_types=MANUAL_INTERACTIONS, errors=[exc.message]), code
    flash_ok("Annotazione aggiornata.")
    return redirect(url_for("families.detail", family_id=family_id) + "#cronologia")


@bp.post("/<family_id>/note/<note_id>/elimina")
def delete_note(family_id: str, note_id: str):
    _family_or_404(family_id)
    _note_or_404(family_id, note_id)
    if request.form.get("confirm") != "1":
        flash_warning("Spunta la conferma per eliminare l'annotazione.")
        return redirect(url_for("families.edit_note", family_id=family_id, note_id=note_id))
    interaction_service.delete_note(get_db(), family_id, note_id)
    flash_ok("Annotazione eliminata.")
    return redirect(url_for("families.detail", family_id=family_id) + "#cronologia")


@bp.route("/<family_id>/modifica", methods=["GET", "POST"])
def edit(family_id: str):
    family = _family_or_404(family_id)
    if request.method == "GET":
        return _render_family_form(dict(family), family=family)
    form = request.form
    try:
        values = family_service.parse_family(form)
        family_service.update_family(get_db(), family_id, values, revision=_revision(), now=state().now(),
                                     confirm_distinct=form.get("confirm_distinct") == "1")
    except ValidationError as exc:
        return _render_family_form(form, family=family, errors=[exc.message], code=422)
    except family_service.DuplicateWarning as warning:
        return _render_family_form(form, family=family, duplicates=warning.matches)
    except StaleWriteError as exc:
        return _render_family_form(form, family=family, errors=[exc.message], code=409)
    flash_ok("Dati della famiglia aggiornati.")
    return redirect(url_for("families.detail", family_id=family_id))


@bp.post("/<family_id>/archivia")
def archive(family_id: str):
    _family_or_404(family_id)
    if family_service.set_archived(get_db(), family_id, True, now=state().now()):
        flash_ok("Famiglia archiviata: resta consultabile dal filtro «Archiviate».")
    return redirect(url_for("families.detail", family_id=family_id))


@bp.route("/<family_id>/elimina", methods=["GET", "POST"])
def delete(family_id: str):
    family = _family_or_404(family_id)
    db = get_db()
    counts = family_service.dossier_counts(db, family_id)
    if request.method == "GET":
        return render_template("family/delete.html", active="families", family=family, counts=counts, errors=[])
    typed = request.form.get("confirm_label", "")
    if typed.strip() != family["display_name"].strip():
        return render_template("family/delete.html", active="families", family=family, counts=counts,
                               errors=["Per confermare digita esattamente l'etichetta della famiglia."]), 422
    try:
        backup = state().preop_backup("eliminazione")
    except (backups.BackupError, OSError) as exc:
        return render_template("family/delete.html", active="families", family=family, counts=counts,
                               errors=[f"Copia di sicurezza non riuscita: eliminazione annullata ({exc})."]), 500
    removed = family_service.delete_family(db, family_id, typed_label=typed, now=state().now())
    return render_template("family/deleted.html", active="families", label=family["display_name"], counts=removed,
                           backup_name=backup.name)


@bp.post("/<family_id>/riattiva")
def unarchive(family_id: str):
    _family_or_404(family_id)
    if family_service.set_archived(get_db(), family_id, False, now=state().now()):
        flash_ok("Famiglia riportata tra le attive.")
    return redirect(url_for("families.detail", family_id=family_id))


def _render_lead_form(family, form: Any, *, lead=None, errors=None, similar=None, code: int = 200):
    return render_template(
        "family/lead_form.html", active="families", family=family, lead=lead, form=form,
        errors=errors or [], similar=similar or [], years=_year_options(), grades=lead_service.GRADE_SUGGESTIONS,
    ), code


@bp.route("/<family_id>/richieste/nuova", methods=["GET", "POST"])
def create_lead(family_id: str):
    family = _family_or_404(family_id)
    if request.method == "GET":
        form: dict[str, Any] = {"new_id": new_id()}
        source_id = request.args.get("da")
        if source_id:
            form.update(lead_service.copy_for_new_year(_lead_or_404(family_id, source_id)))
        return _render_lead_form(family, form)
    form = request.form
    try:
        values = lead_service.parse_lead(form)
        lead_service.create_lead(get_db(), family_id, values, now=state().now(),
                                 lead_id=creation_id(form.get("new_id")),
                                 confirm_similar=form.get("confirm_similar") == "1")
    except ValidationError as exc:
        return _render_lead_form(family, form, errors=[exc.message], code=422)
    except lead_service.SimilarLeadWarning as warning:
        return _render_lead_form(family, form, similar=warning.leads)
    except AlreadySavedError:
        flash_warning("La richiesta era già stata salvata: nessun doppione creato.")
        return redirect(url_for("families.detail", family_id=family_id))
    flash_ok("Richiesta aggiunta.")
    return redirect(url_for("families.detail", family_id=family_id) + "#richieste")


@bp.route("/<family_id>/richieste/<lead_id>/modifica", methods=["GET", "POST"])
def edit_lead(family_id: str, lead_id: str):
    family = _family_or_404(family_id)
    lead = _lead_or_404(family_id, lead_id)
    if request.method == "GET":
        return _render_lead_form(family, dict(lead), lead=lead)
    form = request.form
    try:
        values = lead_service.parse_lead(form)
        review_on = family_service.parse_date_field(form, "review_on", "Data di riesame")
        lead_service.update_lead(get_db(), lead_id, family_id, values, revision=_revision(), now=state().now(),
                                 review_on=review_on)
    except ValidationError as exc:
        return _render_lead_form(family, form, lead=lead, errors=[exc.message], code=422)
    except StaleWriteError as exc:
        return _render_lead_form(family, form, lead=lead, errors=[exc.message], code=409)
    flash_ok("Richiesta aggiornata.")
    return redirect(url_for("families.detail", family_id=family_id) + "#richieste")


@bp.route("/<family_id>/richieste/<lead_id>/stato", methods=["GET", "POST"])
def lead_status(family_id: str, lead_id: str):
    family = _family_or_404(family_id)
    lead = _lead_or_404(family_id, lead_id)
    today = rome_today(state().now()).isoformat()
    if request.method == "GET":
        return render_template("family/lead_status.html", active="families", family=family, lead=lead,
                               form={"closed_on": today}, errors=[], today=today)
    form = request.form
    try:
        change = lead_service.parse_status_change(form)
        result = lead_service.change_status(get_db(), lead_id, family_id, change, revision=_revision(),
                                            now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        code = 409 if isinstance(exc, StaleWriteError) else 422
        return render_template("family/lead_status.html", active="families", family=family,
                               lead=_lead_or_404(family_id, lead_id), form=form, errors=[exc.message],
                               today=today), code
    if result is None:
        flash_warning("Lo stato scelto è già quello attuale: nessuna modifica.")
    else:
        flash_ok("Stato della richiesta aggiornato.")
    return redirect(url_for("families.detail", family_id=family_id) + "#richieste")
