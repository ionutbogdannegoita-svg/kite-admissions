"""Colloquio di ammissione (v1.1, SPEC §14): contenuto strutturato nell'appuntamento.

Un incontro = un evento Calendar = una riga Appointment = un colloquio (DEC-024). La parte
strutturata vive in `Appointment.interview` (JSON); preparazione, esito, data effettiva, sintesi e
nota interna restano nei campi V1. I dati dell'alunno stanno nella sua richiesta, la fonte nella
famiglia, l'economia nell'offerta, il prossimo passo nel follow-up: il colloquio non li copia.

La pagina è un modulo unico su più record (colloquio, alunni dell'ambito, fonte della famiglia).
Per ogni record il modulo porta la revisione e l'impronta dei valori mostrati: un record non
toccato non si riscrive, uno toccato si scrive solo se nessuno l'ha cambiato altrove; altrimenti
nulla si salva e si chiede una scelta esplicita (DEC-030). «Salva» non registra mai esito,
follow-up o chiusure: lo fa soltanto «Concludi colloquio», nella stessa transazione (DEC-029).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from werkzeug.datastructures import MultiDict

from .. import catalog
from ..db import Database
from ..labels import euro, label
from ..schema import FOLLOWUP_ACTIONS, MANUAL_INTERACTIONS
from ..timeutil import (
    format_date,
    format_datetime,
    from_iso,
    local_input_to_utc,
    rome_date_of,
    rome_today,
    utc_iso_to_local_input,
)
from . import appointments as appointment_service
from . import families as family_service
from . import followups as followup_service
from . import leads as lead_service
from . import offers as offer_service
from .common import (
    AlreadySavedError,
    NotFoundError,
    StaleWriteError,
    ValidationError,
    clean,
    creation_id,
    new_id,
    stamp,
    valid_uuid,
)

FORMAT = 1

TEXT_LIMITS = {
    "motivations_note": 600,
    "activities": 200,
    "schedule": 200,
    "constraints": 400,
    "q": 300,
    "a": 500,
}

SECTIONS = ("prepara", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k")
CONTENT_KEYS = ("kind", "attendees", "motivations", "motivations_note", "needs", "presented", "questions",
                "assessment")
STEP_SLOTS = tuple(range(1, catalog.MAX_STEPS + 1))
STEP_ACTIONS = FOLLOWUP_ACTIONS + (catalog.WAIT_ACTION,)
OUTCOMES = appointment_service.OUTCOMES
NOTE_LIMIT = 2000
MEETING_LIMITS = {"preparation": 8000, "local_observations": 4000, "visit_report": 8000}


def fingerprint(values: Any) -> str:
    """Impronta stabile di un insieme di valori: dice se un record è stato toccato nel modulo."""
    text = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def loads(raw: str | None) -> dict[str, Any]:
    """Colloquio salvato; un contenuto illeggibile vale come colloquio vuoto (mai un errore di pagina)."""
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def text_value(value: Any, limit: int, field: str, what: str) -> str | None:
    try:
        return clean(value, limit)
    except ValidationError:
        raise ValidationError(f"{what}: testo troppo lungo (massimo {limit} caratteri).", field) from None


def bool_value(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "si", "sì", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValidationError("Valore sì/no non valido: ricarica la pagina.")


def date_value(value: Any, field: str, what: str) -> str | None:
    text = clean(value, 20) if value is not None else None
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValidationError(f"{what}: data non valida.", field) from None


def clean_interview(data: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Contenuto del colloquio validato con le liste del catalogo; chiavi vuote omesse.

    I codici tolti dal catalogo restano ammessi solo se erano già salvati (`previous`).
    """
    previous = previous or {}
    result: dict[str, Any] = {"format": FORMAT}

    kind = catalog.clean_code(data.get("kind"), catalog.MEETING_KINDS, field="kind", what="Tipo di incontro",
                              previous=previous.get("kind"))
    if kind:
        result["kind"] = kind
    attendees = catalog.clean_codes(data.get("attendees"), catalog.ATTENDEE_RELATIONS, field="attendees",
                                    what="Presenti", previous=previous.get("attendees"))
    if attendees:
        result["attendees"] = attendees

    motivations = catalog.clean_codes(data.get("motivations"), catalog.MOTIVATIONS, field="motivations",
                                      what="Motivazioni", previous=previous.get("motivations"))
    if motivations:
        result["motivations"] = motivations
    note = text_value(data.get("motivations_note"), TEXT_LIMITS["motivations_note"], "motivations_note",
                      "Nota sulle motivazioni")
    if note:
        result["motivations_note"] = note

    needs_in = data.get("needs") or {}
    needs_before = previous.get("needs") or {}
    needs: dict[str, Any] = {}
    services = catalog.clean_codes(needs_in.get("services"), catalog.SERVICES_OF_INTEREST, field="services",
                                   what="Servizi di interesse", previous=needs_before.get("services"))
    if services:
        needs["services"] = services
    for key, what in (("activities", "Attività di interesse"), ("schedule", "Orario desiderato"),
                      ("constraints", "Vincoli di orario e logistica")):
        value = text_value(needs_in.get(key), TEXT_LIMITS[key], key, what)
        if value:
            needs[key] = value
    siblings = bool_value(needs_in.get("siblings_enrolled"))
    if siblings is not None:
        needs["siblings_enrolled"] = siblings
    start = date_value(needs_in.get("desired_start"), "desired_start", "Data auspicata di ingresso")
    if start:
        needs["desired_start"] = start
    if needs:
        result["needs"] = needs

    presented = catalog.clean_codes(data.get("presented"), catalog.PRESENTATION_TOPICS, field="presented",
                                    what="Argomenti presentati", previous=previous.get("presented"))
    if presented:
        result["presented"] = presented

    questions = []
    for row in data.get("questions") or []:
        question = text_value(row.get("q"), TEXT_LIMITS["q"], "questions", "Domanda")
        answer = text_value(row.get("a"), TEXT_LIMITS["a"], "questions", "Risposta")
        if not question and not answer:
            continue
        if not question:
            raise ValidationError("Una risposta non ha la sua domanda: scrivi la domanda o togli la risposta.",
                                  "questions")
        questions.append({"q": question, "a": answer or "", "verify": bool(row.get("verify"))})
    if len(questions) > catalog.MAX_QUESTIONS:
        raise ValidationError(f"Al massimo {catalog.MAX_QUESTIONS} domande per colloquio.", "questions")
    if questions:
        result["questions"] = questions

    assessment_in = data.get("assessment") or {}
    assessment_before = previous.get("assessment") or {}
    assessment: dict[str, str] = {}
    for key, entries, what in (("interest", catalog.INTEREST_LEVELS, "Interesse percepito"),
                               ("timing", catalog.DECISION_TIMINGS, "Tempistica della decisione"),
                               ("driver", catalog.DRIVERS, "Principale driver"),
                               ("obstacle", catalog.OBSTACLES, "Principale ostacolo")):
        value = catalog.clean_code(assessment_in.get(key), entries, field=key, what=what,
                                   previous=assessment_before.get(key))
        if value:
            assessment[key] = value
    if assessment:
        result["assessment"] = assessment
    return result


def has_content(interview: Mapping[str, Any]) -> bool:
    """Il colloquio contiene qualcosa oltre al formato e alla data di salvataggio."""
    return any(key not in ("format", "saved_at") for key in interview)


def content_of(interview: Mapping[str, Any]) -> dict[str, Any]:
    """Parte del colloquio compilata nelle sezioni A–J (senza bozza di chiusura e dati tecnici)."""
    return {key: interview[key] for key in CONTENT_KEYS if key in interview}


# --- Contesto -------------------------------------------------------------------------------

@dataclass
class Context:
    """Appuntamento con famiglia e richieste, letti nello stesso momento."""

    appointment: sqlite3.Row
    family: sqlite3.Row | None
    leads: list[sqlite3.Row]
    now: datetime

    @property
    def interview(self) -> dict[str, Any]:
        return loads(self.appointment["interview"])

    @property
    def linked(self) -> bool:
        return self.family is not None

    @property
    def phase(self) -> str:
        """«done» dopo una visita svolta (correzioni); «open» finché la conclusione è da fare."""
        return "done" if self.appointment["visit_outcome"] == "SVOLTA" else "open"

    @property
    def today(self) -> date:
        return rome_today(self.now)

    def lead(self, lead_id: str | None) -> sqlite3.Row | None:
        return next((row for row in self.leads if row["id"] == lead_id), None)


def load_context(db: Database, appointment_id: str, now: datetime) -> Context:
    appointment = appointment_service.get_appointment(db, appointment_id)
    family = family_service.get_family(db, appointment["family_id"]) if appointment["family_id"] else None
    leads = lead_service.family_leads(db, appointment["family_id"]) if family is not None else []
    return Context(appointment, family, leads, now)


def scope_leads(leads: Iterable[sqlite3.Row], lead_id: str | None) -> list[sqlite3.Row]:
    """Alunni dell'ambito: la richiesta dell'incontro, oppure ogni richiesta aperta della famiglia."""
    if lead_id:
        return [row for row in leads if row["id"] == lead_id]
    return [row for row in leads if row["status"] in lead_service.OPEN_STATUSES]


def other_open_followups(db: Database, family_id: str, appointment_id: str) -> list[sqlite3.Row]:
    """Follow-up aperti della famiglia non nati da questo incontro: si possono completare alla chiusura."""
    return db.all(
        "SELECT f.*, s.display_name AS lead_name FROM FollowUp f LEFT JOIN StudentLead s ON s.id = f.student_lead_id "
        "WHERE f.family_id = ? AND f.status = 'APERTO' AND f.appointment_id IS NOT ? ORDER BY f.due_on, f.created_at",
        (family_id, appointment_id))


def meeting_followups(db: Database, appointment_id: str) -> list[sqlite3.Row]:
    """Prossimi passi nati da questo incontro (FollowUp.appointment_id), con lo stato attuale."""
    return db.all(
        "SELECT f.*, s.display_name AS lead_name FROM FollowUp f LEFT JOIN StudentLead s ON s.id = f.student_lead_id "
        "WHERE f.appointment_id = ? ORDER BY f.created_at, f.rowid", (appointment_id,))


@dataclass
class RenderInfo:
    """Cosa la pagina mostra oltre ai valori: caselle di completamento e di chiusura."""

    followup_ids: list[str]
    closable_lead_ids: list[str]


def render_info(db: Database, ctx: Context, lead_ids: list[str] | None = None) -> RenderInfo:
    followups = other_open_followups(db, ctx.appointment["family_id"], ctx.appointment["id"]) if ctx.linked else []
    if lead_ids is None:
        rendered = scope_leads(ctx.leads, ctx.appointment["student_lead_id"])
    else:
        rendered = [lead for lead in (ctx.lead(lead_id) for lead_id in lead_ids) if lead is not None]
    closable = [lead["id"] for lead in rendered if lead["status"] in lead_service.OPEN_STATUSES]
    return RenderInfo([row["id"] for row in followups], closable)


# --- Valori del modulo ----------------------------------------------------------------------

def _empty_step(lead_id: str | None) -> dict[str, Any]:
    return {"action": None, "due_on": None, "assignee": catalog.DEFAULT_ASSIGNEE, "lead": lead_id, "note": None}


def default_closing(ctx: Context) -> dict[str, Any]:
    """Conclusione proposta: esito già registrato (se c'è) e ora dell'evento se è già iniziato."""
    appointment = ctx.appointment
    visited = appointment["visited_at"]
    if not visited and appointment["src_start_at"] and appointment["src_start_at"] <= stamp(ctx.now):
        visited = appointment["src_start_at"]
    return {
        "outcome": appointment["visit_outcome"],
        "visited_at": visited,
        "steps": [_empty_step(appointment["student_lead_id"]) for _ in STEP_SLOTS],
        "materials": [],
        "materials_other": None,
        "verify": False,
        "verify_due_on": None,
        "verify_assignee": catalog.DEFAULT_ASSIGNEE,
        "complete": [],
        "close": [],
        "close_reasons": {},
    }


def _closing_form(closing: Mapping[str, Any], info: RenderInfo, fallback_lead: str | None) -> dict[str, Any]:
    form: dict[str, Any] = {
        "outcome": closing.get("outcome") or "",
        "visited_at": utc_iso_to_local_input(closing.get("visited_at")),
        "materials": list(closing.get("materials") or []),
        "materials_other": closing.get("materials_other") or "",
        "verify_due_on": closing.get("verify_due_on") or "",
        "verify_assignee": closing.get("verify_assignee") or "",
    }
    steps = [step for step in closing.get("steps") or [] if isinstance(step, dict)]
    for slot in STEP_SLOTS:
        step = steps[slot - 1] if len(steps) >= slot else _empty_step(fallback_lead)
        form[f"step{slot}_action"] = step.get("action") or ""
        form[f"step{slot}_due_on"] = step.get("due_on") or ""
        form[f"step{slot}_assignee"] = step.get("assignee") or ""
        form[f"step{slot}_lead"] = step.get("lead") or ""
        form[f"step{slot}_note"] = step.get("note") or ""
    if closing.get("verify"):
        form["verify_step"] = "1"
    for followup_id in closing.get("complete") or []:
        if followup_id in info.followup_ids:
            form[f"complete_{followup_id}"] = "1"
    for lead_id in closing.get("close") or []:
        if lead_id in info.closable_lead_ids:
            form[f"close_{lead_id}"] = "1"
    for lead_id, reason in (closing.get("close_reasons") or {}).items():
        if lead_id in info.closable_lead_ids and reason:
            form[f"close_reason_{lead_id}"] = reason
    return form


def meeting_form(ctx: Context, info: RenderInfo) -> dict[str, Any]:
    """Valori del record «colloquio» come la pagina li mostra (anche la bozza di chiusura)."""
    appointment = ctx.appointment
    interview = ctx.interview
    content = content_of(interview)
    needs = content.get("needs") or {}
    assessment = content.get("assessment") or {}
    siblings = needs.get("siblings_enrolled")
    form: dict[str, Any] = {
        "a_phase": ctx.phase,
        "preparation": appointment["preparation"] or "",
        "kind": content.get("kind") or "",
        "student_lead_id": appointment["student_lead_id"] or "",
        "attendees": list(content.get("attendees") or []),
        "motivations": list(content.get("motivations") or []),
        "motivations_note": content.get("motivations_note") or "",
        "needs_services": list(needs.get("services") or []),
        "needs_activities": needs.get("activities") or "",
        "needs_schedule": needs.get("schedule") or "",
        "needs_constraints": needs.get("constraints") or "",
        "needs_siblings": "" if siblings is None else ("1" if siblings else "0"),
        "needs_desired_start": needs.get("desired_start") or "",
        "presented": list(content.get("presented") or []),
        "local_observations": appointment["local_observations"] or "",
        "visit_report": appointment["visit_report"] or "",
    }
    for key in ("interest", "timing", "driver", "obstacle"):
        form[key] = assessment.get(key) or ""
    for index, row in enumerate(content.get("questions") or [], start=1):
        form[f"q_{index}"] = row.get("q") or ""
        form[f"a_{index}"] = row.get("a") or ""
        if row.get("verify"):
            form[f"verify_{index}"] = "1"
    if ctx.phase == "open":
        draft = interview.get("closing_draft")
        closing = {**default_closing(ctx), **draft} if isinstance(draft, dict) else default_closing(ctx)
        form.update(_closing_form(closing, info, appointment["student_lead_id"]))
    else:
        form["visit_outcome"] = appointment["visit_outcome"] or ""
        form["visited_at"] = utc_iso_to_local_input(appointment["visited_at"])
    return form


def _lead_form(lead: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    values = lead_service.form_from_lead(lead)
    values.pop("revision", None)
    values.pop("notes", None)
    return {prefix + key: value for key, value in values.items()}


def _multidict(values: Mapping[str, Any]) -> MultiDict:
    items = []
    for key, value in values.items():
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            items.append((key, "" if item is None else str(item)))
    return MultiDict(items)


def question_rows(form: Any) -> int:
    """Righe domanda mostrate: quelle compilate più tre vuote, fino al massimo."""
    filled = max((index for index in range(1, catalog.MAX_QUESTIONS + 1)
                  if (form.get(f"q_{index}") or form.get(f"a_{index}"))), default=0)
    return min(catalog.MAX_QUESTIONS, filled + catalog.EMPTY_QUESTION_ROWS)


# --- Parsing dei tre tipi di record ---------------------------------------------------------

def _local_moment(value: Any, field_name: str, what: str) -> str | None:
    raw = clean(value, 20) if value is not None else None
    if not raw:
        return None
    try:
        return stamp(local_input_to_utc(raw))
    except ValueError:
        raise ValidationError(f"{what}: data e ora non valide.", field_name) from None


def _content_input(form: Any) -> dict[str, Any]:
    return {
        "kind": form.get("kind"),
        "attendees": form.getlist("attendees"),
        "motivations": form.getlist("motivations"),
        "motivations_note": form.get("motivations_note"),
        "needs": {
            "services": form.getlist("needs_services"),
            "activities": form.get("needs_activities"),
            "schedule": form.get("needs_schedule"),
            "constraints": form.get("needs_constraints"),
            "siblings_enrolled": form.get("needs_siblings"),
            "desired_start": form.get("needs_desired_start"),
        },
        "presented": form.getlist("presented"),
        "questions": [{"q": form.get(f"q_{index}"), "a": form.get(f"a_{index}"), "verify": form.get(f"verify_{index}") == "1"}
                      for index in range(1, catalog.MAX_QUESTIONS + 1)],
        "assessment": {key: form.get(key) for key in ("interest", "timing", "driver", "obstacle")},
    }


def _suffixes(form: Any, prefix: str) -> list[str]:
    return sorted(key[len(prefix):] for key in form.keys() if key.startswith(prefix) and form.get(key) == "1")


def parse_closing(form: Any, lead_ids: set[str], previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bozza della conclusione (sezione K): solo controlli di formato, nessun campo obbligatorio."""
    previous = previous or {}
    outcome = form.get("outcome") or None
    if outcome is not None and outcome not in OUTCOMES:
        raise ValidationError("Esito dell'incontro non valido.", "outcome")
    steps = []
    for slot in STEP_SLOTS:
        what = "Prossimo passo" if slot == 1 else "Altro passo"
        action = form.get(f"step{slot}_action") or None
        if action is not None and action not in STEP_ACTIONS:
            raise ValidationError(f"{what}: azione non valida.", f"step{slot}_action")
        lead = form.get(f"step{slot}_lead") or None
        if lead is not None and lead not in lead_ids:
            raise ValidationError(f"{what}: la richiesta scelta non appartiene a questa famiglia.", f"step{slot}_lead")
        steps.append({
            "action": action,
            "due_on": date_value(form.get(f"step{slot}_due_on"), f"step{slot}_due_on", f"{what}, data"),
            "assignee": text_value(form.get(f"step{slot}_assignee"), 120, f"step{slot}_assignee",
                                   f"{what}, responsabile"),
            "lead": lead,
            "note": text_value(form.get(f"step{slot}_note"), NOTE_LIMIT, f"step{slot}_note", f"{what}, nota"),
        })
    complete = _suffixes(form, "complete_")
    if not all(valid_uuid(value) for value in complete):
        raise ValidationError("Follow-up non valido: ricarica la pagina.", "complete")
    close = [value for value in _suffixes(form, "close_") if not value.startswith("reason_")]
    if not all(value in lead_ids for value in close):
        raise ValidationError("Richiesta da chiudere non valida: ricarica la pagina.", "close")
    reasons = {}
    for lead_id in sorted(lead_ids):
        reason = text_value(form.get(f"close_reason_{lead_id}"), 500, f"close_reason_{lead_id}", "Motivo")
        if reason:
            reasons[lead_id] = reason
    return {
        "outcome": outcome,
        "visited_at": _local_moment(form.get("visited_at"), "visited_at", "Data effettiva dell'incontro"),
        "steps": steps,
        "materials": catalog.clean_codes(form.getlist("materials"), catalog.MATERIALS, field="materials",
                                         what="Materiale da inviare", previous=previous.get("materials")),
        "materials_other": text_value(form.get("materials_other"), 200, "materials_other", "Altro materiale"),
        "verify": form.get("verify_step") == "1",
        "verify_due_on": date_value(form.get("verify_due_on"), "verify_due_on", "Verifiche, data"),
        "verify_assignee": text_value(form.get("verify_assignee"), 120, "verify_assignee", "Verifiche, responsabile"),
        "complete": complete,
        "close": close,
        "close_reasons": reasons,
    }


def parse_meeting(form: Any, ctx: Context) -> dict[str, Any]:
    """Valori normalizzati del record «colloquio» (R0) nella fase in cui la pagina è stata mostrata."""
    phase = form.get("a_phase") or ctx.phase
    if phase not in ("open", "done"):
        raise ValidationError("Modulo incompleto: ricarica la pagina.")
    lead_ids = {row["id"] for row in ctx.leads}
    student_lead_id = form.get("student_lead_id") or None
    if student_lead_id is not None and student_lead_id not in lead_ids:
        raise ValidationError("La richiesta scelta non appartiene a questa famiglia.", "student_lead_id")
    stored = ctx.interview
    content = clean_interview(_content_input(form), previous=content_of(stored))
    content.pop("format", None)
    values: dict[str, Any] = {
        "phase": phase,
        "preparation": text_value(form.get("preparation"), MEETING_LIMITS["preparation"], "preparation",
                                  "Nota di preparazione"),
        "student_lead_id": student_lead_id,
        "content": content,
        "local_observations": text_value(form.get("local_observations"), MEETING_LIMITS["local_observations"],
                                         "local_observations", "Nota interna"),
        "visit_report": text_value(form.get("visit_report"), MEETING_LIMITS["visit_report"], "visit_report",
                                   "Sintesi libera"),
    }
    if phase == "open":
        draft = stored.get("closing_draft") if isinstance(stored.get("closing_draft"), dict) else {}
        values["closing"] = parse_closing(form, lead_ids, draft)
    else:
        outcome = form.get("visit_outcome") or None
        if outcome is not None and outcome not in OUTCOMES:
            raise ValidationError("Esito dell'incontro non valido.", "visit_outcome")
        visited_at = _local_moment(form.get("visited_at"), "visited_at", "Data effettiva dell'incontro")
        if visited_at and from_iso(visited_at) > ctx.now + timedelta(minutes=5):
            raise ValidationError("La data della visita non può essere nel futuro.", "visited_at")
        if outcome == "SVOLTA" and not visited_at:
            raise ValidationError("Per una visita svolta indica data e ora effettive.", "visited_at")
        values["outcome"] = outcome
        values["visited_at"] = visited_at
    return values


def family_values(form: Any, family: Mapping[str, Any]) -> dict[str, Any]:
    """Record «fonte del contatto» (RF): solo fonte e dettaglio."""
    return {
        "contact_source": family_service.check_contact_source(form.get("contact_source"), family["contact_source"]),
        "contact_source_detail": text_value(form.get("contact_source_detail"), 200, "contact_source_detail",
                                            "Dettaglio della fonte"),
    }


def lead_values(form: Any, prefix: str, lead: Mapping[str, Any], today: date) -> dict[str, Any]:
    return lead_service.parse_lead(form, prefix, previous=lead, today=today, with_notes=False)


def lead_fingerprint(values: Mapping[str, Any]) -> str:
    return fingerprint({**values, "languages": json.loads(values.get("languages") or "[]")})


def _safe_fp(build) -> str | None:
    """Impronta dei valori attuali; None se i dati salvati non superano più la validazione."""
    try:
        return build()
    except (ValidationError, ValueError):
        return None


def initial_form(db: Database, ctx: Context) -> MultiDict:
    """Modulo della pagina con i valori salvati, le revisioni e le impronte di ciò che si mostra."""
    appointment, family = ctx.appointment, ctx.family
    leads = scope_leads(ctx.leads, appointment["student_lead_id"])
    info = render_info(db, ctx)
    values: dict[str, Any] = meeting_form(ctx, info)
    values.update({
        "family_id": appointment["family_id"],
        "contact_source": family["contact_source"] or "",
        "contact_source_detail": family["contact_source_detail"] or "",
    })
    for index, lead in enumerate(leads, start=1):
        values.update(_lead_form(lead, f"s{index}_"))
        values[f"s{index}_id"] = lead["id"]
    shown = _multidict(values)
    extra: dict[str, Any] = {
        "a_rev": appointment["revision"],
        "a_fp": _safe_fp(lambda: fingerprint(parse_meeting(shown, ctx))) or "",
        "f_rev": family["revision"],
        "f_fp": _safe_fp(lambda: fingerprint(family_values(shown, family))) or "",
        "conclude_id": new_id(),
        "verify_new_id": new_id(),
    }
    for slot in STEP_SLOTS:
        extra[f"step{slot}_new_id"] = new_id()
    for index, lead in enumerate(leads, start=1):
        prefix = f"s{index}_"
        extra[prefix + "rev"] = lead["revision"]
        extra[prefix + "fp"] = _safe_fp(lambda: lead_fingerprint(lead_values(shown, prefix, lead, ctx.today))) or ""
    return _multidict({**values, **extra})


# --- Invio: record, decisioni e conflitti ---------------------------------------------------

@dataclass
class RecordInput:
    key: str  # «a» colloquio, «f» famiglia, «s1»… alunni
    label: str
    row_id: str
    sent_rev: int
    sent_fp: str
    values: dict[str, Any]
    resolve: str | None = None  # «mine» | «theirs» dopo un conflitto
    current_rev_hint: int | None = None


@dataclass
class Difference:
    field: str
    current: str
    mine: str


@dataclass
class Conflict:
    key: str
    label: str
    current_rev: int
    differences: list[Difference] = field(default_factory=list)


class ConflictError(Exception):
    """Record modificati altrove dopo l'apertura della pagina: nulla è stato salvato."""

    def __init__(self, conflicts: list[Conflict]):
        super().__init__("conflitto")
        self.conflicts = conflicts


@dataclass
class Action:
    kind: str  # save | conclude | go | offer_version | offer_communicate
    section: str = ""
    target: str = ""
    arg: str = ""


GO_TARGETS = {"offer_new": "i", "offer_edit": "i", "new_lead": "a", "edit_family": "prepara"}


def parse_action(value: str | None) -> Action:
    text = (value or "save:").strip()
    if text == "conclude":
        return Action("conclude", section="k")
    kind, _, rest = text.partition(":")
    if kind == "save":
        return Action("save", section=rest if rest in SECTIONS else "")
    if kind == "go":
        target, _, arg = rest.partition(":")
        if target in GO_TARGETS and (not arg or arg == "famiglia" or valid_uuid(arg)):
            return Action("go", section=GO_TARGETS[target], target=target, arg=arg)
    if kind in ("offer_version", "offer_communicate") and valid_uuid(rest):
        return Action(kind, section="i", arg=rest)
    raise ValidationError("Azione non valida: ricarica la pagina.")


@dataclass
class Submission:
    meeting: RecordInput
    family: RecordInput
    leads: list[RecordInput]
    family_id: str
    conclude_id: str
    step_ids: list[str]
    verify_id: str
    form: Any = None


def _revision(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise StaleWriteError("Modulo incompleto: ricarica la pagina.") from None


def _hint(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _resolve(value: Any) -> str | None:
    return value if value in ("mine", "theirs") else None


def parse_submission(form: Any, ctx: Context) -> Submission:
    """Tutti i record del modulo, validati; un errore qui non salva nulla (SPEC §14)."""
    appointment, family = ctx.appointment, ctx.family
    if family is None:
        raise ValidationError("Collega prima l'appuntamento a una famiglia.")
    if form.get("family_id") != appointment["family_id"]:
        raise StaleWriteError("Nel frattempo l'appuntamento è stato collegato a un'altra famiglia: ricarica la pagina.")
    meeting = RecordInput("a", "Colloquio", appointment["id"], _revision(form.get("a_rev")), form.get("a_fp") or "",
                          parse_meeting(form, ctx), _resolve(form.get("a_resolve")), _hint(form.get("a_current_rev")))
    family_record = RecordInput("f", "Fonte del contatto (famiglia)", family["id"], _revision(form.get("f_rev")),
                                form.get("f_fp") or "", family_values(form, family), _resolve(form.get("f_resolve")),
                                _hint(form.get("f_current_rev")))
    leads: list[RecordInput] = []
    seen: set[str] = set()
    index = 1
    while form.get(f"s{index}_id"):
        prefix = f"s{index}_"
        lead = ctx.lead(form.get(prefix + "id"))
        if lead is None or lead["id"] in seen:
            raise ValidationError("Richiesta non valida nel modulo: ricarica la pagina.")
        seen.add(lead["id"])
        leads.append(RecordInput(f"s{index}", f"Dati di {lead['display_name']}", lead["id"],
                                 _revision(form.get(prefix + "rev")), form.get(prefix + "fp") or "",
                                 lead_values(form, prefix, lead, ctx.today), _resolve(form.get(prefix + "resolve")),
                                 _hint(form.get(prefix + "current_rev"))))
        index += 1
    return Submission(meeting, family_record, leads, appointment["family_id"], creation_id(form.get("conclude_id")),
                      [creation_id(form.get(f"step{slot}_new_id")) for slot in STEP_SLOTS],
                      creation_id(form.get("verify_new_id")), form)


def decide(record: RecordInput, submitted_fp: str, current_rev: int, current_fp: str | None, *,
           must_write: bool = False) -> str:
    """write | skip | conflict, secondo il protocollo delle impronte (DEC-030).

    - intatto (impronta uguale a quella mostrata) → nessuna scrittura;
    - revisione ancora quella mostrata (o quella confermata con «mantieni i miei») → scrittura;
    - valori attuali uguali a quelli inviati → invio ripetuto, nessuna scrittura;
    - valori attuali ancora uguali a quelli mostrati → cambiati altrove solo campi non mostrati: scrittura;
    - altrimenti conflitto: niente si salva, serve una scelta esplicita.
    """
    if record.resolve == "theirs":
        return "skip"
    if submitted_fp == record.sent_fp and not must_write:
        return "skip"
    expected = record.current_rev_hint if record.resolve == "mine" and record.current_rev_hint else record.sent_rev
    if current_rev == expected:
        return "write"
    if current_fp is not None and current_fp == submitted_fp:
        return "write" if must_write else "skip"
    if current_fp is not None and current_fp == record.sent_fp:
        return "write"
    return "conflict"


_MEETING_LABELS = {
    "preparation": "Nota di preparazione",
    "student_lead_id": "L'incontro riguarda",
    "local_observations": "Nota interna",
    "visit_report": "Sintesi libera",
    "outcome": "Esito",
    "visited_at": "Data effettiva",
    "phase": "Stato del colloquio",
    "content.kind": "Tipo di incontro",
    "content.attendees": "Presenti",
    "content.motivations": "Cosa cercano",
    "content.motivations_note": "Nota su cosa cercano",
    "content.needs": "Esigenze organizzative",
    "content.presented": "Cosa abbiamo presentato",
    "content.questions": "Domande e dubbi",
    "content.assessment": "Valutazione interna",
    "closing": "Conclusione (bozza)",
}
_LEAD_LABELS = {
    "display_name": "Nome", "school_year": "Anno scolastico", "grade": "Classe richiesta",
    "origin": "Città o zona", "birth_year": "Anno di nascita", "birth_date": "Data di nascita",
    "current_school": "Scuola attuale", "current_grade": "Classe attuale", "languages": "Lingue",
    "bilingual_context": "Contesti bilingui", "profile_note": "Profilo", "educational_review": "Approfondimento",
    "educational_review_note": "Nota di approfondimento",
}
_FAMILY_LABELS = {"contact_source": "Fonte", "contact_source_detail": "Dettaglio della fonte"}


def _shown(value: Any) -> str:
    if value in (None, "", [], {}):
        return "—"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text if len(text) <= 160 else text[:159] + "…"


def _differences(current: Mapping[str, Any] | None, mine: Mapping[str, Any], labels: Mapping[str, str]) -> list[Difference]:
    if current is None:
        return [Difference("Dati salvati", "non più leggibili con le regole attuali", "i tuoi valori")]
    flat_current, flat_mine = dict(current), dict(mine)
    for container in ("content",):
        if isinstance(flat_current.get(container), dict) or isinstance(flat_mine.get(container), dict):
            inner_current = flat_current.pop(container, {}) or {}
            inner_mine = flat_mine.pop(container, {}) or {}
            for key in set(inner_current) | set(inner_mine):
                flat_current[f"{container}.{key}"] = inner_current.get(key)
                flat_mine[f"{container}.{key}"] = inner_mine.get(key)
    result = []
    for key in sorted(set(flat_current) | set(flat_mine), key=lambda item: list(labels).index(item) if item in labels else 99):
        if flat_current.get(key) != flat_mine.get(key):
            result.append(Difference(labels.get(key, key), _shown(flat_current.get(key)), _shown(flat_mine.get(key))))
    return result


@dataclass
class SaveResult:
    written: list[str] = field(default_factory=list)
    concluded: bool = False
    repeated_conclusion: bool = False
    followups: list[str] = field(default_factory=list)
    offer_id: str | None = None
    offer_changed: bool | None = None
    kept_current: list[str] = field(default_factory=list)


def _closing_default_normalized(ctx: Context, info: RenderInfo) -> dict[str, Any]:
    form = _multidict(_closing_form(default_closing(ctx), info, ctx.appointment["student_lead_id"]))
    return parse_closing(form, {row["id"] for row in ctx.leads})


def _write_meeting(db: Database, ctx: Context, values: Mapping[str, Any], *, revision: int, now: datetime,
                   conclusion: Mapping[str, Any] | None = None) -> None:
    appointment = ctx.appointment
    stored = ctx.interview
    interview: dict[str, Any] = {"format": FORMAT, **values["content"], "saved_at": stamp(now)}
    for key in ("conclusion_id", "concluded_at"):
        if key in stored:
            interview[key] = stored[key]
    columns: dict[str, Any] = {
        "preparation": values["preparation"],
        "student_lead_id": values["student_lead_id"],
        "local_observations": values["local_observations"],
        "visit_report": values["visit_report"],
    }
    if conclusion is not None:
        interview["conclusion_id"] = conclusion["id"]
        interview["concluded_at"] = stamp(now)
        columns["visit_outcome"] = conclusion["outcome"]
        columns["visited_at"] = conclusion["visited_at"]
    elif values["phase"] == "open":
        if appointment["visit_outcome"] != "SVOLTA":
            info = render_info(db, ctx)
            if values["closing"] != _closing_default_normalized(ctx, info):
                interview["closing_draft"] = values["closing"]
    else:
        if values["outcome"] == "SVOLTA" and appointment["visit_outcome"] != "SVOLTA":
            raise ValidationError("Il passaggio a «Visita svolta» si registra con «Concludi colloquio».", "visit_outcome")
        columns["visit_outcome"] = values["outcome"]
        columns["visited_at"] = values["visited_at"]
    columns["interview"] = dumps(interview)
    appointment_service._update_local(db, appointment["id"], columns, revision, now)


def save(db: Database, appointment_id: str, submission: Submission, action: Action, now: datetime) -> SaveResult:
    """Salvataggio atomico di colloquio, alunni e fonte (più conclusione o azione economica)."""
    result = SaveResult()
    with db.transaction():
        ctx = load_context(db, appointment_id, now)
        if not ctx.linked or ctx.appointment["family_id"] != submission.family_id:
            raise StaleWriteError("Nel frattempo l'appuntamento è stato collegato a un'altra famiglia: ricarica la pagina.")
        if action.kind == "conclude" and ctx.interview.get("conclusion_id") == submission.conclude_id:
            result.repeated_conclusion = True
            return result
        conclude = action.kind == "conclude" and submission.meeting.resolve != "theirs"
        current_meeting = _safe_values(lambda: parse_meeting(_multidict(meeting_form(ctx, render_info(db, ctx))), ctx))
        decisions: dict[str, str] = {}
        conflicts: list[Conflict] = []

        def judge(record: RecordInput, current_rev: int, current_values, fp_function, labels, must_write=False):
            current_fp = fp_function(current_values) if current_values is not None else None
            decision = decide(record, fp_function(record.values), current_rev, current_fp, must_write=must_write)
            decisions[record.key] = decision
            if decision == "conflict":
                conflicts.append(Conflict(record.key, record.label, current_rev,
                                          _differences(current_values, record.values, labels)))
            if record.resolve == "theirs":
                result.kept_current.append(record.label)

        family_row = ctx.family
        judge(submission.family, family_row["revision"],
              _safe_values(lambda: family_values(_multidict({"contact_source": family_row["contact_source"] or "",
                                                             "contact_source_detail": family_row["contact_source_detail"] or ""}),
                                                 family_row)),
              fingerprint, _FAMILY_LABELS)
        for record in submission.leads:
            lead = ctx.lead(record.row_id)
            prefix = "cur_"
            judge(record, lead["revision"],
                  _safe_values(lambda: lead_values(_multidict(_lead_form(lead, prefix)), prefix, lead, ctx.today)),
                  lead_fingerprint, _LEAD_LABELS)
        judge(submission.meeting, ctx.appointment["revision"], current_meeting, fingerprint, _MEETING_LABELS,
              must_write=conclude)
        if conflicts:
            raise ConflictError(conflicts)

        if decisions["f"] == "write":
            family_service.update_contact_source(db, family_row["id"], submission.family.values["contact_source"],
                                                 submission.family.values["contact_source_detail"],
                                                 revision=family_row["revision"], now=now)
            result.written.append("f")
        for record in submission.leads:
            if decisions[record.key] == "write":
                lead = ctx.lead(record.row_id)
                lead_service.update_facts(db, record.row_id, family_row["id"], record.values,
                                          revision=lead["revision"], now=now)
                result.written.append(record.key)

        meeting = submission.meeting
        if conclude:
            fresh = load_context(db, appointment_id, now)
            created = _conclude(db, fresh, meeting.values, submission, now)
            _write_meeting(db, ctx, meeting.values, revision=ctx.appointment["revision"], now=now,
                           conclusion={"id": submission.conclude_id, "outcome": meeting.values["closing"]["outcome"],
                                       "visited_at": meeting.values["closing"]["visited_at"]})
            result.written.append("a")
            result.concluded = True
            result.followups = created
        elif decisions["a"] == "write":
            _write_meeting(db, ctx, meeting.values, revision=ctx.appointment["revision"], now=now)
            result.written.append("a")

        if action.kind in ("offer_version", "offer_communicate"):
            _offer_action(db, family_row["id"], action, submission.form, now, result)
    return result


def _safe_values(build):
    try:
        return build()
    except (ValidationError, ValueError):
        return None


def _offer_action(db: Database, family_id: str, action: Action, form: Any, now: datetime, result: SaveResult) -> None:
    try:
        offer = offer_service.get_offer(db, action.arg, family_id)
    except NotFoundError:
        raise ValidationError("Proposta non trovata in questa famiglia: ricarica la pagina.") from None
    if action.kind == "offer_communicate":
        channel = clean(form.get(f"channel_{offer['id']}"), 40) or "Di persona"
        result.offer_id = offer["id"]
        result.offer_changed = offer_service.communicate(db, offer["id"], family_id,
                                                         revision=_revision(form.get(f"offer_rev_{offer['id']}")),
                                                         channel=channel, now=now)
        return
    new_offer_id = creation_id(form.get(f"offer_new_id_{offer['id']}"))
    try:
        result.offer_id = offer_service.create_new_version(db, offer["id"], offer_id=new_offer_id, now=now)
        result.offer_changed = True
    except offer_service.DraftExists as exists:
        result.offer_id, result.offer_changed = exists.draft_id, False
    except AlreadySavedError as saved:
        result.offer_id, result.offer_changed = saved.record_id, False


# --- Conclusione e regola del prossimo passo (OD-5) ------------------------------------------

def closure_coverage(db: Database, family_id: str, appointment_id: str, scope_lead_id: str | None, *,
                     visited_at: str | None, new_step_leads: Iterable[str | None],
                     closing: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Richieste aperte dell'ambito senza un prossimo passo *successivo* alla visita (§8.8).

    Contano: i passi creati in questa conclusione (quelli di famiglia coprono tutte le richieste),
    i follow-up aperti creati dopo l'inizio della visita, un altro appuntamento della famiglia non
    annullato, verificato, senza esito e successivo; oppure la richiesta chiusa ora, già chiusa o in
    pausa con riesame. Non contano l'incontro stesso, gli appuntamenti con esito e i follow-up nati
    prima della visita. Restituisce i nomi scoperti ([] = si può chiudere).
    """
    moment = visited_at or "0000"
    steps: list[str | None] = list(new_step_leads)
    steps += [row[0] for row in db.all(
        "SELECT student_lead_id FROM FollowUp WHERE family_id = ? AND status = 'APERTO' AND created_at >= ?",
        (family_id, moment))]
    steps += [row[0] for row in db.all(
        f"SELECT a.student_lead_id FROM Appointment a WHERE a.family_id = ? AND a.id <> ? "
        f"AND a.src_status <> 'CANCELLED_SOURCE' AND a.verification_state = 'VERIFIED' AND a.visit_outcome IS NULL "
        f"AND {appointment_service.START_KEY} > ?", (family_id, appointment_id, moment))]
    leads = db.all("SELECT id, display_name, status FROM StudentLead WHERE family_id = ? ORDER BY display_name",
                   (family_id,))
    if not leads:
        return [] if steps else ["la famiglia (nessuna richiesta registrata)"]
    family_level = any(lead_id is None for lead_id in steps)
    scope = [row for row in leads if row["id"] == scope_lead_id] if scope_lead_id else leads
    uncovered = []
    for lead in scope:
        if lead["status"] != "IN_CORSO" or lead["id"] in closing:
            continue  # in pausa con riesame, già chiusa o chiusa in questa conclusione
        if family_level or lead["id"] in steps:
            continue
        uncovered.append(lead["display_name"])
    return uncovered


def coverage_message(uncovered: list[str]) -> str:
    return ("Per registrare la visita come svolta serve un prossimo passo successivo all'incontro per: "
            + ", ".join(uncovered) + ". Scegli un passo (anche «Attendere risposta della famiglia» con la data "
            "di riesame) oppure segna «Non prosegue».")


def _truncate(text: str | None, limit: int = NOTE_LIMIT) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def _join(note: str | None, extra: str) -> str:
    return f"{note} — {extra}" if note else extra


def _conclude(db: Database, ctx: Context, values: Mapping[str, Any], submission: Submission,
              now: datetime) -> list[str]:
    """Valida la chiusura e crea passi, completamenti e chiusure nella transazione in corso."""
    appointment = ctx.appointment
    family_id = appointment["family_id"]
    closing = values["closing"]
    outcome = closing["outcome"]
    if outcome not in OUTCOMES:
        raise ValidationError("Per concludere scegli l'esito dell'incontro.", "outcome")
    visited_at = closing["visited_at"]
    if visited_at and from_iso(visited_at) > now + timedelta(minutes=5):
        raise ValidationError("La data della visita non può essere nel futuro.", "visited_at")
    if outcome == "SVOLTA" and not visited_at:
        raise ValidationError("Per una visita svolta indica data e ora effettive.", "visited_at")

    steps: list[dict[str, Any]] = []
    for slot, step in zip(STEP_SLOTS, closing["steps"]):
        if not (step["action"] or step["due_on"] or step["note"]):
            continue
        what = "Prossimo passo" if slot == 1 else "Altro passo"
        if not step["action"]:
            raise ValidationError(f"{what}: scegli l'azione.", f"step{slot}_action")
        waiting = step["action"] == catalog.WAIT_ACTION
        if not step["due_on"]:
            raise ValidationError(f"{what}: indica " + ("la data di riesame." if waiting else "entro quando."),
                                  f"step{slot}_due_on")
        action, note = step["action"], step["note"]
        if waiting:  # «Attendere risposta della famiglia»: un follow-up ALTRO con nota predefinita (OD-5)
            action, note = "ALTRO", _join(catalog.WAIT_NOTE, note) if note else catalog.WAIT_NOTE
        elif action == "ALTRO" and not note:
            raise ValidationError(f"{what}: per «Altro» descrivi l'azione nella nota.", f"step{slot}_note")
        steps.append({"id": submission.step_ids[slot - 1], "action": action, "due_on": step["due_on"], "note": note,
                      "student_lead_id": step["lead"], "assignee": step["assignee"]})

    materials = closing["materials"]
    if materials:
        target = next((step for step in steps if step["action"] == "INVIARE_INFORMAZIONI"), None)
        if target is None:
            raise ValidationError("Hai scelto del materiale da inviare: aggiungi un passo «Inviare informazioni».",
                                  "materials")
        names = [catalog.label(catalog.MATERIALS, code) for code in materials if code != "ALTRO"]
        if "ALTRO" in materials:
            names.append(f"altro: {closing['materials_other']}" if closing["materials_other"] else "altro")
        target["note"] = _join(target["note"], "Materiale: " + ", ".join(names))

    if closing["verify"]:
        rows = [row["q"] for row in values["content"].get("questions", []) if row.get("verify")]
        if not rows:
            raise ValidationError("Nessuna domanda è segnata «da verificare» (sezione H): segnala o togli la spunta.",
                                  "verify_step")
        if not closing["verify_due_on"]:
            raise ValidationError("Verifiche: indica entro quando rispondere alla famiglia.", "verify_due_on")
        steps.append({"id": submission.verify_id, "action": "ALTRO", "due_on": closing["verify_due_on"],
                      "note": f"{catalog.VERIFY_NOTE}: " + "; ".join(rows),
                      "student_lead_id": values["student_lead_id"],
                      "assignee": closing["verify_assignee"] or catalog.DEFAULT_ASSIGNEE})
    for step in steps:
        step["note"] = _truncate(step["note"])

    scope = {row["id"]: row for row in scope_leads(ctx.leads, values["student_lead_id"])}
    closing_leads: set[str] = set()
    for lead_id in closing["close"]:
        lead = scope.get(lead_id)
        if lead is None:
            raise ValidationError("Una richiesta da chiudere non riguarda questo incontro: ricarica la pagina.", "close")
        if lead["status"] in lead_service.OPEN_STATUSES:
            closing_leads.add(lead_id)

    if outcome == "SVOLTA" and appointment["visit_outcome"] != "SVOLTA":
        uncovered = closure_coverage(db, family_id, appointment["id"], values["student_lead_id"],
                                     visited_at=visited_at, new_step_leads=[step["student_lead_id"] for step in steps],
                                     closing=closing_leads)
        if uncovered:
            raise ValidationError(coverage_message(uncovered), "step1_action")

    created = []
    for step in steps:
        if db.one("SELECT 1 FROM FollowUp WHERE id = ?", (step["id"],)):
            continue
        followup_service.insert_followup(db, family_id, {**step, "appointment_id": appointment["id"]},
                                         followup_id=step["id"], now=now)
        created.append(step["id"])
    today = rome_today(now)
    day = min(rome_date_of(visited_at), today) if visited_at else today
    for followup_id in closing["complete"]:
        row = db.one("SELECT * FROM FollowUp WHERE id = ? AND family_id = ?", (followup_id, family_id))
        if row is None:
            raise ValidationError("Follow-up da completare non valido: ricarica la pagina.", "complete")
        if row["status"] == "APERTO":
            followup_service.close_followup(db, followup_id, family_id, status="COMPLETATO",
                                            outcome=f"Completato con il colloquio del {format_date(day)}",
                                            revision=row["revision"], now=now)
    for lead_id in sorted(closing_leads):
        lead = lead_service.get_lead(db, lead_id, family_id)
        change = lead_service.StatusChange("NON_PROSEGUE", closed_on=day.isoformat(),
                                           closure_reason=closing["close_reasons"].get(lead_id))
        lead_service.change_status(db, lead_id, family_id, change, revision=lead["revision"], now=now)
    return created


# --- Dati della pagina ----------------------------------------------------------------------

def step_label(row: Mapping[str, Any]) -> str:
    """Azione del follow-up; «Attendere risposta della famiglia» e «Verificare e rispondere»
    restano riconoscibili anche se sono follow-up ALTRO con nota predefinita."""
    if row["action"] == "ALTRO":
        for text in (catalog.WAIT_NOTE, catalog.VERIFY_NOTE):
            if (row["note"] or "").startswith(text):
                return text
    return label(row["action"])


def short_date(value: str | None) -> str:
    return format_date(value)[:5] if value else ""


def quick_outcomes(today: date) -> list[dict[str, Any]]:
    """Scelte rapide con le date già calcolate dal server (Europe/Rome): precompilano, non salvano."""
    items = []
    for outcome in catalog.QUICK_OUTCOMES:
        item = {"code": outcome.code, "label": outcome.label, "action": outcome.action or "", "note": outcome.note,
                "due": (today + timedelta(days=outcome.days)).isoformat() if outcome.action else "",
                "by_timing": "", "close": outcome.action is None}
        if outcome.action == catalog.WAIT_ACTION:
            item["by_timing"] = ";".join(f"{timing}={(today + timedelta(days=days)).isoformat()}"
                                         for timing, days in catalog.WAIT_DAYS.items())
        items.append(item)
    return items


def relevant_offer_scopes(db: Database, ctx: Context, lead_id: str | None) -> list[offer_service.OfferScope]:
    """Ambiti economici dell'incontro: la richiesta (o ogni richiesta aperta) e la proposta familiare."""
    family_id = ctx.appointment["family_id"]
    existing = {scope.lead_id: scope for scope in offer_service.family_offer_scopes(db, family_id)}
    result = []
    for lead in scope_leads(ctx.leads, lead_id):
        result.append(existing.get(lead["id"]) or offer_service.OfferScope(
            family_id, lead["id"], lead["display_name"], lead_grade=lead["grade"], lead_year=lead["school_year"]))
    result.append(existing.get(None) or offer_service.OfferScope(family_id, None, None))
    return result


def family_interviews(db: Database, family_id: str) -> list[sqlite3.Row]:
    return db.all(f"{appointment_service.SELECT} WHERE a.family_id = ? ORDER BY {appointment_service.START_KEY}",
                  (family_id,))


def is_meeting(row: Mapping[str, Any]) -> bool:
    """Incontro con un colloquio o un resoconto registrato."""
    return bool(row["visit_outcome"] or row["visit_report"] or has_content(loads(row["interview"])))


def is_held(row: Mapping[str, Any], now: datetime) -> bool:
    """Colloquio avvenuto: con esito, oppure con contenuto e già iniziato. Un incontro futuro solo
    preparato non rende «precedenti» i colloqui già svolti."""
    if row["visit_outcome"]:
        return True
    if not is_meeting(row):
        return False
    start = row["src_start_at"] or (f"{row['src_start_date']}T00:00:00.000000Z" if row["src_start_date"] else None)
    return start is not None and start <= stamp(now)


def to_ask(ctx: Context, leads: list[sqlite3.Row], interviews: list[Mapping[str, Any]]) -> list[str]:
    """«Da chiedere o confermare», calcolato da ciò che manca nelle tabelle."""
    family = ctx.family
    items = []
    if not family_service.has_contact(family):
        items.append("un recapito della famiglia (telefono o email)")
    if not family["contact_source"]:
        items.append("come ci hanno conosciuto")
    for lead in leads:
        name = lead["display_name"]
        for column, what in (("birth_date", "data di nascita"), ("school_year", "anno scolastico"),
                             ("grade", "classe richiesta"), ("current_school", "scuola attuale")):
            if not lead[column]:
                items.append(f"{what} di {name}")
        if not lead_service.languages_of(lead):
            items.append(f"lingue di {name}")
    if not any(item.get("motivations") or item.get("motivations_note") for item in interviews):
        items.append("cosa cercano (mai raccolto)")
    if not any(item.get("needs") for item in interviews):
        items.append("esigenze organizzative (mai raccolte)")
    return items


@dataclass
class SectionState:
    code: str
    title: str
    tag: str
    status: str  # ok | empty | todo
    line: str = ""

    @property
    def icon(self) -> str:
        return {"ok": "✓", "todo": "!", "empty": "·"}[self.status]


SECTION_TITLES = {
    "a": ("Presenti e dati dell'alunno", "Registra"),
    "b": ("Come ci hanno conosciuto", "Domanda"),
    "c": ("Cosa cercano", "Domanda"),
    "d": ("Profilo scolastico (dichiarato dalla famiglia)", "Domanda"),
    "e": ("Lingue (dichiarate)", "Domanda"),
    "f": ("Esigenze organizzative", "Domanda"),
    "g": ("Cosa abbiamo presentato", "Registra"),
    "h": ("Domande e dubbi della famiglia", "Registra"),
    "i": ("Aspetti economici", "Economico"),
    "j": ("Valutazione interna KITE", "Interno"),
    "k": ("Conclusione e prossimo passo", ""),
}


def _labels(entries, codes, lower: bool = False) -> list[str]:
    result = [catalog.label(entries, code) for code in codes or []]
    return [text[:1].lower() + text[1:] for text in result] if lower else result


def section_states(ctx: Context, leads: list[sqlite3.Row], scopes: list[offer_service.OfferScope]) -> list[SectionState]:
    """Riga di sintesi e stato (✓ compilata · vuota ! da completare) di ogni sezione, dai dati salvati."""
    content = content_of(ctx.interview)
    appointment, family = ctx.appointment, ctx.family
    states = []

    def add(code, status, line=""):
        title, tag = SECTION_TITLES[code]
        states.append(SectionState(code, title, tag, status, line))

    attendees = _labels(catalog.ATTENDEE_RELATIONS, content.get("attendees"), lower=True)
    missing = []
    for lead in leads:
        gaps = [what for column, what in (("birth_date", "data di nascita"), ("school_year", "anno"),
                                          ("grade", "classe"), ("current_school", "scuola attuale")) if not lead[column]]
        missing.append(f"{lead['display_name']}: " + ("mancano " + ", ".join(gaps) if gaps else "dati completi"))
    any_gap = any("mancano" in item for item in missing)
    add("a", "todo" if any_gap else ("ok" if attendees or leads else "empty"),
        " · ".join(part for part in (", ".join(attendees), " · ".join(missing)) if part))
    source = family["contact_source"]
    add("b", "ok" if source else "empty",
        (source + (f" ({family['contact_source_detail']})" if family["contact_source_detail"] else "")) if source else "")
    motivations = _labels(catalog.MOTIVATIONS, content.get("motivations"))
    add("c", "ok" if motivations or content.get("motivations_note") else "empty", " · ".join(motivations[:3]))
    profiled = [lead["display_name"] for lead in leads if lead["profile_note"] or lead["educational_review"]]
    add("d", "ok" if profiled else "empty", ", ".join(profiled))
    without = [lead["display_name"] for lead in leads if not lead_service.languages_of(lead)]
    add("e", "todo" if without else ("ok" if leads else "empty"),
        ("lingue mancanti: " + ", ".join(without)) if without else
        " · ".join(f"{lead['display_name']}: {lead_service.languages_summary(lead)}" for lead in leads))
    needs = content.get("needs") or {}
    add("f", "ok" if needs else "empty", ", ".join(_labels(catalog.SERVICES_OF_INTEREST, needs.get("services"), True)))
    presented = content.get("presented") or []
    add("g", "ok" if presented else "empty", f"{len(presented)} argomenti presentati" if presented else "")
    questions = content.get("questions") or []
    to_verify = sum(1 for row in questions if row.get("verify"))
    add("h", "ok" if questions else "empty",
        f"{len(questions)} domande" + (f", {to_verify} da verificare" if to_verify else "") if questions else "")
    current = [scope for scope in scopes if scope.current is not None]
    drafts = [scope for scope in scopes if scope.draft is not None]
    if drafts:
        add("i", "todo", "bozza da comunicare: " + ", ".join(f"v{scope.draft['version_no']}" for scope in drafts))
    elif current:
        add("i", "ok", " · ".join(f"v{scope.current['version_no']} comunicata" for scope in current))
    else:
        add("i", "empty", "nessuna proposta")
    assessment = content.get("assessment") or {}
    bits = []
    if assessment.get("interest"):
        bits.append("interesse " + catalog.label(catalog.INTEREST_LEVELS, assessment["interest"]).lower())
    if assessment.get("obstacle"):
        bits.append("ostacolo " + catalog.label(catalog.OBSTACLES, assessment["obstacle"]).lower())
    add("j", "ok" if assessment or appointment["local_observations"] else "empty", " · ".join(bits))
    if appointment["visit_outcome"]:
        add("k", "ok", label(appointment["visit_outcome"])
            + (f" il {format_datetime(appointment['visited_at'])}" if appointment["visited_at"] else ""))
    else:
        past = appointment_service.needs_report(appointment, ctx.now)
        add("k", "todo" if past else "empty", "da concludere" if past else "")
    return states


_FIELD_SECTIONS = {
    "prepara": ("preparation", "kind", "student_lead_id"),
    "a": ("attendees", "display_name", "birth_date", "birth_year", "school_year", "grade", "current_school",
          "current_grade", "origin"),
    "b": ("contact_source", "contact_source_detail"),
    "c": ("motivations", "motivations_note"),
    "d": ("profile_note", "educational_review", "educational_review_note"),
    "e": ("languages", "bilingual_context"),
    "f": ("services", "activities", "schedule", "constraints", "desired_start", "needs_siblings"),
    "g": ("presented",),
    "h": ("questions",),
    "j": ("interest", "timing", "driver", "obstacle", "local_observations"),
}


def section_of(field_name: str | None) -> str:
    """Sezione da riaprire dopo un errore di validazione sul campo indicato."""
    if not field_name:
        return ""
    name = field_name
    if len(name) > 3 and name[0] == "s" and "_" in name and name[1:name.index("_")].isdigit():
        name = name[name.index("_") + 1:]
        if name.startswith(("lang_", "other_lang_")):
            return "e"
    for section, names in _FIELD_SECTIONS.items():
        if name in names:
            return section
    return "k"


@dataclass
class Page:
    """Tutto ciò che la pagina del colloquio mostra oltre ai valori del modulo."""

    leads: list[tuple[int, sqlite3.Row]]
    ages: dict[str, Any]
    siblings: list[sqlite3.Row]
    scopes: list[offer_service.OfferScope]
    states: list[SectionState]
    prepare: dict[str, Any]
    open_followups: list[sqlite3.Row]
    meeting_followups: list[sqlite3.Row]
    quick: list[dict[str, Any]]
    coverage: list[str]
    question_rows: int
    topics: tuple[catalog.Entry, ...]
    phase: str
    default_section: str
    saved_at: str | None


def prepare_panel(db: Database, ctx: Context, leads: list[sqlite3.Row],
                  scopes: list[offer_service.OfferScope]) -> dict[str, Any]:
    """«Prepara incontro»: sintesi calcolata da tutte le tabelle; l'unico campo scritto è la nota."""
    family = ctx.family
    appointment = ctx.appointment
    rows = family_interviews(db, family["id"])
    previous = [row for row in reversed(rows) if row["id"] != appointment["id"] and is_meeting(row)]
    adults = sum(1 for prefix in ("primary", "secondary")
                 if family[f"{prefix}_adult_name"] or family[f"{prefix}_phone"] or family[f"{prefix}_email"])
    followups = db.all(
        "SELECT f.*, s.display_name AS lead_name, a.src_start_at AS origin_start_at, a.src_start_date AS origin_start_date "
        "FROM FollowUp f LEFT JOIN StudentLead s ON s.id = f.student_lead_id LEFT JOIN Appointment a ON a.id = f.appointment_id "
        "WHERE f.family_id = ? AND f.status = 'APERTO' ORDER BY f.due_on, f.created_at", (family["id"],))
    marks = ", ".join("?" for _ in MANUAL_INTERACTIONS)
    notes = db.all(f"SELECT * FROM Interaction WHERE family_id = ? AND type IN ({marks}) "
                   "ORDER BY occurred_at DESC, rowid DESC LIMIT 3", (family["id"], *MANUAL_INTERACTIONS))
    return {
        "adults": adults,
        "phone": bool(family["primary_phone"] or family["secondary_phone"]),
        "email": bool(family["primary_email"] or family["secondary_email"]),
        "previous": [{"row": row, "content": content_of(loads(row["interview"])), "steps": meeting_followups(db, row["id"])}
                     for row in previous],
        "followups": followups,
        "notes": notes,
        "to_ask": to_ask(ctx, leads, [content_of(loads(row["interview"])) for row in rows]),
        "offers": [scope for scope in scopes if scope.versions],
    }


def page_data(db: Database, ctx: Context, form: Any) -> Page:
    """Dati della pagina per i blocchi alunno presenti nel modulo (quelli mostrati o inviati)."""
    leads: list[tuple[int, sqlite3.Row]] = []
    index = 1
    while form.get(f"s{index}_id"):
        lead = ctx.lead(form.get(f"s{index}_id"))
        if lead is not None:
            leads.append((index, lead))
        index += 1
    shown = [lead for _, lead in leads]
    shown_ids = {lead["id"] for lead in shown}
    lead_id = ctx.appointment["student_lead_id"]
    scopes = relevant_offer_scopes(db, ctx, lead_id)
    cycles = {cycle for cycle in (catalog.grade_cycle(lead["grade"]) for lead in shown) if cycle}
    interview = ctx.interview
    visited = interview.get("closing_draft", {}).get("visited_at") if isinstance(interview.get("closing_draft"), dict) else None
    visited = ctx.appointment["visited_at"] or visited or default_closing(ctx)["visited_at"] or stamp(ctx.now)
    coverage = closure_coverage(db, ctx.appointment["family_id"], ctx.appointment["id"], lead_id, visited_at=visited,
                                new_step_leads=[]) if ctx.phase == "open" else []
    if ctx.appointment["visit_outcome"] is None and appointment_service.needs_report(ctx.appointment, ctx.now):
        default = "k"
    elif not has_content(interview) and not ctx.appointment["visit_outcome"]:
        started = ctx.appointment["src_start_at"] and ctx.appointment["src_start_at"] <= stamp(ctx.now)
        default = "a" if started else "prepara"
    else:
        default = ""
    return Page(
        leads=leads,
        ages={lead["id"]: lead_service.age_info(lead, ctx.today) for lead in ctx.leads},
        siblings=[lead for lead in ctx.leads if lead["id"] not in shown_ids],
        scopes=scopes,
        states=section_states(ctx, shown, scopes),
        prepare=prepare_panel(db, ctx, shown, scopes),
        open_followups=other_open_followups(db, ctx.appointment["family_id"], ctx.appointment["id"]),
        meeting_followups=meeting_followups(db, ctx.appointment["id"]),
        quick=quick_outcomes(ctx.today),
        coverage=coverage,
        question_rows=question_rows(form),
        topics=catalog.for_cycle(catalog.PRESENTATION_TOPICS, cycles),
        phase=form.get("a_phase") or ctx.phase,
        default_section=default,
        saved_at=interview.get("saved_at"),
    )


# --- Riepilogo e storico (sempre ricalcolati, mai salvati) ---------------------------------

SUMMARY_LINES = 12
SUMMARY_LINES_PER_EXTRA_STUDENT = 2
_AGE_STATUS = {"REGOLARE": "regolare", "ANTICIPO": "anticipo", "DA_VERIFICARE": "da verificare"}


@dataclass
class SummaryLine:
    label: str
    text: str
    kind: str = ""  # «declared» dichiarato dalla famiglia · «internal» interno KITE


@dataclass
class Summary:
    lines: list[SummaryLine]
    limit: int
    latest: bool
    structured: bool


def _moment(row: Mapping[str, Any]) -> str:
    return row["visited_at"] or row["src_start_at"] or ((row["src_start_date"] or "") + "T00:00:00.000000Z")


def _meeting_day(row: Mapping[str, Any]) -> date | None:
    if row["visited_at"]:
        return rome_date_of(row["visited_at"])
    if row["src_start_at"]:
        return rome_date_of(row["src_start_at"])
    return date.fromisoformat(row["src_start_date"]) if row["src_start_date"] else None


def _lower(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def age_text(info: Any) -> str:
    if info is None:
        return ""
    if info.approximate:
        return f"circa {info.age_today} anni"
    text = f"{info.age_today} anni"
    if info.age_at_deadline is not None:
        extra = f", {_AGE_STATUS[info.status]}" if info.status else ""
        text += f" ({info.age_at_deadline} al {info.deadline:%d/%m/%Y}{extra})"
    return text


def _offer_text(offer: Mapping[str, Any]) -> str:
    period = label(offer["periodicity"])
    parts = []
    if offer["standard_fee_cents"] is not None:
        parts.append(f"listino {euro(offer['standard_fee_cents'])} {period}".strip())
    reductions = offer_service.reductions_of(offer)
    if reductions:
        parts.append("riservata " + ", ".join(
            f"−{euro(item.get('amount_cents'))} ({catalog.label(catalog.REDUCTION_REASONS, item.get('reason'))})"
            for item in reductions))
    if offer["proposed_fee_cents"] is not None:
        parts.append(f"finale {euro(offer['proposed_fee_cents'])} {period}".strip())
    if offer["enrollment_fee_cents"] is not None:
        parts.append(f"iscrizione {euro(offer['enrollment_fee_cents'])}")
    if offer["valid_until"]:
        parts.append(f"valida fino al {format_date(offer['valid_until'])}")
    return " · ".join(parts)


def _step_text(row: Mapping[str, Any]) -> str:
    waiting = step_label(row) == catalog.WAIT_NOTE
    text = f"{step_label(row)} {'riesame il' if waiting else 'entro'} {short_date(row['due_on'])}"
    if row["assignee"]:
        text += f" · {row['assignee']}"
    note = row["note"] or ""
    if step_label(row) in (catalog.WAIT_NOTE, catalog.VERIFY_NOTE):
        note = note[len(step_label(row)):].lstrip(" —:")
    if note:
        text += f" · «{note if len(note) <= 60 else note[:59] + '…'}»"
    return f"{text} [{label(row['status'])}]"


def _fit(items: list[str], max_lines: int) -> list[str]:
    """Al massimo `max_lines` righe: le voci in eccesso si uniscono nell'ultima."""
    if max_lines <= 0 or not items:
        return []
    if len(items) <= max_lines:
        return items
    return items[:max_lines - 1] + [" · ".join(items[max_lines - 1:])]


def _presented_before(meetings: list[sqlite3.Row], upto: Mapping[str, Any]) -> set[str]:
    done: set[str] = set()
    for row in meetings:
        if _moment(row) <= _moment(upto):
            done |= set(content_of(loads(row["interview"])).get("presented") or [])
    return done


def summary(db: Database, appointment: Mapping[str, Any], now: datetime) -> Summary:
    """Riepilogo del colloquio in al massimo 12 righe (+2 per ogni alunno in più), §4.

    Dichiarato e interno sono separati ed etichettati. I dati dell'alunno sono quelli *attuali* della
    richiesta: compaiono solo nel colloquio più recente della famiglia.
    """
    interview = loads(appointment["interview"])
    content = content_of(interview)
    outcome = appointment["visit_outcome"]
    if not (outcome or appointment["visit_report"] or has_content(interview)):
        return Summary([], SUMMARY_LINES, False, False)
    family_id = appointment["family_id"]
    rows = family_interviews(db, family_id) if family_id else []
    meetings = sorted((row for row in rows if is_held(row, now)), key=_moment)
    # Più recente: nessun altro colloquio avvenuto dopo questo (un incontro futuro solo preparato non conta).
    latest = not any(row["id"] != appointment["id"] and _moment(row) > _moment(appointment) for row in meetings)
    steps = [_step_text(row) for row in meeting_followups(db, appointment["id"])]
    when = appointment["visited_at"] or appointment["src_start_at"]
    when_text = format_datetime(when) if when else format_date(appointment["src_start_date"])

    if not content:  # resoconto V1, o colloquio concluso senza sezioni compilate: come nella V1
        lines = [SummaryLine("Resoconto", " · ".join(part for part in (
            label(outcome) if outcome else "Esito non ancora registrato", when_text) if part))]
        if appointment["visit_report"]:
            report = appointment["visit_report"]
            lines.append(SummaryLine("Sintesi", report if len(report) <= 240 else report[:239] + "…"))
        for index, text in enumerate(_fit(steps, 2)):
            lines.append(SummaryLine("Prossimi passi" if index == 0 else "", text))
        return Summary(lines, SUMMARY_LINES, latest, False)

    header = [catalog.label(catalog.MEETING_KINDS, content.get("kind")) or "Colloquio", when_text]
    if outcome == "SVOLTA":
        visited = sorted((row for row in meetings if row["visit_outcome"] == "SVOLTA"), key=lambda row: row["visited_at"])
        position = next((index for index, row in enumerate(visited, start=1) if row["id"] == appointment["id"]), None)
        if position:
            header.append(f"{position}° incontro")
    else:
        header.append(label(outcome) if outcome else "da concludere")
    attendees = _labels(catalog.ATTENDEE_RELATIONS, content.get("attendees"), lower=True)
    if attendees:
        header.append("presenti: " + ", ".join(attendees))
    fixed: list[SummaryLine] = [SummaryLine("Colloquio", " · ".join(header))]

    leads = lead_service.family_leads(db, family_id) if family_id else []
    students = scope_leads(leads, appointment["student_lead_id"])
    today = rome_today(now)
    if latest:
        for lead in students:
            identity = [lead["display_name"], age_text(lead_service.age_info(lead, today)),
                        " ".join(part for part in (lead["grade"], lead["school_year"]) if part)]
            if lead["current_school"] or lead["current_grade"]:
                identity.append("ora: " + " ".join(part for part in (lead["current_grade"], lead["current_school"]) if part))
            if lead["educational_review"]:
                identity.append("approfondimento necessario")
            fixed.append(SummaryLine("Alunno", " · ".join(part for part in identity if part)))
            languages = lead_service.languages_summary(lead)
            if lead["bilingual_context"] is not None:
                languages = " · ".join(part for part in (languages, "contesti bilingui: " + ("sì" if lead["bilingual_context"] else "no")) if part)
            fixed.append(SummaryLine("Lingue (dichiarate)", languages or "non indicate", "declared"))
    elif students:
        fixed.append(SummaryLine("Alunno", "Dati dell'alunno: vedi richiesta (valori attuali)"))

    motivations = _labels(catalog.MOTIVATIONS, content.get("motivations"))
    if motivations or content.get("motivations_note"):
        text = " · ".join(motivations)
        if content.get("motivations_note"):
            text += (" — " if text else "") + f"«{content['motivations_note']}»"
        fixed.append(SummaryLine("Cerca (dichiarato)", text, "declared"))
    needs = content.get("needs") or {}
    if needs:
        parts = [", ".join(_labels(catalog.SERVICES_OF_INTEREST, needs.get("services"), lower=True))]
        parts += [needs.get(key) or "" for key in ("schedule", "activities", "constraints")]
        if needs.get("desired_start"):
            start = date.fromisoformat(needs["desired_start"])
            begin = int(lead_service.school_year_of(start)[:4])
            parts.append(f"ingresso dal {format_date(start)}" + (" (in corso d'anno)" if start > date(begin, 9, 30) else ""))
        if "siblings_enrolled" in needs:
            parts.append("fratelli a KITE: " + ("sì" if needs["siblings_enrolled"] else "no"))
        fixed.append(SummaryLine("Esigenze (dichiarate)", " · ".join(part for part in parts if part), "declared"))
    questions = content.get("questions") or []
    if questions:
        pending = [row["q"] for row in questions if row.get("verify")]
        text = f"{len(questions)} {'domanda' if len(questions) == 1 else 'domande'}"
        if pending:
            text += f", {len(pending)} da verificare: " + "; ".join(f"«{question}»" for question in pending[:2])
            if len(pending) > 2:
                text += "…"
        fixed.append(SummaryLine("Dubbi", text))
    assessment = content.get("assessment") or {}
    internal = []
    for key, entries, what in (("interest", catalog.INTEREST_LEVELS, "interesse"),
                               ("timing", catalog.DECISION_TIMINGS, "decisione"),
                               ("driver", catalog.DRIVERS, "driver"), ("obstacle", catalog.OBSTACLES, "ostacolo")):
        if assessment.get(key):
            value = catalog.label(entries, assessment[key])
            internal.append(f"{what}: {_lower(value)}" if key != "interest" else f"interesse {value.upper()}")
    internal_line = SummaryLine("Interno KITE", " · ".join(internal), "internal") if internal else None
    presented = content.get("presented") or []
    not_presented = None
    if presented:
        cycles = {cycle for cycle in (catalog.grade_cycle(lead["grade"]) for lead in students) if cycle}
        seen = _presented_before(meetings, appointment) | set(presented)
        missing = [_lower(entry.label) for entry in catalog.for_cycle(catalog.PRESENTATION_TOPICS, cycles)
                   if entry.code not in seen]
        if missing:
            not_presented = SummaryLine("Non ancora presentato", ", ".join(missing))

    proposals = []
    day = _meeting_day(appointment)
    existing ={scope.lead_id: scope for scope in offer_service.family_offer_scopes(db, family_id)} if family_id else {}
    scopes = [existing.get(lead["id"]) for lead in students] + [existing.get(None)]
    scopes = [scope for scope in scopes if scope is not None and scope.versions]
    named = len(scopes) > 1
    for scope in scopes:
        prefix = f"{scope.heading.removeprefix('Per ')}: " if named else ""
        same_day = [row for row in scope.versions if row["communicated_at"] and day is not None
                    and rome_date_of(row["communicated_at"]) == day]
        if same_day:
            offer = max(same_day, key=lambda row: row["version_no"])
            proposals.append(f"{prefix}v{offer['version_no']} comunicata il {short_date(rome_date_of(offer['communicated_at']).isoformat())}"
                             f" · {_offer_text(offer)}")
        elif latest and scope.current is not None:
            offer = scope.current
            proposals.append(f"{prefix}corrente v{offer['version_no']}, comunicata il "
                             f"{format_date(rome_date_of(offer['communicated_at']))} · {_offer_text(offer)}")
        elif latest and scope.draft is not None:
            proposals.append(f"{prefix}bozza v{scope.draft['version_no']} da comunicare · {_offer_text(scope.draft)}")
    if not proposals and not latest and outcome == "SVOLTA":
        proposals.append("nessuna proposta comunicata in questo incontro")

    students_shown = max(1, len(students) if latest else 1)
    limit = SUMMARY_LINES + SUMMARY_LINES_PER_EXTRA_STUDENT * (students_shown - 1)
    tail = [line for line in (internal_line, not_presented) if line is not None]
    room = limit - len(fixed) - len(tail)
    step_room = min(len(steps), 2, max(room - min(len(proposals), 1), 1 if steps else 0))
    proposal_lines = _fit(proposals, room - step_room)
    step_lines = _fit(steps, room - len(proposal_lines))
    lines = list(fixed)
    lines += [SummaryLine("Proposta" if index == 0 else "", text) for index, text in enumerate(proposal_lines)]
    if internal_line is not None:
        lines.append(internal_line)
    lines += [SummaryLine("Prossimi passi" if index == 0 else "", text) for index, text in enumerate(step_lines)]
    if not_presented is not None:
        lines.append(not_presented)
    return Summary(lines[:limit], limit, latest, True)


def compact_summary(db: Database, appointment: Mapping[str, Any]) -> list[str]:
    """«Ultimo colloquio» nella scheda famiglia: data e tipo · interesse e tempistica · ostacolo · passi."""
    content = content_of(loads(appointment["interview"]))
    when = appointment["visited_at"] or appointment["src_start_at"]
    first = [format_datetime(when) if when else format_date(appointment["src_start_date"]),
             catalog.label(catalog.MEETING_KINDS, content.get("kind")) or "Incontro",
             label(appointment["visit_outcome"]) if appointment["visit_outcome"] else "da concludere"]
    lines = [" · ".join(part for part in first if part)]
    assessment = content.get("assessment") or {}
    if assessment.get("interest") or assessment.get("timing"):
        bits = []
        if assessment.get("interest"):
            bits.append("Interesse " + _lower(catalog.label(catalog.INTEREST_LEVELS, assessment["interest"])))
        if assessment.get("timing"):
            bits.append("decisione: " + _lower(catalog.label(catalog.DECISION_TIMINGS, assessment["timing"])))
        lines.append(" · ".join(bits))
    if assessment.get("obstacle"):
        lines.append("Ostacolo: " + _lower(catalog.label(catalog.OBSTACLES, assessment["obstacle"])))
    steps = [_step_text(row) for row in meeting_followups(db, appointment["id"])]
    if steps:
        lines.append("Prossimi passi: " + " · ".join(steps))
    return lines[:4]


@dataclass
class HistoryRow:
    appointment: sqlite3.Row
    kind: str
    interest: str
    obstacle: str
    steps: list[sqlite3.Row]
    structured: bool


def history(db: Database, family_id: str) -> list[HistoryRow]:
    """«Appuntamenti e colloqui»: un incontro per riga, nessuno sovrascrive l'altro (§5)."""
    rows = db.all(f"{appointment_service.SELECT} WHERE a.family_id = ? ORDER BY {appointment_service.START_KEY} DESC",
                  (family_id,))
    result = []
    for row in rows:
        content = content_of(loads(row["interview"]))
        assessment = content.get("assessment") or {}
        result.append(HistoryRow(
            row, catalog.label(catalog.MEETING_KINDS, content.get("kind")),
            catalog.label(catalog.INTEREST_LEVELS, assessment.get("interest")),
            catalog.label(catalog.OBSTACLES, assessment.get("obstacle")),
            meeting_followups(db, row["id"]), bool(content)))
    return result


def latest_meeting(db: Database, family_id: str, now: datetime) -> sqlite3.Row | None:
    """«Ultimo colloquio» della scheda: l'ultimo avvenuto (con esito, o con contenuto e già iniziato)."""
    meetings = sorted((row for row in family_interviews(db, family_id) if is_held(row, now)), key=_moment)
    return meetings[-1] if meetings else None
