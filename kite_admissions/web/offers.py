"""Proposte economiche nella scheda famiglia (SPEC §6) e dal colloquio (v1.1, SPEC §14)."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, redirect, render_template, request, url_for

from .. import catalog
from ..schema import PERIODICITIES
from ..services import families as family_service
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
from ..timeutil import format_datetime
from . import flash_error, flash_ok, flash_warning, get_db, safe_next, state

bp = Blueprint("offers", __name__, url_prefix="/famiglie/<family_id>/offerte")


def _family(family_id: str):
    if not valid_uuid(family_id):
        abort(404)
    try:
        return family_service.get_family(get_db(), family_id)
    except NotFoundError:
        abort(404)


def _offer(family_id: str, offer_id: str):
    if not valid_uuid(offer_id):
        abort(404)
    try:
        return offer_service.get_offer(get_db(), offer_id, family_id)
    except NotFoundError:
        abort(404)


def _revision() -> int:
    try:
        return int(request.form.get("revision", ""))
    except ValueError:
        raise StaleWriteError("Modulo incompleto: ricarica la pagina.") from None


def _next() -> str:
    """Pagina da cui si è arrivati (per esempio il colloquio), solo se interna."""
    return safe_next(request.form.get("next") or request.args.get("next"), "")


def _to_family(family_id: str, offer_id: str | None = None):
    anchor = f"#offerta-{offer_id}" if offer_id else "#offerte"
    return redirect(_next() or url_for("families.detail", family_id=family_id) + anchor)


def _render_form(family, form: Any, *, offer=None, scope_lead=None, errors=None, code: int = 200):
    db = get_db()
    back = _next() or url_for("families.detail", family_id=family["id"]) + "#offerte"
    return render_template(
        "family/offer_form.html", active="families", family=family, offer=offer, form=form, errors=errors or [],
        scope_lead=scope_lead, leads=lead_service.family_leads(db, family["id"]),
        periodicities=PERIODICITIES, service_rows=range(1, offer_service.MAX_SERVICES + 1),
        reduction_rows=range(1, catalog.MAX_REDUCTIONS + 1), reasons=catalog.REDUCTION_REASONS,
        authorizers=catalog.AUTHORIZER_SUGGESTIONS, back=back, next=_next(),
        warning=offer_service.consistency_warning(offer) if offer is not None else None,
        previous_authorization=offer_service.previous_authorization(db, offer) if offer is not None else None,
    ), code


@bp.route("/nuova", methods=["GET", "POST"])
def create(family_id: str):
    family = _family(family_id)
    if request.method == "GET":
        scope = request.args.get("ambito", "")
        form = {"new_id": new_id(), "student_lead_id": scope if valid_uuid(scope) else "", "periodicity": "ANNUALE"}
        return _render_form(family, form)
    form = request.form
    lead_id = form.get("student_lead_id") or None
    try:
        values = offer_service.parse_offer(form)
        offer_id = offer_service.create_offer(get_db(), family_id, lead_id, values,
                                              offer_id=creation_id(form.get("new_id")), now=state().now())
    except ValidationError as exc:
        return _render_form(family, form, errors=[exc.message], code=422)
    except offer_service.DraftExists as exists:
        flash_warning("In questo ambito esiste già una bozza: modifica quella.")
        return redirect(url_for("offers.edit", family_id=family_id, offer_id=exists.draft_id, next=_next() or None))
    except AlreadySavedError as saved:
        flash_warning("La proposta era già stata salvata.")
        return _to_family(family_id, saved.record_id)
    flash_ok("Bozza della proposta salvata. Quando l'hai comunicata alla famiglia, premi «Segna come comunicata».")
    return _to_family(family_id, offer_id)


@bp.route("/<offer_id>/modifica", methods=["GET", "POST"])
def edit(family_id: str, offer_id: str):
    family = _family(family_id)
    offer = _offer(family_id, offer_id)
    scope_lead = lead_service.get_lead(get_db(), offer["student_lead_id"]) if offer["student_lead_id"] else None
    if offer["status"] != "BOZZA":
        flash_warning("Proposta comunicata: il contenuto è congelato. Per cambiarla crea una nuova versione.")
        return _to_family(family_id, offer_id)
    if request.method == "GET":
        return _render_form(family, offer_service.form_from_offer(offer), offer=offer, scope_lead=scope_lead)
    form = request.form
    try:
        values = offer_service.parse_offer(form, previous=offer)
        offer_service.update_draft(get_db(), offer_id, family_id, values, revision=_revision(), now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        code = 409 if isinstance(exc, StaleWriteError) else 422
        return _render_form(family, form, offer=offer, scope_lead=scope_lead, errors=[exc.message], code=code)
    flash_ok("Bozza aggiornata.")
    return _to_family(family_id, offer_id)


@bp.post("/<offer_id>/comunica")
def communicate(family_id: str, offer_id: str):
    _family(family_id)
    _offer(family_id, offer_id)
    channel = clean(request.form.get("channel"), 40)
    try:
        changed = offer_service.communicate(get_db(), offer_id, family_id, revision=_revision(), channel=channel,
                                            now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _to_family(family_id, offer_id)
    offer = _offer(family_id, offer_id)
    if changed:
        flash_ok(f"Proposta v{offer['version_no']} segnata come comunicata il "
                 f"{format_datetime(offer['communicated_at'])}. Il contenuto ora è congelato.")
    else:
        flash_warning(f"La proposta v{offer['version_no']} risultava già comunicata il "
                      f"{format_datetime(offer['communicated_at'])}: nulla è cambiato.")
    return _to_family(family_id, offer_id)


@bp.post("/<offer_id>/nuova-versione")
def new_version(family_id: str, offer_id: str):
    _family(family_id)
    _offer(family_id, offer_id)
    back = _next() or None
    try:
        created = offer_service.create_new_version(get_db(), offer_id, offer_id=creation_id(request.form.get("new_id")),
                                                   now=state().now())
    except ValidationError as exc:
        flash_error(exc.message)
        return _to_family(family_id, offer_id)
    except offer_service.DraftExists as exists:
        flash_warning("Esiste già una bozza in questo ambito: modifica quella.")
        return redirect(url_for("offers.edit", family_id=family_id, offer_id=exists.draft_id, next=back))
    except AlreadySavedError as saved:
        return redirect(url_for("offers.edit", family_id=family_id, offer_id=saved.record_id, next=back))
    flash_ok("Nuova versione creata come bozza: la proposta comunicata resta la corrente finché non comunichi questa.")
    return redirect(url_for("offers.edit", family_id=family_id, offer_id=created, next=back))


@bp.post("/<offer_id>/ritira")
def withdraw(family_id: str, offer_id: str):
    _family(family_id)
    _offer(family_id, offer_id)
    try:
        changed = offer_service.withdraw(get_db(), offer_id, family_id, reason=clean(request.form.get("reason"), 500),
                                         revision=_revision(), now=state().now())
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _to_family(family_id, offer_id)
    if changed:
        flash_ok("Proposta ritirata: resta consultabile con data e motivo. Nessuna versione precedente torna valida.")
    return _to_family(family_id, offer_id)


@bp.post("/<offer_id>/elimina")
def delete(family_id: str, offer_id: str):
    _family(family_id)
    _offer(family_id, offer_id)
    if request.form.get("confirm") != "1":
        flash_warning("Spunta la conferma per eliminare la bozza.")
        return _to_family(family_id, offer_id)
    try:
        offer_service.delete_draft(get_db(), offer_id, family_id, revision=_revision())
    except (ValidationError, StaleWriteError) as exc:
        flash_error(exc.message)
        return _to_family(family_id, offer_id)
    flash_ok("Bozza eliminata.")
    return _to_family(family_id)
