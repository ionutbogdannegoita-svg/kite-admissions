"""Colloquio di ammissione (v1.1, SPEC §14): contenuto strutturato nell'appuntamento.

Un incontro = un evento Calendar = una riga Appointment = un colloquio (DEC-024). La parte
strutturata vive in `Appointment.interview` (JSON); preparazione, esito, data effettiva, sintesi e
nota interna restano nei campi V1. I dati dell'alunno stanno nella sua richiesta, la fonte nella
famiglia, l'economia nell'offerta, il prossimo passo nel follow-up: il colloquio non li copia.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Mapping

from .. import catalog
from .common import ValidationError, clean

FORMAT = 1

TEXT_LIMITS = {
    "motivations_note": 600,
    "activities": 200,
    "schedule": 200,
    "constraints": 400,
    "q": 300,
    "a": 500,
}


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
