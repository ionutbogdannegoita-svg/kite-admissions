"""Date e orari: UTC nel database, Europe/Rome a schermo (SPEC §8)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_WEEKDAYS = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
_WEEKDAYS_LONG = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
_MONTHS = (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(moment: datetime) -> str:
    """Timestamp UTC a larghezza fissa, ordinabile come testo."""
    if moment.tzinfo is None:
        raise ValueError("datetime senza fuso orario")
    return moment.astimezone(UTC).strftime(_ISO_FORMAT)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, _ISO_FORMAT).replace(tzinfo=UTC)


def rome_today(now: datetime) -> date:
    return now.astimezone(ROME).date()


def rome_midnight(day: date) -> datetime:
    """Mezzanotte di Roma di quel giorno, espressa in UTC."""
    return datetime.combine(day, time(0), tzinfo=ROME).astimezone(UTC)


def parse_rfc3339(value: str) -> datetime:
    text = value.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("orario RFC 3339 senza fuso")
    return moment.astimezone(UTC)


def rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def import_window(today: date, past_days: int, future_days: int) -> tuple[datetime, datetime]:
    """Finestra di import calcolata in Europe/Rome: da mezzanotte di (oggi - past) a mezzanotte di (oggi + future + 1)."""
    start = rome_midnight(today - timedelta(days=past_days))
    end = rome_midnight(today + timedelta(days=future_days + 1))
    return start, end


def parse_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def local_input_to_utc(value: str) -> datetime:
    """Valore di <input type=datetime-local> interpretato come ora di Roma."""
    naive = datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M")
    return naive.replace(tzinfo=ROME).astimezone(UTC)


def utc_iso_to_local_input(value: str | None) -> str:
    if not value:
        return ""
    return from_iso(value).astimezone(ROME).strftime("%Y-%m-%dT%H:%M")


def _as_rome(value: datetime | str) -> datetime:
    moment = from_iso(value) if isinstance(value, str) else value
    return moment.astimezone(ROME)


def format_datetime(value: datetime | str | None, weekday: bool = True) -> str:
    if not value:
        return ""
    local = _as_rome(value)
    text = local.strftime("%d/%m/%Y %H:%M")
    return f"{_WEEKDAYS[local.weekday()]} {text}" if weekday else text


def format_time(value: datetime | str | None) -> str:
    if not value:
        return ""
    return _as_rome(value).strftime("%H:%M")


def format_date(value: date | str | None, weekday: bool = False) -> str:
    if not value:
        return ""
    day = date.fromisoformat(value[:10]) if isinstance(value, str) else value
    text = day.strftime("%d/%m/%Y")
    return f"{_WEEKDAYS[day.weekday()]} {text}" if weekday else text


def format_long_date(day: date) -> str:
    return f"{_WEEKDAYS_LONG[day.weekday()]} {day.day} {_MONTHS[day.month - 1]} {day.year}"


def rome_date_of(value: str) -> date:
    """Giorno di Roma di un timestamp UTC salvato."""
    return from_iso(value).astimezone(ROME).date()
