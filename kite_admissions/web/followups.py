"""Follow-up nella scheda famiglia (SPEC §3, §7)."""

from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..schema import FOLLOWUP_ACTIONS
from ..services import families as family_service
from ..services import followups as followup_service
from ..services import leads as lead_service
from ..services.common import (
    AlreadySavedError,
    NotFoundError,
    StaleWriteError,
    ValidationError,
    clean,
    creation_id,
    valid_uuid,
)
from . import flash_error, flash_ok, flash_warning, get_db, safe_next, state

bp = Blueprint("followups", __name__, url_prefix="/famiglie/<family_id>/follow-up")


def _family(family_id: str):
    if not valid_uuid(family_id):
        abort(404)
    try:
        return family_service.get_family(get_db(), family_id)
    except NotFoundError:
        abort(404)


def _followup(family_id: str, followup_id: str):
    if not valid_uuid(followup_id):
        abort(404)
    try:
        return followup_service.get_followup(get_db(), followup_id, family_id)
    except NotFoundError:
        abort(404)


def _revision() -> int:
    try:
        return int(request.form.get("revision", ""))
    except ValueError:
        raise StaleWriteError("Modulo incompleto: ricarica la pagina.") from None


def _back(family_id: str):
    return redirect(safe_next(request.form.get("next"), url_for("families.detail", family_id=family_id) + "#follow-up"))


@bp.post("/nuovo")
def create(family_id: str):
    _family(family_id)
    try:
        values = followup_service.parse_followup(request.form)
        followup_service.create_followup(get_db(), family_id, values,
                                         followup_id=creation_id(request.form.get("new_id")), now=state().now())
    except ValidationError as exc:
        flash_error(exc.message)
        return _back(family_id)
    except AlreadySavedError:
        flash_warning("Il follow-up era già stato salvato.")
        return _back(family_id)
    flash_ok("Follow-up aggiunto.")
    return _back(family_id)


@bp.post("/<followup_id>/completa")
def complete(family_id: str, followup_id: str):
    return _close(family_id, followup_id, "COMPLETATO")


@bp.post("/<followup_id>/annulla")
def cancel(family_id: str, followup_id: str):
    return _close(family_id, followup_id, "ANNULLATO")


def _close(family_id: str, followup_id: str, status: str):
    _family(family_id)
    _followup(family_id, followup_id)
    try:
        changed = followup_service.close_followup(get_db(), followup_id, family_id, status=status,
                                                  outcome=clean(request.form.get("outcome"), 1000),
                                                  revision=_revision(), now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _back(family_id)
    if changed:
        flash_ok("Follow-up completato." if status == "COMPLETATO" else "Follow-up annullato.")
    return _back(family_id)


@bp.route("/<followup_id>/modifica", methods=["GET", "POST"])
def edit(family_id: str, followup_id: str):
    family = _family(family_id)
    followup = _followup(family_id, followup_id)
    leads = lead_service.family_leads(get_db(), family_id)
    if request.method == "GET":
        return render_template("family/followup_form.html", active="families", family=family, followup=followup,
                               form=dict(followup), leads=leads, actions=FOLLOWUP_ACTIONS, errors=[])
    form = request.form
    try:
        values = followup_service.parse_followup(form)
        followup_service.update_followup(get_db(), followup_id, family_id, values, revision=_revision(),
                                         now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        code = 409 if isinstance(exc, StaleWriteError) else 422
        return render_template("family/followup_form.html", active="families", family=family, followup=followup,
                               form=form, leads=leads, actions=FOLLOWUP_ACTIONS, errors=[exc.message]), code
    flash_ok("Follow-up aggiornato.")
    return redirect(url_for("families.detail", family_id=family_id) + "#follow-up")
