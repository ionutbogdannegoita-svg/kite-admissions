"""Famiglie: creazione, modifica, doppioni, ricerca, archiviazione (SPEC §3, §5, §8, §9.4)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

from .. import AUTHOR
from ..db import Database, fold
from ..textutil import escape_like, normalize_email, normalize_phone, phone_digits, similar_labels
from .common import AlreadySavedError, NotFoundError, StaleWriteError, ValidationError, clean, stamp

FAMILY_FIELDS = (
    "display_name",
    "primary_adult_name",
    "primary_phone",
    "primary_email",
    "secondary_adult_name",
    "secondary_phone",
    "secondary_email",
    "contact_source",
    "first_contact_on",
    "preliminary_notes",
)
CONTACT_SOURCES = ("Passaparola", "Sito web", "Social", "Open day", "Telefonata", "Email", "Segreteria", "Altro")


@dataclass
class DuplicateMatch:
    family_id: str
    display_name: str
    archived: bool
    reasons: list[str] = field(default_factory=list)


class DuplicateWarning(Exception):
    """Possibili doppioni: il salvataggio richiede una conferma esplicita."""

    def __init__(self, matches: list[DuplicateMatch]):
        super().__init__("possibili doppioni")
        self.matches = matches


def _phone(form: Mapping[str, Any], name: str) -> tuple[str | None, str | None]:
    raw = clean(form.get(name), 60)
    if raw is None:
        return None, None
    normalized = normalize_phone(raw)
    if normalized is None:
        raise ValidationError("Numero di telefono non riconosciuto: controlla le cifre.", name)
    return raw, normalized


def _email(form: Mapping[str, Any], name: str) -> tuple[str | None, str | None]:
    raw = clean(form.get(name), 200)
    if raw is None:
        return None, None
    normalized = normalize_email(raw)
    if normalized is None:
        raise ValidationError("Indirizzo email non valido.", name)
    return raw, normalized


def parse_date_field(form: Mapping[str, Any], name: str, label: str) -> str | None:
    raw = clean(form.get(name), 20)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        raise ValidationError(f"{label}: data non valida.", name) from None


def parse_family(form: Mapping[str, Any]) -> dict[str, Any]:
    """Valori della famiglia dal modulo, validati. Recapiti mancanti ammessi (SPEC §3)."""
    display_name = clean(form.get("display_name"), 200)
    if display_name is None:
        raise ValidationError("L'etichetta della famiglia è obbligatoria (es. «Famiglia Rossi»).", "display_name")
    values: dict[str, Any] = {"display_name": display_name}
    values["primary_adult_name"] = clean(form.get("primary_adult_name"), 200)
    values["primary_phone"], values["primary_phone_norm"] = _phone(form, "primary_phone")
    values["primary_email"], values["primary_email_norm"] = _email(form, "primary_email")
    values["secondary_adult_name"] = clean(form.get("secondary_adult_name"), 200)
    values["secondary_phone"], values["secondary_phone_norm"] = _phone(form, "secondary_phone")
    values["secondary_email"], values["secondary_email_norm"] = _email(form, "secondary_email")
    values["contact_source"] = clean(form.get("contact_source"), 120)
    values["first_contact_on"] = parse_date_field(form, "first_contact_on", "Data primo contatto")
    values["preliminary_notes"] = clean(form.get("preliminary_notes"), 4000)
    return values


def get_family(db: Database, family_id: str) -> sqlite3.Row:
    row = db.one("SELECT * FROM Family WHERE id = ?", (family_id,))
    if row is None:
        raise NotFoundError(family_id)
    return row


def has_contact(row: Mapping[str, Any]) -> bool:
    return any(row[key] for key in ("primary_phone", "primary_email", "secondary_phone", "secondary_email"))


def find_duplicates(
    db: Database,
    values: Mapping[str, Any],
    *,
    exclude_id: str | None = None,
    check_name: bool = True,
    phones: list[str] | None = None,
    emails: list[str] | None = None,
    child_names: list[str] | None = None,
) -> list[DuplicateMatch]:
    """Possibili doppioni per telefono/email normalizzati, etichetta simile o nome bambino.

    Telefono ed email non sono chiavi univoche: un nucleo distinto può condividerli (SPEC §5).
    """
    if phones is None:
        phones = [v for v in (values.get("primary_phone_norm"), values.get("secondary_phone_norm")) if v]
    if emails is None:
        emails = [v for v in (values.get("primary_email_norm"), values.get("secondary_email_norm")) if v]
    matches: dict[str, DuplicateMatch] = {}

    def note(row: sqlite3.Row, reason: str) -> None:
        if row["id"] == exclude_id:
            return
        match = matches.setdefault(row["id"], DuplicateMatch(row["id"], row["display_name"], row["archived_at"] is not None))
        if reason not in match.reasons:
            match.reasons.append(reason)

    for phone in phones:
        for row in db.all(
            "SELECT id, display_name, archived_at FROM Family WHERE primary_phone_norm = ? OR secondary_phone_norm = ?",
            (phone, phone),
        ):
            note(row, "stesso telefono")
    for email in emails:
        for row in db.all(
            "SELECT id, display_name, archived_at FROM Family WHERE primary_email_norm = ? OR secondary_email_norm = ?",
            (email, email),
        ):
            note(row, "stessa email")
    if check_name and values.get("display_name"):
        for row in db.all("SELECT id, display_name, archived_at FROM Family"):
            if similar_labels(row["display_name"], values["display_name"]):
                note(row, "etichetta simile")
    for child in child_names or []:
        key = fold(child)
        for row in db.all(
            "SELECT f.id, f.display_name, f.archived_at FROM Family f JOIN StudentLead s ON s.family_id = f.id "
            "WHERE fold(s.display_name) = ?",
            (key,),
        ):
            note(row, "stesso nome del bambino")
    strong = {"stesso telefono": 0, "stessa email": 0}
    return sorted(matches.values(), key=lambda m: (min(strong.get(r, 1) for r in m.reasons), fold(m.display_name)))


def _insert_family(db: Database, family_id: str, values: Mapping[str, Any], now: datetime, author: str) -> None:
    columns = ["id"] + list(values) + ["created_at", "updated_at", "created_by", "updated_by"]
    params = [family_id] + list(values.values()) + [stamp(now), stamp(now), author, author]
    db.execute(
        f"INSERT INTO Family ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        params,
    )


def create_family(
    db: Database,
    values: Mapping[str, Any],
    *,
    now: datetime,
    family_id: str,
    confirm_distinct: bool = False,
    first_lead: Mapping[str, Any] | None = None,
) -> str:
    """Crea la famiglia (ed eventualmente la prima richiesta) in una sola transazione."""
    from . import leads  # import locale: evita il ciclo tra moduli

    with db.transaction():
        if db.one("SELECT 1 FROM Family WHERE id = ?", (family_id,)):
            raise AlreadySavedError(family_id)
        if not confirm_distinct:
            children = [first_lead["display_name"]] if first_lead else []
            matches = find_duplicates(db, values, child_names=children)
            if matches:
                raise DuplicateWarning(matches)
        _insert_family(db, family_id, values, now, AUTHOR)
        if first_lead:
            leads.insert_lead(db, family_id, first_lead, now=now)
    return family_id


def update_family(
    db: Database,
    family_id: str,
    values: Mapping[str, Any],
    *,
    revision: int,
    now: datetime,
    confirm_distinct: bool = False,
) -> None:
    with db.transaction():
        current = get_family(db, family_id)
        if current["revision"] != revision:
            raise StaleWriteError()
        if not confirm_distinct:
            phones = [v for k, v in values.items() if k.endswith("phone_norm") and v and v != current[k]]
            emails = [v for k, v in values.items() if k.endswith("email_norm") and v and v != current[k]]
            name_changed = values["display_name"] != current["display_name"]
            matches = find_duplicates(db, values, exclude_id=family_id, check_name=name_changed,
                                      phones=phones, emails=emails)
            if matches:
                raise DuplicateWarning(matches)
        assignments = ", ".join(f"{column} = ?" for column in values)
        cursor = db.execute(
            f"UPDATE Family SET {assignments}, updated_at = ?, updated_by = ?, revision = revision + 1 "
            "WHERE id = ? AND revision = ?",
            list(values.values()) + [stamp(now), AUTHOR, family_id, revision],
        )
        if cursor.rowcount != 1:
            raise StaleWriteError()


def set_archived(db: Database, family_id: str, archived: bool, *, now: datetime) -> bool:
    """Archiviazione reversibile: nasconde dai flussi attivi senza perdere lo storico (SPEC §9.4)."""
    with db.transaction():
        current = get_family(db, family_id)
        if (current["archived_at"] is not None) == archived:
            return False
        db.execute(
            "UPDATE Family SET archived_at = ?, updated_at = ?, updated_by = ?, revision = revision + 1 WHERE id = ?",
            (stamp(now) if archived else None, stamp(now), AUTHOR, family_id),
        )
    return True


def dossier_counts(db: Database, family_id: str) -> dict[str, int]:
    """Quanti dati locali coinvolge l'eliminazione definitiva (SPEC §9.4)."""
    appointment_ids = [row[0] for row in db.all("SELECT id FROM Appointment WHERE family_id = ?", (family_id,))]
    marks = ", ".join("?" for _ in appointment_ids) or "NULL"
    return {
        "richieste": db.scalar("SELECT count(*) FROM StudentLead WHERE family_id = ?", (family_id,)),
        "appuntamenti": len(appointment_ids),
        "resoconti": db.scalar(
            "SELECT count(*) FROM Appointment WHERE family_id = ? AND (visit_outcome IS NOT NULL "
            "OR visit_report IS NOT NULL OR local_observations IS NOT NULL)", (family_id,)),
        "offerte": db.scalar("SELECT count(*) FROM Offer WHERE family_id = ?", (family_id,)),
        "follow-up": db.scalar("SELECT count(*) FROM FollowUp WHERE family_id = ?", (family_id,)),
        "attività in cronologia": db.scalar(
            f"SELECT count(*) FROM Interaction WHERE family_id = ? OR appointment_id IN ({marks})",
            [family_id, *appointment_ids]),
    }


def delete_family(db: Database, family_id: str, *, typed_label: str, now: datetime) -> dict[str, int]:
    """Eliminazione definitiva del dossier locale, in una transazione.

    Gli eventi Calendar collegati entrano in CalendarExclusion (FAMILY_DELETED): gli stessi eventi
    non vengono reimportati né riproposti. Google Calendar non viene modificato. La copia
    pre-operazione è responsabilità del chiamante e deve riuscire prima di questa funzione.
    """
    with db.transaction():
        family = get_family(db, family_id)
        if typed_label.strip() != family["display_name"].strip():
            raise ValidationError("Per confermare digita esattamente l'etichetta della famiglia.", "confirm_label")
        counts = dossier_counts(db, family_id)
        appointments = db.all("SELECT id, calendar_id, google_event_id FROM Appointment WHERE family_id = ?",
                              (family_id,))
        ids = [row["id"] for row in appointments]
        marks = ", ".join("?" for _ in ids) or "NULL"
        db.execute(f"DELETE FROM Interaction WHERE family_id = ? OR appointment_id IN ({marks})", [family_id, *ids])
        db.execute("DELETE FROM FollowUp WHERE family_id = ?", (family_id,))
        db.execute("DELETE FROM Offer WHERE family_id = ?", (family_id,))
        for row in appointments:
            db.execute(
                "INSERT INTO CalendarExclusion (calendar_id, google_event_id, reason, excluded_at) "
                "VALUES (?, ?, 'FAMILY_DELETED', ?) ON CONFLICT (calendar_id, google_event_id) "
                "DO UPDATE SET reason = 'FAMILY_DELETED', excluded_at = excluded.excluded_at",
                (row["calendar_id"], row["google_event_id"], stamp(now)),
            )
        db.execute("DELETE FROM Appointment WHERE family_id = ?", (family_id,))
        db.execute("DELETE FROM StudentLead WHERE family_id = ?", (family_id,))
        db.execute("DELETE FROM Family WHERE id = ?", (family_id,))
    return counts


def search_families(
    db: Database,
    *,
    query: str | None = None,
    school_year: str | None = None,
    status: str | None = None,
    archived: str = "attive",
) -> list[sqlite3.Row]:
    """Ricerca per etichetta/cognome, adulti, nome bambino, telefono o email (SPEC §3)."""
    sql = ["SELECT f.* FROM Family f WHERE 1 = 1"]
    params: list[Any] = []
    if archived == "attive":
        sql.append("AND f.archived_at IS NULL")
    elif archived == "archiviate":
        sql.append("AND f.archived_at IS NOT NULL")
    text = (query or "").strip()
    if text:
        like = f"%{escape_like(fold(text))}%"
        conditions = [
            "fold(f.display_name) LIKE ? ESCAPE '\\'",
            "fold(f.primary_adult_name) LIKE ? ESCAPE '\\'",
            "fold(f.secondary_adult_name) LIKE ? ESCAPE '\\'",
            "f.primary_email_norm LIKE ? ESCAPE '\\'",
            "f.secondary_email_norm LIKE ? ESCAPE '\\'",
            "EXISTS (SELECT 1 FROM StudentLead s WHERE s.family_id = f.id AND fold(s.display_name) LIKE ? ESCAPE '\\')",
        ]
        params.extend([like] * len(conditions))
        digits = phone_digits(text)
        if len(digits) >= 5 and not re.search(r"[A-Za-z@]", text):
            conditions += ["f.primary_phone_norm LIKE ?", "f.secondary_phone_norm LIKE ?"]
            params += [f"%{digits}%", f"%{digits}%"]
        sql.append("AND (" + " OR ".join(conditions) + ")")
    if school_year or status:
        lead_conditions = ["s.family_id = f.id"]
        if school_year:
            lead_conditions.append("s.school_year = ?")
            params.append(school_year)
        if status:
            lead_conditions.append("s.status = ?")
            params.append(status)
        sql.append("AND EXISTS (SELECT 1 FROM StudentLead s WHERE " + " AND ".join(lead_conditions) + ")")
    sql.append("ORDER BY fold(f.display_name), f.created_at")
    return db.all(" ".join(sql), params)


def leads_by_family(db: Database, family_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
    result: dict[str, list[sqlite3.Row]] = {family_id: [] for family_id in family_ids}
    if not family_ids:
        return result
    marks = ", ".join("?" for _ in family_ids)
    for row in db.all(
        f"SELECT * FROM StudentLead WHERE family_id IN ({marks}) ORDER BY school_year, fold(display_name)",
        family_ids,
    ):
        result[row["family_id"]].append(row)
    return result
