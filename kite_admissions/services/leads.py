"""Richieste alunno (StudentLead): una riga per figlio/anno, quattro stati (SPEC §7, §8).

Ogni variazione di stato inserisce una sola Interaction nella stessa transazione (B1).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from .. import AUTHOR, catalog
from ..db import Database, fold
from ..timeutil import format_date, rome_today
from .common import (
    AlreadySavedError,
    NotFoundError,
    StaleWriteError,
    ValidationError,
    clean,
    new_id,
    stamp,
)
from .families import parse_date_field

# Dati dell'alunno utili all'ammissione (v1.1): si correggono qui, mai copiati nel colloquio.
FACT_FIELDS = (
    "display_name", "school_year", "grade", "origin", "birth_year", "birth_date", "current_school",
    "current_grade", "languages", "bilingual_context", "profile_note", "educational_review",
    "educational_review_note",
)
LEAD_FIELDS = FACT_FIELDS + ("notes",)
STATUSES = ("IN_CORSO", "IN_PAUSA", "ISCRITTO", "NON_PROSEGUE")
OPEN_STATUSES = ("IN_CORSO", "IN_PAUSA")
CLOSED_STATUSES = ("ISCRITTO", "NON_PROSEGUE")
PROFILE_LIMIT = 500
EDUCATIONAL_NOTE_LIMIT = 300
_DEFAULTS = {"languages": "[]", "educational_review": 0}


def clean_languages(items: list[Mapping[str, Any]] | None, previous: list[Mapping[str, Any]] | None = None
                    ) -> list[dict[str, str]]:
    """Lingue dichiarate dalla famiglia (non valutazioni): IT, EN, FR e fino a due altre lingue.

    Ogni voce ha un livello del catalogo. Lingue e livelli già salvati e poi tolti dal catalogo restano
    ammessi (IW13): si conservano e si possono solo togliere.
    """
    stored = [item for item in previous or [] if isinstance(item, Mapping)]
    legacy_levels = {item.get("level") for item in stored}
    main = catalog.codes(catalog.LANGUAGES) + tuple(legacy_language_codes(stored))
    by_code: dict[str, dict[str, str]] = {}
    others: list[dict[str, str]] = []
    for item in items or []:
        code = str(item.get("code") or "").strip()
        name = clean(item.get("name"), 40) if code == catalog.OTHER_LANGUAGE else None
        level = str(item.get("level") or "").strip()
        if code not in main and code != catalog.OTHER_LANGUAGE:
            raise ValidationError("Lingua non valida: ricarica la pagina.", "languages")
        if not level and not name:
            continue
        what = name or catalog.label(catalog.LANGUAGES, code)
        if not level:
            raise ValidationError(f"Lingua «{what}»: indica il livello dichiarato.", "languages")
        catalog.clean_code(level, catalog.LANGUAGE_LEVELS, field="languages", what=f"Livello di «{what}»",
                           previous=level if level in legacy_levels else None)
        if code == catalog.OTHER_LANGUAGE:
            if not name:
                raise ValidationError("Altra lingua: scrivi quale.", "languages")
            others.append({"code": code, "name": name, "level": level})
        else:
            by_code[code] = {"code": code, "level": level}
    if len(others) > catalog.MAX_OTHER_LANGUAGES:
        raise ValidationError(f"Al massimo {catalog.MAX_OTHER_LANGUAGES} altre lingue.", "languages")
    return [by_code[code] for code in main if code in by_code] + others


def legacy_language_codes(items: list[Mapping[str, Any]]) -> list[str]:
    """Codici di lingua salvati ma non più nel catalogo (esclusa «altra lingua»), nell'ordine salvato."""
    main = catalog.codes(catalog.LANGUAGES)
    result: list[str] = []
    for item in items:
        code = str(item.get("code") or "")
        if code and code not in main and code != catalog.OTHER_LANGUAGE and code not in result:
            result.append(code)
    return result


def languages_of(row: Mapping[str, Any]) -> list[dict[str, str]]:
    try:
        value = json.loads(row["languages"] or "[]")
    except (ValueError, KeyError, IndexError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def languages_summary(row: Mapping[str, Any]) -> str:
    """«IT madrelingua/bilingue · EN base»: livelli dichiarati, per schede e riepiloghi."""
    parts = []
    for item in languages_of(row):
        name = item.get("name") if item.get("code") == catalog.OTHER_LANGUAGE else item.get("code")
        level = catalog.label(catalog.LANGUAGE_LEVELS, item.get("level")).replace(" (dichiarato)", "")
        parts.append(f"{name} {level[:1].lower() + level[1:]}")
    return " · ".join(parts)


def _anniversary(birth: date, year: int) -> date:
    """Compleanno in un anno dato; chi è nato il 29 febbraio lo compie il 1° marzo negli anni non bisestili."""
    try:
        return birth.replace(year=year)
    except ValueError:
        return date(year, 3, 1)


def age_on(birth: date, day: date) -> int:
    return day.year - birth.year - (1 if day < _anniversary(birth, day.year) else 0)


@dataclass
class AgeInfo:
    """Età calcolata e verifica anagrafica: informazioni, mai un blocco della pratica (OD-2)."""

    birth_date: date | None
    approximate: bool
    age_today: int
    age_at_deadline: int | None = None
    deadline: date | None = None
    status: str | None = None  # REGOLARE | ANTICIPO | DA_VERIFICARE
    message: str = ""


def age_info(lead: Mapping[str, Any], today: date) -> AgeInfo | None:
    """Età oggi e anni compiuti al 31/12 dell'anno di inizio, con la verifica per Infanzia e Primaria 1ª."""
    if lead["birth_date"]:
        birth = date.fromisoformat(lead["birth_date"])
    elif lead["birth_year"]:
        return AgeInfo(None, True, today.year - int(lead["birth_year"]))
    else:
        return None
    info = AgeInfo(birth, False, age_on(birth, today))
    start_year = catalog.school_start_year(lead["school_year"])
    if start_year is None:
        return info
    cycle = catalog.grade_cycle(lead["grade"])
    rule = catalog.AGE_RULES.get(cycle or "")
    info.deadline = date(start_year, 12, 31)
    info.age_at_deadline = age_on(birth, info.deadline)
    if rule is None:
        return info
    deadline, early = catalog.rule_dates(rule, start_year)
    turns = _anniversary(birth, birth.year + rule.years)
    what = "l'Infanzia" if cycle == "INFANZIA" else "la Primaria 1ª"
    too_old = rule.years + (3 if cycle == "INFANZIA" else 1)
    if info.age_at_deadline >= too_old:
        info.status = "DA_VERIFICARE"
        info.message = f"al {deadline:%d/%m/%Y} avrà {info.age_at_deadline} anni: età da verificare per {what}"
    elif turns <= deadline:
        info.status = "REGOLARE"
        info.message = f"età regolare per {what}: {rule.years} anni entro il {deadline:%d/%m/%Y}"
    elif turns <= early:
        info.status = "ANTICIPO"
        info.message = (f"anticipo: compie {rule.years} anni il {turns:%d/%m/%Y}, entro il {early:%d/%m/%Y}; "
                        "ammissione secondo circolare e disponibilità")
    else:
        info.status = "DA_VERIFICARE"
        info.message = f"compie {rule.years} anni il {turns:%d/%m/%Y}, dopo il {early:%d/%m/%Y}: età da verificare"
    return info


def _parse_languages(form: Mapping[str, Any], prefix: str, previous: Mapping[str, Any] | None) -> str:
    stored = languages_of(previous) if previous is not None else []
    # Anche le lingue salvate e poi tolte dal catalogo: il modulo le mostra, qui si rileggono (IW13).
    codes = list(catalog.codes(catalog.LANGUAGES)) + legacy_language_codes(stored)
    items: list[dict[str, Any]] = [{"code": code, "level": form.get(f"{prefix}lang_{code}") or ""} for code in codes]
    for index in range(1, catalog.MAX_OTHER_LANGUAGES + 1):
        items.append({"code": catalog.OTHER_LANGUAGE, "name": form.get(f"{prefix}other_lang_{index}_name"),
                      "level": form.get(f"{prefix}other_lang_{index}_level") or ""})
    cleaned = clean_languages(items, stored if previous is not None else None)
    return json.dumps(cleaned, ensure_ascii=False)


def form_from_lead(lead: Mapping[str, Any]) -> dict[str, Any]:
    """Valori per i campi del modulo (anche con prefisso nel colloquio)."""
    form: dict[str, Any] = {name: lead[name] for name in LEAD_FIELDS if name not in ("languages",)}
    form["revision"] = lead["revision"] if "revision" in lead.keys() else None
    others = 0
    for item in languages_of(lead):
        if item.get("code") == catalog.OTHER_LANGUAGE and others < catalog.MAX_OTHER_LANGUAGES:
            others += 1
            form[f"other_lang_{others}_name"] = item.get("name")
            form[f"other_lang_{others}_level"] = item.get("level")
        elif item.get("code") != catalog.OTHER_LANGUAGE:
            form[f"lang_{item.get('code')}"] = item.get("level")
    form["bilingual_context"] = "" if lead["bilingual_context"] is None else str(lead["bilingual_context"])
    form["educational_review"] = "1" if lead["educational_review"] else ""
    return form


class SimilarLeadWarning(Exception):
    """Richiesta simile già presente nella stessa famiglia e nello stesso anno."""

    def __init__(self, leads: list[sqlite3.Row]):
        super().__init__("richiesta simile")
        self.leads = leads


def school_year_of(day: date) -> str:
    start = day.year if day.month >= 9 else day.year - 1
    return f"{start}/{start + 1}"


def school_year_options(today: date) -> list[str]:
    start = int(school_year_of(today)[:4])
    return [f"{year}/{year + 1}" for year in range(start - 1, start + 4)]


def _field_text(form: Mapping[str, Any], name: str, limit: int, what: str) -> str | None:
    try:
        return clean(form.get(name), limit)
    except ValidationError:
        raise ValidationError(f"{what}: testo troppo lungo (massimo {limit} caratteri).", name) from None


def parse_lead(form: Mapping[str, Any], prefix: str = "", *, previous: Mapping[str, Any] | None = None,
               today: date | None = None, with_notes: bool = True) -> dict[str, Any]:
    """Dati della richiesta dal modulo. Campi v1.1 facoltativi: assenti nei moduli brevi della V1.

    `previous` (riga attuale) conserva i livelli linguistici non più in catalogo; `with_notes=False`
    (colloquio) lascia fuori le note della richiesta, che si modificano dal loro modulo.
    """
    name = clean(form.get(prefix + "display_name"), 200)
    if name is None:
        raise ValidationError("Il nome o un riferimento provvisorio del bambino è obbligatorio.",
                              prefix + "display_name")
    school_year = clean(form.get(prefix + "school_year"), 9)
    if school_year is not None:
        try:
            first, second = (int(part) for part in school_year.split("/"))
        except ValueError:
            raise ValidationError("Anno scolastico nel formato 2026/2027.", prefix + "school_year") from None
        if second != first + 1:
            raise ValidationError("Anno scolastico nel formato 2026/2027.", prefix + "school_year")
    birth_date = parse_date_field(form, prefix + "birth_date", "Data di nascita")
    if birth_date is not None:
        born = date.fromisoformat(birth_date)
        if not 1990 <= born.year <= 2100 or (today is not None and born > today):
            raise ValidationError("Data di nascita non valida.", prefix + "birth_date")
    birth_raw = clean(form.get(prefix + "birth_year"), 4)
    birth_year = None
    if birth_date is not None:
        birth_year = int(birth_date[:4])  # l'anno segue sempre la data completa
    elif birth_raw is not None:
        if not birth_raw.isdigit() or not 1990 <= int(birth_raw) <= 2100:
            raise ValidationError("Anno di nascita non valido.", prefix + "birth_year")
        birth_year = int(birth_raw)
    bilingual_raw = form.get(prefix + "bilingual_context") or ""
    if bilingual_raw not in ("", "0", "1"):
        raise ValidationError("Contesti bilingui: valore non valido.", prefix + "bilingual_context")
    educational = 1 if form.get(prefix + "educational_review") == "1" else 0
    values = {
        "display_name": name,
        "school_year": school_year,
        "grade": clean(form.get(prefix + "grade"), 80),
        "origin": clean(form.get(prefix + "origin"), 200),
        "birth_year": birth_year,
        "birth_date": birth_date,
        "current_school": _field_text(form, prefix + "current_school", 200, "Scuola attuale"),
        "current_grade": _field_text(form, prefix + "current_grade", 80, "Classe attuale"),
        "languages": _parse_languages(form, prefix, previous),
        "bilingual_context": int(bilingual_raw) if bilingual_raw else None,
        "profile_note": _field_text(form, prefix + "profile_note", PROFILE_LIMIT, "Profilo scolastico"),
        "educational_review": educational,
        # Senza il flag la nota non ha senso: si svuota (lo impone anche il database).
        "educational_review_note": _field_text(form, prefix + "educational_review_note", EDUCATIONAL_NOTE_LIMIT,
                                               "Nota di approfondimento") if educational else None,
    }
    if with_notes:
        values["notes"] = clean(form.get(prefix + "notes"), 4000)
    return values


def get_lead(db: Database, lead_id: str, family_id: str | None = None) -> sqlite3.Row:
    row = db.one("SELECT * FROM StudentLead WHERE id = ?", (lead_id,))
    if row is None or (family_id is not None and row["family_id"] != family_id):
        raise NotFoundError(lead_id)
    return row


def family_leads(db: Database, family_id: str) -> list[sqlite3.Row]:
    return db.all(
        "SELECT * FROM StudentLead WHERE family_id = ? ORDER BY school_year, fold(display_name), created_at",
        (family_id,),
    )


def similar_leads(db: Database, family_id: str, values: Mapping[str, Any], exclude_id: str | None = None) -> list[sqlite3.Row]:
    """Stessa famiglia, nome simile e stesso anno: segnalate, mai fuse (SPEC §8)."""
    key = fold(values["display_name"])
    found = []
    for row in family_leads(db, family_id):
        if row["id"] == exclude_id or row["school_year"] != values.get("school_year"):
            continue
        other = fold(row["display_name"])
        if other == key or (min(len(other), len(key)) >= 3 and (other in key or key in other)):
            found.append(row)
    return found


def insert_lead(db: Database, family_id: str, values: Mapping[str, Any], *, now: datetime,
                lead_id: str | None = None) -> str:
    lead_id = lead_id or new_id()
    columns = ["id", "family_id", *LEAD_FIELDS, "status", "created_at", "updated_at", "created_by", "updated_by"]
    params = [lead_id, family_id,
              *(values.get(name) if values.get(name) is not None else _DEFAULTS.get(name) for name in LEAD_FIELDS),
              "IN_CORSO", stamp(now), stamp(now), AUTHOR, AUTHOR]
    db.execute(f"INSERT INTO StudentLead ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", params)
    return lead_id


def create_lead(db: Database, family_id: str, values: Mapping[str, Any], *, now: datetime, lead_id: str,
                confirm_similar: bool = False) -> str:
    with db.transaction():
        if db.one("SELECT 1 FROM StudentLead WHERE id = ?", (lead_id,)):
            raise AlreadySavedError(lead_id)
        if db.one("SELECT 1 FROM Family WHERE id = ?", (family_id,)) is None:
            raise NotFoundError(family_id)
        if not confirm_similar:
            similar = similar_leads(db, family_id, values)
            if similar:
                raise SimilarLeadWarning(similar)
        insert_lead(db, family_id, values, now=now, lead_id=lead_id)
    return lead_id


def update_facts(db: Database, lead_id: str, family_id: str, values: Mapping[str, Any], *, revision: int,
                 now: datetime) -> int:
    """Dati dell'alunno aggiornati dal colloquio: solo FACT_FIELDS, nessuna Interaction.

    A differenza di `update_lead` non tocca note e data di riesame. Restituisce la nuova revisione,
    così le operazioni successive nella stessa transazione possono concatenarla.
    """
    unknown = set(values) - set(FACT_FIELDS)
    if unknown:
        raise ValueError(f"campi non ammessi: {sorted(unknown)}")
    with db.transaction():
        current = get_lead(db, lead_id, family_id)
        if current["revision"] != revision:
            raise StaleWriteError()
        assignments = ", ".join(f"{column} = ?" for column in values)
        cursor = db.execute(
            f"UPDATE StudentLead SET {assignments}, updated_at = ?, updated_by = ?, revision = revision + 1 "
            "WHERE id = ? AND revision = ?",
            list(values.values()) + [stamp(now), AUTHOR, lead_id, revision],
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()
    return revision + 1


def update_lead(db: Database, lead_id: str, family_id: str, values: Mapping[str, Any], *, revision: int,
                now: datetime, review_on: str | None = None) -> None:
    """Modifica ordinaria dei campi: nessuna Interaction (SPEC §7)."""
    with db.transaction():
        current = get_lead(db, lead_id, family_id)
        if current["revision"] != revision:
            raise StaleWriteError()
        updates = dict(values)
        if current["status"] == "IN_PAUSA":
            if review_on is None:
                raise ValidationError("Una richiesta in pausa richiede la data di riesame.", "review_on")
            updates["review_on"] = review_on
        assignments = ", ".join(f"{column} = ?" for column in updates)
        cursor = db.execute(
            f"UPDATE StudentLead SET {assignments}, updated_at = ?, updated_by = ?, revision = revision + 1 "
            "WHERE id = ? AND revision = ?",
            list(updates.values()) + [stamp(now), AUTHOR, lead_id, revision],
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()


def transition_type(previous: str, new: str) -> str:
    if new == "ISCRITTO":
        return "LEAD_ENROLLED"
    if new == "NON_PROSEGUE":
        return "LEAD_NOT_CONTINUING"
    if previous in CLOSED_STATUSES:
        return "LEAD_REOPENED"
    return "LEAD_STATUS_CHANGED"


@dataclass
class StatusChange:
    new_status: str
    review_on: str | None = None
    closed_on: str | None = None
    enrollment_ref: str | None = None
    closure_reason: str | None = None
    note: str | None = None


def parse_status_change(form: Mapping[str, Any]) -> StatusChange:
    status = form.get("new_status", "")
    if status not in STATUSES:
        raise ValidationError("Scegli il nuovo stato della richiesta.", "new_status")
    return StatusChange(
        new_status=status,
        review_on=parse_date_field(form, "review_on", "Data di riesame"),
        closed_on=parse_date_field(form, "closed_on", "Data"),
        enrollment_ref=clean(form.get("enrollment_ref"), 200),
        closure_reason=clean(form.get("closure_reason"), 500),
        note=clean(form.get("note"), 2000),
    )


def _transition_text(previous: str, change: StatusChange) -> str:
    parts: list[str] = []
    new = change.new_status
    if new == "ISCRITTO":
        parts.append(f"Iscrizione confermata il {format_date(change.closed_on)} (riferimento: {change.enrollment_ref}).")
    elif new == "NON_PROSEGUE":
        parts.append(f"Non prosegue dal {format_date(change.closed_on)}.")
        if change.closure_reason:
            parts.append(f"Motivo: {change.closure_reason}.")
    elif new == "IN_PAUSA":
        parts.append(f"In pausa, riesame il {format_date(change.review_on)}.")
    elif previous in CLOSED_STATUSES:
        parts.append("Richiesta riaperta.")
    else:
        parts.append("Richiesta di nuovo in corso.")
    if change.note:
        parts.append(f"Nota: {change.note}")
    return " ".join(parts)


def change_status(db: Database, lead_id: str, family_id: str, change: StatusChange, *, revision: int,
                  now: datetime) -> str | None:
    """Cambia stato e registra la transizione nella stessa transazione.

    Restituisce l'id della Interaction, oppure None se lo stato non cambia (nessuna riga).
    """
    today = rome_today(now)
    with db.transaction():
        current = get_lead(db, lead_id, family_id)
        previous = current["status"]
        if change.new_status == previous:
            return None
        if current["revision"] != revision:
            raise StaleWriteError()
        new = change.new_status
        fields: dict[str, Any] = {"review_on": None, "closed_on": None, "enrollment_ref": None, "closure_reason": None}
        if new == "IN_PAUSA":
            if not change.review_on:
                raise ValidationError("Per mettere in pausa serve la data di riesame.", "review_on")
            if date.fromisoformat(change.review_on) < today:
                raise ValidationError("La data di riesame non può essere nel passato.", "review_on")
            fields["review_on"] = change.review_on
        elif new in CLOSED_STATUSES:
            if not change.closed_on:
                raise ValidationError("Indica la data.", "closed_on")
            if date.fromisoformat(change.closed_on) > today:
                raise ValidationError("La data non può essere nel futuro.", "closed_on")
            fields["closed_on"] = change.closed_on
            if new == "ISCRITTO":
                if not change.enrollment_ref:
                    raise ValidationError("L'iscrizione richiede un riferimento amministrativo effettivo.",
                                          "enrollment_ref")
                fields["enrollment_ref"] = change.enrollment_ref
            else:
                fields["closure_reason"] = change.closure_reason
        if previous in CLOSED_STATUSES and not change.note:
            raise ValidationError("Per riaprire o rettificare una richiesta chiusa serve una breve nota.", "note")
        assignments = ", ".join(f"{column} = ?" for column in fields)
        cursor = db.execute(
            f"UPDATE StudentLead SET status = ?, {assignments}, updated_at = ?, updated_by = ?, "
            "revision = revision + 1 WHERE id = ? AND revision = ?",
            [new, *fields.values(), stamp(now), AUTHOR, lead_id, revision],
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()
        interaction_id = new_id()
        db.execute(
            "INSERT INTO Interaction (id, family_id, student_lead_id, type, occurred_at, text, origin, "
            "previous_state, next_state, created_at, updated_at, created_by, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (interaction_id, family_id, lead_id, transition_type(previous, new), stamp(now),
             _transition_text(previous, change), AUTHOR, previous, new, stamp(now), stamp(now), AUTHOR, AUTHOR),
        )
    return interaction_id


def copy_for_new_year(lead: Mapping[str, Any]) -> dict[str, Any]:
    """Valori del modulo per una nuova richiesta in un altro anno: la pratica conclusa non si sovrascrive.

    Si copiano i dati stabili (nome, zona, data di nascita, lingue); scuola e classe attuali, profilo e
    flag di approfondimento si rivedono con la nuova richiesta.
    """
    next_year = None
    if lead["school_year"]:
        first = int(lead["school_year"][:4]) + 1
        next_year = f"{first}/{first + 1}"
    form = form_from_lead(lead)
    for name in ("grade", "notes", "current_school", "current_grade", "profile_note", "educational_review_note"):
        form[name] = None
    form.update({"school_year": next_year, "educational_review": "", "revision": None})
    return form
