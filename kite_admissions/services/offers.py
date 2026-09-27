"""Proposte economiche versionate (SPEC §6, DEC-007).

BOZZA → COMUNICATA → RITIRATA. «Segna come comunicata» registra una sola data/ora e congela il
contenuto (anche il trigger del database lo impedisce). Le variazioni sono nuove versioni.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .. import AUTHOR, catalog
from ..db import Database
from ..schema import PERIODICITIES
from .common import AlreadySavedError, NotFoundError, StaleWriteError, ValidationError, clean, stamp
from .families import parse_date_field

CHANNELS = ("Di persona", "Telefono", "Email", "WhatsApp", "Altro")
MAX_SERVICES = 6
# Contenuto copiato da «Crea nuova versione». `authorized_by` resta fuori: una nuova versione
# richiede di nuovo chi autorizza la condizione riservata (OD-3c).
CONTENT_FIELDS = ("standard_fee_cents", "proposed_fee_cents", "periodicity", "services", "conditions", "valid_until",
                  "enrollment_fee_cents", "reductions")
DRAFT_FIELDS = CONTENT_FIELDS + ("authorized_by",)
_MAX_CENTS = 1_000_000_00


class DraftExists(Exception):
    """Esiste già una bozza nello stesso ambito: si modifica quella."""

    def __init__(self, draft_id: str):
        super().__init__(draft_id)
        self.draft_id = draft_id


def parse_euro(raw: Any, field_name: str, label: str) -> int | None:
    """Importo in centesimi: accetta '1.000,50', '1000,50', '1000.50', '1000'."""
    text = str(raw or "").strip().replace("€", "").replace(" ", "").replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d{1,2})?", text):
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+(,\d{1,2})?", text):
        text = text.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d{1,2})?", text):
        text = text.replace(",", "")
    elif not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise ValidationError(f"{label}: importo non valido (es. 1.250,00).", field_name)
    try:
        cents = int((Decimal(text) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        raise ValidationError(f"{label}: importo non valido.", field_name) from None
    if cents > _MAX_CENTS:
        raise ValidationError(f"{label}: importo troppo alto.", field_name)
    return cents


def parse_services(form: Mapping[str, Any]) -> list[dict[str, Any]]:
    services = []
    for index in range(1, MAX_SERVICES + 1):
        description = clean(form.get(f"service_description_{index}"), 300)
        amount = parse_euro(form.get(f"service_amount_{index}"), f"service_amount_{index}", f"Servizio {index}")
        periodicity = form.get(f"service_periodicity_{index}") or None
        if description is None:
            if amount is not None:
                raise ValidationError(f"Servizio {index}: manca la descrizione.", f"service_description_{index}")
            continue
        if periodicity is not None and periodicity not in PERIODICITIES:
            raise ValidationError(f"Servizio {index}: periodicità non valida.", f"service_periodicity_{index}")
        if amount is not None and periodicity is None:
            raise ValidationError(f"Servizio {index}: indica la periodicità dell'importo.", f"service_periodicity_{index}")
        services.append({
            "description": description,
            "amount_cents": amount,
            "periodicity": periodicity if amount is not None else None,
            "included": form.get(f"service_included_{index}", "1") == "1",
        })
    return services


def clean_reductions(items: list[Mapping[str, Any]] | None, previous: list[Mapping[str, Any]] | None = None
                     ) -> list[dict[str, Any]]:
    """Condizione riservata: righe di riduzione sulla retta, con motivo del catalogo (OD-3a).

    Nessuna riduzione è calcolata o proposta dal sistema: si registra ciò che è stato deciso.
    """
    legacy = {item.get("reason") for item in previous or [] if isinstance(item, Mapping)}
    rows = []
    for index, item in enumerate(items or [], start=1):
        reason = str(item.get("reason") or "").strip()
        amount = item.get("amount_cents")
        try:
            note = clean(item.get("note"), 200) if item.get("note") is not None else None
        except ValidationError:
            raise ValidationError(f"Riduzione {index}: nota troppo lunga (massimo 200 caratteri).",
                                  f"reduction_note_{index}") from None
        if not reason and amount is None and not note:
            continue
        if not reason:
            raise ValidationError(f"Riduzione {index}: scegli il motivo.", f"reduction_reason_{index}")
        catalog.clean_code(reason, catalog.REDUCTION_REASONS, field=f"reduction_reason_{index}",
                           what=f"Riduzione {index}", previous=reason if reason in legacy else None)
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValidationError(f"Riduzione {index}: indica un importo maggiore di zero.", f"reduction_amount_{index}")
        if reason == "ALTRO" and not note:
            raise ValidationError(f"Riduzione {index}: per «{catalog.label(catalog.REDUCTION_REASONS, reason)}» "
                                  "descrivi la condizione nella nota.", f"reduction_note_{index}")
        rows.append({"reason": reason, "amount_cents": amount, "note": note or ""})
    if len(rows) > catalog.MAX_REDUCTIONS:
        raise ValidationError(f"Al massimo {catalog.MAX_REDUCTIONS} riduzioni per proposta.")
    return rows


def reductions_of(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        value = json.loads(row["reductions"] or "[]")
    except (ValueError, KeyError, IndexError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def needs_authorization(reductions: list[Mapping[str, Any]]) -> bool:
    """Almeno un motivo discrezionale (promozione, accordo Direzione, altro): serve «Autorizzata da» (OD-3c)."""
    return any((entry := catalog.find(catalog.REDUCTION_REASONS, item.get("reason"))) is None
               or entry.needs_authorization for item in reductions)


def parse_offer(form: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Valori della bozza: listino, condizione riservata, importo finale comunicato, servizi.

    Tutti gli importi sono scritti da chi compila; nessuno sconto viene calcolato (SPEC §6, OD-3).
    """
    periodicity = form.get("periodicity") or None
    if periodicity is not None and periodicity not in PERIODICITIES:
        raise ValidationError("Periodicità non valida.", "periodicity")
    standard = parse_euro(form.get("standard_fee"), "standard_fee", "Retta di listino")
    rows = [{"reason": form.get(f"reduction_reason_{index}") or "",
             "amount_cents": parse_euro(form.get(f"reduction_amount_{index}"), f"reduction_amount_{index}",
                                        f"Riduzione {index}"),
             "note": form.get(f"reduction_note_{index}")}
            for index in range(1, catalog.MAX_REDUCTIONS + 1)]
    reductions = clean_reductions(rows, reductions_of(previous) if previous is not None else None)
    if standard is not None and sum(item["amount_cents"] for item in reductions) > standard:
        raise ValidationError("Le riduzioni superano la retta di listino.", "reduction_amount_1")
    try:
        authorized_by = clean(form.get("authorized_by"), 120)
    except ValidationError:
        raise ValidationError("«Autorizzata da»: massimo 120 caratteri.", "authorized_by") from None
    return {
        "standard_fee_cents": standard,
        "proposed_fee_cents": parse_euro(form.get("proposed_fee"), "proposed_fee", "Retta finale comunicata"),
        "periodicity": periodicity,
        "services": json.dumps(parse_services(form), ensure_ascii=False),
        "conditions": clean(form.get("conditions"), 4000),
        "valid_until": parse_date_field(form, "valid_until", "Validità"),
        "enrollment_fee_cents": parse_euro(form.get("enrollment_fee"), "enrollment_fee", "Quota d'iscrizione"),
        "reductions": json.dumps(reductions, ensure_ascii=False),
        "authorized_by": authorized_by,
    }


def services_of(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        value = json.loads(row["services"] or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def discount_cents(row: Mapping[str, Any]) -> int | None:
    """Stessa base (stessa periodicità): lo sconto è la differenza. Nessuna percentuale."""
    if row["standard_fee_cents"] is None or row["proposed_fee_cents"] is None:
        return None
    difference = row["standard_fee_cents"] - row["proposed_fee_cents"]
    return difference if difference > 0 else None


def consistency_warning(row: Mapping[str, Any]) -> str | None:
    """Avviso, mai un blocco: listino meno riduzioni diverso dalla retta finale scritta."""
    reductions = reductions_of(row)
    if not reductions or row["standard_fee_cents"] is None or row["proposed_fee_cents"] is None:
        return None
    total = sum(int(item.get("amount_cents") or 0) for item in reductions)
    expected = row["standard_fee_cents"] - total
    if expected == row["proposed_fee_cents"]:
        return None
    from ..labels import euro

    return (f"Listino {euro(row['standard_fee_cents'])} meno riduzioni {euro(total)} = {euro(expected)}, "
            f"ma la retta finale indicata è {euro(row['proposed_fee_cents'])}: controlla prima di comunicarla.")


def get_offer(db: Database, offer_id: str, family_id: str | None = None) -> sqlite3.Row:
    row = db.one("SELECT * FROM Offer WHERE id = ?", (offer_id,))
    if row is None or (family_id is not None and row["family_id"] != family_id):
        raise NotFoundError(offer_id)
    return row


def _scope_rows(db: Database, family_id: str, lead_id: str | None) -> list[sqlite3.Row]:
    return db.all("SELECT * FROM Offer WHERE family_id = ? AND student_lead_id IS ? ORDER BY version_no",
                  (family_id, lead_id))


@dataclass
class OfferScope:
    family_id: str
    lead_id: str | None
    lead_name: str | None
    versions: list[sqlite3.Row] = field(default_factory=list)
    lead_grade: str | None = None
    lead_year: str | None = None

    @property
    def heading(self) -> str:
        """«Per Luca · Primaria 1ª · 2027/2028» oppure «Proposta familiare»: anno e classe dell'ambito."""
        if not self.lead_id:
            return "Proposta familiare"
        return "Per " + " · ".join(part for part in (self.lead_name, self.lead_grade, self.lead_year) if part)

    @property
    def draft(self) -> sqlite3.Row | None:
        return next((row for row in self.versions if row["status"] == "BOZZA"), None)

    @property
    def latest_sent(self) -> sqlite3.Row | None:
        sent = [row for row in self.versions if row["status"] != "BOZZA"]
        return max(sent, key=lambda row: row["version_no"]) if sent else None

    @property
    def current(self) -> sqlite3.Row | None:
        """Proposta corrente: l'ultima comunicata, se non ritirata (SPEC §6)."""
        latest = self.latest_sent
        return latest if latest is not None and latest["status"] == "COMUNICATA" else None


def family_offer_scopes(db: Database, family_id: str) -> list[OfferScope]:
    rows = db.all(
        "SELECT o.*, s.display_name AS lead_name, s.school_year AS lead_year, s.grade AS lead_grade FROM Offer o "
        "LEFT JOIN StudentLead s ON s.id = o.student_lead_id WHERE o.family_id = ? "
        "ORDER BY o.student_lead_id IS NOT NULL, s.display_name, o.version_no",
        (family_id,),
    )
    scopes: dict[str | None, OfferScope] = {}
    for row in rows:
        scope = scopes.setdefault(row["student_lead_id"], OfferScope(
            family_id, row["student_lead_id"], row["lead_name"], lead_grade=row["lead_grade"],
            lead_year=row["lead_year"]))
        scope.versions.append(row)
    return list(scopes.values())


def _insert(db: Database, offer_id: str, family_id: str, lead_id: str | None, version: int, previous: str | None,
            values: Mapping[str, Any], now: datetime) -> None:
    db.execute(
        "INSERT INTO Offer (id, family_id, student_lead_id, version_no, previous_offer_id, standard_fee_cents, "
        "proposed_fee_cents, periodicity, services, conditions, valid_until, enrollment_fee_cents, reductions, "
        "authorized_by, status, created_at, updated_at, created_by, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'BOZZA', ?, ?, ?, ?)",
        (offer_id, family_id, lead_id, version, previous, values["standard_fee_cents"], values["proposed_fee_cents"],
         values["periodicity"], values["services"], values["conditions"], values["valid_until"],
         values.get("enrollment_fee_cents"), values.get("reductions") or "[]", values.get("authorized_by"),
         stamp(now), stamp(now), AUTHOR, AUTHOR),
    )


def create_offer(db: Database, family_id: str, lead_id: str | None, values: Mapping[str, Any], *, offer_id: str,
                 now: datetime) -> str:
    """Nuova bozza nell'ambito famiglia/richiesta: versione successiva all'ultima esistente."""
    with db.transaction():
        if db.one("SELECT 1 FROM Offer WHERE id = ?", (offer_id,)):
            raise AlreadySavedError(offer_id)
        if db.one("SELECT 1 FROM Family WHERE id = ?", (family_id,)) is None:
            raise NotFoundError(family_id)
        if lead_id and db.one("SELECT 1 FROM StudentLead WHERE id = ? AND family_id = ?",
                              (lead_id, family_id)) is None:
            raise ValidationError("La richiesta scelta non appartiene a questa famiglia.", "student_lead_id")
        rows = _scope_rows(db, family_id, lead_id)
        draft = next((row for row in rows if row["status"] == "BOZZA"), None)
        if draft is not None:
            raise DraftExists(draft["id"])
        previous = rows[-1]["id"] if rows else None
        _insert(db, offer_id, family_id, lead_id, (rows[-1]["version_no"] + 1) if rows else 1, previous, values, now)
    return offer_id


def create_new_version(db: Database, source_id: str, *, offer_id: str, now: datetime) -> str:
    """«Crea nuova versione»: copia i dati in una nuova BOZZA collegata alla precedente."""
    with db.transaction():
        if db.one("SELECT 1 FROM Offer WHERE id = ?", (offer_id,)):
            raise AlreadySavedError(offer_id)
        source = get_offer(db, source_id)
        if source["status"] == "BOZZA":
            raise ValidationError("È già una bozza: modificala direttamente.")
        rows = _scope_rows(db, source["family_id"], source["student_lead_id"])
        draft = next((row for row in rows if row["status"] == "BOZZA"), None)
        if draft is not None:
            raise DraftExists(draft["id"])
        values = {name: source[name] for name in CONTENT_FIELDS}  # senza «Autorizzata da» (OD-3c)
        _insert(db, offer_id, source["family_id"], source["student_lead_id"], rows[-1]["version_no"] + 1,
                source_id, values, now)
    return offer_id


def update_draft(db: Database, offer_id: str, family_id: str, values: Mapping[str, Any], *, revision: int,
                 now: datetime) -> None:
    with db.transaction():
        current = get_offer(db, offer_id, family_id)
        if current["status"] != "BOZZA":
            raise ValidationError("Proposta già comunicata: il contenuto è congelato. Crea una nuova versione.")
        if current["revision"] != revision:
            raise StaleWriteError()
        assignments = ", ".join(f"{column} = ?" for column in DRAFT_FIELDS)
        cursor = db.execute(
            f"UPDATE Offer SET {assignments}, updated_at = ?, updated_by = ?, revision = revision + 1 "
            "WHERE id = ? AND revision = ? AND status = 'BOZZA'",
            [*(values[name] for name in DRAFT_FIELDS), stamp(now), AUTHOR, offer_id, revision],
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()


def completeness_problems(row: Mapping[str, Any]) -> list[str]:
    """Cosa manca perché la proposta sia comprensibile prima di comunicarla (SPEC §6)."""
    problems = []
    if row["proposed_fee_cents"] is None:
        problems.append("manca la quota proposta (retta finale comunicata)")
    if row["periodicity"] is None:
        problems.append("manca la periodicità")
    for service in services_of(row):
        if not service.get("description"):
            problems.append("un servizio non ha descrizione")
        if service.get("amount_cents") is not None and not service.get("periodicity"):
            problems.append(f"il servizio «{service.get('description')}» non ha periodicità")
    if needs_authorization(reductions_of(row)) and not row["authorized_by"]:
        problems.append("manca «Autorizzata da» per la condizione riservata (promozione, accordo con la Direzione "
                        "o altra condizione discrezionale)")
    return problems


def communicate(db: Database, offer_id: str, family_id: str, *, revision: int, channel: str | None,
                now: datetime) -> bool:
    """Segna come comunicata. Idempotente: se lo è già, nulla cambia (data, numero, contenuto).

    Restituisce True se la proposta è passata ora a COMUNICATA.
    """
    if channel is not None and channel not in CHANNELS:
        raise ValidationError("Canale non valido.", "channel")
    with db.transaction():
        current = get_offer(db, offer_id, family_id)
        if current["status"] != "BOZZA":
            return False
        if current["revision"] != revision:
            raise StaleWriteError("La bozza è cambiata dopo l'apertura della pagina: ricontrollala prima di comunicarla.")
        problems = completeness_problems(current)
        if problems:
            raise ValidationError("Prima di comunicarla completa la proposta: " + "; ".join(problems) + ".")
        cursor = db.execute(
            "UPDATE Offer SET status = 'COMUNICATA', communicated_at = ?, communicated_by = ?, "
            "communication_channel = ?, updated_at = ?, updated_by = ?, revision = revision + 1 "
            "WHERE id = ? AND status = 'BOZZA' AND revision = ?",
            (stamp(now), AUTHOR, channel, stamp(now), AUTHOR, offer_id, revision),
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()
    return True


def withdraw(db: Database, offer_id: str, family_id: str, *, reason: str | None, revision: int,
             now: datetime) -> bool:
    """Ritiro: si conservano versione, data e motivo; nessuna versione precedente torna valida."""
    if not reason:
        raise ValidationError("Indica il motivo del ritiro.", "reason")
    with db.transaction():
        current = get_offer(db, offer_id, family_id)
        if current["status"] == "RITIRATA":
            return False
        if current["status"] != "COMUNICATA":
            raise ValidationError("Si ritira solo una proposta comunicata; una bozza si elimina.")
        if current["revision"] != revision:
            raise StaleWriteError()
        db.execute(
            "UPDATE Offer SET status = 'RITIRATA', withdrawn_at = ?, withdrawal_reason = ?, updated_at = ?, "
            "updated_by = ?, revision = revision + 1 WHERE id = ? AND status = 'COMUNICATA'",
            (stamp(now), reason, stamp(now), AUTHOR, offer_id),
        )
    return True


def delete_draft(db: Database, offer_id: str, family_id: str, *, revision: int) -> None:
    with db.transaction():
        current = get_offer(db, offer_id, family_id)
        if current["status"] != "BOZZA":
            raise ValidationError("Solo le bozze si eliminano: una proposta comunicata si ritira.")
        if current["revision"] != revision:
            raise StaleWriteError()
        if db.scalar("SELECT count(*) FROM Interaction WHERE offer_id = ?", (offer_id,)):
            raise ValidationError("La bozza è citata in una nota della cronologia: modifica prima la nota.")
        db.execute("DELETE FROM Offer WHERE id = ? AND status = 'BOZZA'", (offer_id,))


def form_from_offer(row: Mapping[str, Any]) -> dict[str, Any]:
    """Valori per il modulo di modifica della bozza."""
    from ..labels import euro_input

    form: dict[str, Any] = {
        "standard_fee": euro_input(row["standard_fee_cents"]),
        "proposed_fee": euro_input(row["proposed_fee_cents"]),
        "periodicity": row["periodicity"],
        "conditions": row["conditions"],
        "valid_until": row["valid_until"],
        "revision": row["revision"],
        "enrollment_fee": euro_input(row["enrollment_fee_cents"]),
        "authorized_by": row["authorized_by"],
    }
    for index, item in enumerate(reductions_of(row), start=1):
        form[f"reduction_reason_{index}"] = item.get("reason")
        form[f"reduction_amount_{index}"] = euro_input(item.get("amount_cents"))
        form[f"reduction_note_{index}"] = item.get("note")
    for index, service in enumerate(services_of(row), start=1):
        form[f"service_description_{index}"] = service.get("description")
        form[f"service_amount_{index}"] = euro_input(service.get("amount_cents"))
        form[f"service_periodicity_{index}"] = service.get("periodicity")
        form[f"service_included_{index}"] = "1" if service.get("included") else "0"
    return form


def previous_authorization(db: Database, row: Mapping[str, Any]) -> str | None:
    """«Autorizzata da» della versione precedente: solo un suggerimento, mai copiato (OD-3c)."""
    if not row["previous_offer_id"]:
        return None
    return db.scalar("SELECT authorized_by FROM Offer WHERE id = ?", (row["previous_offer_id"],))


def expired(row: Mapping[str, Any], today: date) -> bool:
    return bool(row["valid_until"]) and date.fromisoformat(row["valid_until"]) < today
