"""Aggiornamento da Google Calendar: import selettivo e ripetibile (SPEC §4.2–4.4, §7 B1).

- Gli eventi già importati si riconoscono da (calendar_id, google_event_id) e si aggiornano
  sulla stessa riga; i dati locali non vengono mai toccati.
- Un evento assente dalla finestra si legge per ID: si aggiornano anche gli spostamenti fuori
  finestra. Solo `status=cancelled` conferma un annullamento; ogni altro errore lascia
  «Non verificato» e conserva la copia precedente.
- I nuovi eventi restano soltanto nell'anteprima in memoria finché non vengono selezionati.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable

from .. import IMPORT_AUTHOR
from ..db import Database
from ..gcal.source import CalendarSource, SourceError, SourceEvent, normalize_event
from ..textutil import similar_labels
from ..timeutil import rome_date_of, to_iso
from .common import NotFoundError, new_id, stamp

Progress = Callable[..., None]


@dataclass
class ImportSummary:
    attempted_at: str
    calendar_id: str
    window_start: str
    window_end: str
    finished_at: str | None = None
    listing_ok: bool = False
    listing_error: str | None = None
    pages: int = 0
    listed: int = 0
    updated: int = 0
    unchanged: int = 0
    cancelled: int = 0
    reactivated: int = 0
    not_verified: int = 0
    new_candidates: int = 0
    ignored: int = 0
    invalid: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def verified(self) -> int:
        return self.updated + self.unchanged + self.cancelled + self.reactivated

    @property
    def status(self) -> str:
        if self.listing_ok and self.not_verified == 0:
            return "OK"
        if not self.listing_ok and self.verified == 0:
            return "FAILED"
        return "PARTIAL"

    def counts(self) -> dict[str, int]:
        keys = ("listed", "pages", "updated", "unchanged", "cancelled", "reactivated", "not_verified",
                "new_candidates", "ignored", "invalid")
        return {key: getattr(self, key) for key in keys}

    def problem(self) -> str | None:
        if self.listing_error:
            return self.listing_error
        if self.not_verified == 1:
            return "1 appuntamento non verificato: controlla Google Calendar."
        if self.not_verified:
            return f"{self.not_verified} appuntamenti non verificati: controlla Google Calendar."
        return self.errors[0] if self.errors else None


@dataclass
class Preview:
    """Anteprima dei nuovi eventi: vive solo in memoria, mai nel database o nei log."""

    created_at: datetime
    calendar_id: str
    candidates: dict[str, SourceEvent] = field(default_factory=dict)
    ignored: dict[str, SourceEvent] = field(default_factory=dict)
    invalid: list[SourceEvent] = field(default_factory=list)
    similar: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def sorted_candidates(self) -> list[SourceEvent]:
        return sorted(self.candidates.values(), key=lambda event: event.sort_key)

    def sorted_ignored(self) -> list[SourceEvent]:
        return sorted(self.ignored.values(), key=lambda event: event.sort_key)


class UnavailableSource:
    """Sorgente non utilizzabile (es. Google non collegato): ogni lettura fallisce."""

    label = "Google Calendar"

    def __init__(self, error: SourceError):
        self.error = error

    def list_calendars(self):
        raise self.error

    def list_events(self, *args, **kwargs):
        raise self.error

    def get_event(self, *args, **kwargs):
        raise self.error


_TRANSITION_TEXT = {
    "CALENDAR_CANCELLED": "Evento annullato su Google Calendar (rilevato dall'aggiornamento).",
    "CALENDAR_REACTIVATED": "Evento di nuovo attivo su Google Calendar (rilevato dall'aggiornamento).",
}


def _record_transition(db: Database, appointment_id: str, kind: str, previous: str, new: str, now: datetime) -> None:
    """Transizione B1 riferita direttamente all'appuntamento, anche se non collegato."""
    db.execute(
        "INSERT INTO Interaction (id, appointment_id, type, occurred_at, text, origin, previous_state, next_state, "
        "created_at, updated_at, created_by, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id(), appointment_id, kind, stamp(now), _TRANSITION_TEXT[kind], IMPORT_AUTHOR, previous, new,
         stamp(now), stamp(now), IMPORT_AUTHOR, IMPORT_AUTHOR),
    )


def _set_not_verified(db: Database, appointment_id: str, message: str, now: datetime) -> None:
    db.execute(
        "UPDATE Appointment SET verification_state = 'NOT_VERIFIED', verification_error = ?, last_checked_at = ? "
        "WHERE id = ?",
        (message, stamp(now), appointment_id),
    )


def mark_not_verified(db: Database, appointment_id: str, message: str, now: datetime) -> str:
    """Nessuna conferma dalla sorgente: si conserva tutto, senza Interaction (SPEC §4.4)."""
    with db.transaction():
        _set_not_verified(db, appointment_id, message, now)
    return "not_verified"


def apply_source_event(db: Database, appointment_id: str, event: SourceEvent, now: datetime) -> str:
    """Applica una risposta riuscita di Google alla copia locale. Restituisce l'esito."""
    with db.transaction():
        current = db.one("SELECT * FROM Appointment WHERE id = ?", (appointment_id,))
        if current is None:
            return "unchanged"
        changes: dict[str, Any] = {}
        outcome = "unchanged"
        if event.status == "cancelled":
            if current["src_status"] != "CANCELLED_SOURCE":
                changes["src_status"] = "CANCELLED_SOURCE"
                _record_transition(db, appointment_id, "CALENDAR_CANCELLED", current["src_status"],
                                   "CANCELLED_SOURCE", now)
                outcome = "cancelled"
            if event.updated and event.updated != current["src_updated_at"]:
                changes["src_updated_at"] = event.updated
        else:
            if not event.valid:
                _set_not_verified(db, appointment_id,
                                  f"Dati dell'evento non validi ({event.invalid_reason}): conservata la copia precedente.",
                                  now)
                return "not_verified"
            columns = event.source_columns()
            changes = {column: value for column, value in columns.items() if current[column] != value}
            if current["src_status"] == "CANCELLED_SOURCE":
                _record_transition(db, appointment_id, "CALENDAR_REACTIVATED", "CANCELLED_SOURCE",
                                   columns["src_status"], now)
                outcome = "reactivated"
            elif changes:
                outcome = "updated"
        values: dict[str, Any] = {
            "verification_state": "VERIFIED",
            "verification_error": None,
            "last_synced_at": stamp(now),
            "last_checked_at": stamp(now),
        }
        if changes:
            values.update(changes)
            values["updated_at"] = stamp(now)
            values["updated_by"] = IMPORT_AUTHOR
        assignments = ", ".join(f"{column} = ?" for column in values)
        # La revisione non cambia: protegge i campi locali, che l'import non tocca mai.
        db.execute(f"UPDATE Appointment SET {assignments} WHERE id = ?", [*values.values(), appointment_id])
    return outcome


def similar_imported(db: Database, event: SourceEvent) -> list[dict[str, Any]]:
    """Appuntamenti già importati con titolo simile nello stesso giorno: possibile copia (SPEC §4.4)."""
    if not event.title:
        return []
    day = event.start_date or (rome_date_of(event.start_at).isoformat() if event.start_at else None)
    found = []
    for row in db.all("SELECT id, src_title, src_start_at, src_start_date FROM Appointment"):
        other_day = row["src_start_date"] or (rome_date_of(row["src_start_at"]).isoformat() if row["src_start_at"] else None)
        if other_day == day and similar_labels(row["src_title"], event.title):
            found.append({"id": row["id"], "title": row["src_title"], "start_at": row["src_start_at"],
                          "start_date": row["src_start_date"]})
    return found


def refresh(
    db: Database,
    source: CalendarSource,
    *,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
    now: datetime,
    clock: Callable[[], datetime] | None = None,
    progress: Progress | None = None,
) -> tuple[ImportSummary, Preview]:
    """Un aggiornamento completo: lettura della finestra, verifica degli importati, anteprima."""
    report = progress or (lambda **_: None)
    summary = ImportSummary(attempted_at=stamp(now), calendar_id=calendar_id,
                            window_start=to_iso(time_min), window_end=to_iso(time_max))
    listed: dict[str, SourceEvent] = {}
    down: SourceError | None = None

    def on_page(page: int) -> None:
        summary.pages = page
        report(pages=page)

    report(phase="Lettura degli eventi della finestra da Google Calendar")
    try:
        for raw in source.list_events(calendar_id, time_min, time_max, on_page=on_page):
            try:
                event = normalize_event(raw, calendar_id)
            except ValueError:
                summary.errors.append("Un evento senza identificativo è stato tralasciato.")
                continue
            listed[event.event_id] = event
        summary.listing_ok = True
        summary.listed = len(listed)
    except SourceError as exc:
        summary.listing_error = exc.message
        if exc.source_wide:
            down = exc

    imported = db.all("SELECT id, calendar_id, google_event_id FROM Appointment ORDER BY calendar_id, google_event_id")
    report(phase="Verifica degli appuntamenti già importati", to_verify=len(imported), verified=0)
    for index, row in enumerate(imported, start=1):
        if down is not None:
            outcome = mark_not_verified(db, row["id"], down.message, now)
        elif summary.listing_ok and row["calendar_id"] == calendar_id and row["google_event_id"] in listed:
            outcome = apply_source_event(db, row["id"], listed[row["google_event_id"]], now)
        else:
            try:
                raw = source.get_event(row["calendar_id"], row["google_event_id"])
                event = normalize_event(raw, row["calendar_id"])
                if event.event_id != row["google_event_id"]:
                    raise SourceError("invalid")
                outcome = apply_source_event(db, row["id"], event, now)
            except SourceError as exc:
                outcome = mark_not_verified(db, row["id"], exc.message, now)
                if exc.source_wide:
                    down = exc
            except ValueError:
                outcome = mark_not_verified(db, row["id"], "Risposta di Google non valida.", now)
        setattr(summary, outcome, getattr(summary, outcome) + 1)
        report(verified=index)

    preview = Preview(created_at=now, calendar_id=calendar_id)
    if summary.listing_ok:
        report(phase="Preparazione dell'anteprima dei nuovi eventi")
        exclusions = {row[0]: row[1] for row in db.all(
            "SELECT google_event_id, reason FROM CalendarExclusion WHERE calendar_id = ?", (calendar_id,))}
        known = {row["google_event_id"] for row in imported if row["calendar_id"] == calendar_id}
        for event in sorted(listed.values(), key=lambda item: item.sort_key):
            if event.event_id in known or event.status == "cancelled":
                continue
            reason = exclusions.get(event.event_id)
            if reason == "FAMILY_DELETED":
                continue  # eventi di dossier eliminati: mai riproposti automaticamente (SPEC §9.4)
            if reason == "IGNORED":
                preview.ignored[event.event_id] = event
                continue
            if not event.valid:
                preview.invalid.append(event)
                continue
            preview.candidates[event.event_id] = event
            similar = similar_imported(db, event)
            if similar:
                preview.similar[event.event_id] = similar
    summary.new_candidates = len(preview.candidates)
    summary.ignored = len(preview.ignored)
    summary.invalid = len(preview.invalid)
    summary.finished_at = stamp(clock() if clock else now)
    report(phase="Completato")
    return summary, preview


_INSERT_COLUMNS = (
    "id", "calendar_id", "google_event_id", "src_title", "src_start_at", "src_end_at", "src_start_date",
    "src_end_date", "src_time_zone", "src_location", "src_description", "src_contacts", "src_status",
    "src_updated_at", "src_html_link", "src_ical_uid", "src_recurring_event_id", "src_original_start",
    "verification_state", "last_synced_at", "last_checked_at", "created_at", "updated_at", "created_by",
    "updated_by",
)


def import_selected(db: Database, preview: Preview, event_ids: list[str], now: datetime) -> tuple[int, int]:
    """Salva soltanto gli eventi selezionati, senza riscriverne titolo, data o descrizione.

    Idempotente: un doppio clic o un secondo invio non creano una seconda riga (UNIQUE calendario/evento).
    Restituisce (importati, già presenti).
    """
    chosen = [preview.candidates[event_id] for event_id in dict.fromkeys(event_ids) if event_id in preview.candidates]
    imported = already = 0
    checked_at = stamp(preview.created_at)
    with db.transaction():
        excluded = {row[0] for row in db.all(
            "SELECT google_event_id FROM CalendarExclusion WHERE calendar_id = ?", (preview.calendar_id,))}
        for event in chosen:
            if event.event_id in excluded:
                continue
            columns = event.source_columns()
            values = [new_id(), preview.calendar_id, event.event_id, *(columns[name] for name in _INSERT_COLUMNS[3:18]),
                      "VERIFIED", checked_at, checked_at, stamp(now), stamp(now), IMPORT_AUTHOR, IMPORT_AUTHOR]
            cursor = db.execute(
                f"INSERT INTO Appointment ({', '.join(_INSERT_COLUMNS)}) VALUES ({', '.join('?' for _ in _INSERT_COLUMNS)}) "
                "ON CONFLICT (calendar_id, google_event_id) DO NOTHING",
                values,
            )
            if cursor.rowcount == 1:
                imported += 1
            else:
                already += 1
    for event in chosen:
        preview.candidates.pop(event.event_id, None)
    return imported, already


def ignore_event(db: Database, preview: Preview, event_id: str, now: datetime) -> None:
    """«Ignora questo evento»: solo gli identificativi, nessun contenuto (SPEC §8)."""
    event = preview.candidates.get(event_id)
    if event is None:
        raise NotFoundError(event_id)
    with db.transaction():
        db.execute(
            "INSERT INTO CalendarExclusion (calendar_id, google_event_id, reason, excluded_at) VALUES (?, ?, 'IGNORED', ?) "
            "ON CONFLICT (calendar_id, google_event_id) DO NOTHING",
            (preview.calendar_id, event_id, stamp(now)),
        )
    preview.ignored[event_id] = preview.candidates.pop(event_id)


def unignore_event(db: Database, preview: Preview | None, calendar_id: str, event_id: str) -> bool:
    """La scelta «Ignora» è revocabile; le esclusioni di dossier eliminati no."""
    with db.transaction():
        cursor = db.execute(
            "DELETE FROM CalendarExclusion WHERE calendar_id = ? AND google_event_id = ? AND reason = 'IGNORED'",
            (calendar_id, event_id),
        )
    if preview is not None and event_id in preview.ignored:
        event = preview.ignored.pop(event_id)
        if event.valid:
            preview.candidates[event_id] = event
        else:
            preview.invalid.append(event)
    return cursor.rowcount == 1


def summary_record(summary: ImportSummary, previous: dict[str, Any] | None) -> dict[str, Any]:
    """Stato dell'ultimo aggiornamento per la configurazione locale (senza contenuti degli eventi)."""
    previous = previous or {}
    if previous.get("status") == "RESTORED":
        previous = {}  # dopo un ripristino un controllo completo precedente non vale più
    return {
        "attempted_at": summary.attempted_at,
        "finished_at": summary.finished_at,
        "status": summary.status,
        "full_check_at": summary.finished_at if summary.status == "OK" else previous.get("full_check_at"),
        "calendar_id": summary.calendar_id,
        "counts": summary.counts(),
        "problem": summary.problem(),
    }


def failed_record(attempted_at: str, message: str, previous: dict[str, Any] | None) -> dict[str, Any]:
    previous = previous or {}
    return {"attempted_at": attempted_at, "finished_at": attempted_at, "status": "FAILED",
            "full_check_at": previous.get("full_check_at"), "calendar_id": previous.get("calendar_id"),
            "counts": {}, "problem": message}


def summary_dict(summary: ImportSummary) -> dict[str, Any]:
    data = asdict(summary)
    data["status"] = summary.status
    return data
