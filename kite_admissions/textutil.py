"""Testo: recapiti normalizzati, testo sicuro dalle descrizioni HTML, estrazione deterministica."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from .db import fold

_EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]{2,}$")
_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Numeri di telefono nel testo: prefisso internazionale opzionale e gruppi di cifre.
_PHONE_IN_TEXT = re.compile(r"(?<![\w+])(?:\+|00)?\d[\d .\-/()]{6,18}\d(?!\w)")
# Date (25/09/2026, 25.09.26, 2026-09-25), anni scolastici e intervalli di anni (2026/2027, 2019-2021):
# mai proposti come telefoni.
_DATE_IN_TEXT = re.compile(
    r"(?<![\d./-])(?:(?P<d>\d{1,2})(?P<s1>[./-])(?P<m>\d{1,2})(?P=s1)(?:\d{4}|\d{2})"
    r"|(?:19|20)\d{2}(?P<s2>[./-])(?P<im>\d{1,2})(?P=s2)(?P<id>\d{1,2})"
    r"|(?P<y1>(?:19|20)\d{2}) ?[/-] ?(?P<y2>(?:19|20)\d{2}))(?![\d./-]*\d)"
)
_LABEL_NOISE = re.compile(r"\b(famiglia|fam|family)\b\.?", re.IGNORECASE)


def normalize_phone(raw: str | None) -> str | None:
    """Forma confrontabile di un numero: '+39 333 123 4567' e '333 1234567' -> '+393331234567'.

    Restituisce None se il testo non contiene un numero plausibile.
    """
    if not raw:
        return None
    text = raw.strip()
    digits = re.sub(r"\D", "", text)
    international = text.startswith("+")
    if text.startswith("00"):
        digits = digits[2:]
        international = True
    if international or len(digits) > 11:
        return "+" + digits if 8 <= len(digits) <= 15 else None
    if 6 <= len(digits) <= 11:
        return "+39" + digits
    return None


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    return value if _EMAIL_RE.match(value) else None


def phone_digits(query: str) -> str:
    return re.sub(r"\D", "", query or "")


def label_key(value: str | None) -> str:
    """Chiave per confrontare etichette: 'Famiglia Rossì' ~ 'rossi'."""
    text = fold(value or "") or ""
    text = _LABEL_NOISE.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def similar_labels(first: str | None, second: str | None) -> bool:
    a, b = label_key(first), label_key(second)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= 4 and f" {shorter} " in f" {longer} "


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class _TextExtractor(HTMLParser):
    """Converte HTML in testo: nessun tag, script o immagine sopravvive."""

    _BREAKS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}
    _SKIP = {"script", "style", "head", "title", "iframe", "object", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._href = ""

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAKS:
            self.parts.append("\n")
        elif tag == "a":
            # Il testo del collegamento resta; l'indirizzo è mostrato come testo, mai cliccabile.
            href = dict(attrs).get("href") or ""
            self._href = href if href.startswith(("http://", "https://", "mailto:", "tel:")) else ""

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAKS:
            self.parts.append("\n")
        elif tag == "a" and self._href:
            target = self._href.removeprefix("mailto:").removeprefix("tel:")
            if target and target not in "".join(self.parts[-3:]):
                self.parts.append(f" ({target})")
            self._href = ""

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Descrizione Calendar (anche HTML) come testo semplice da mostrare in modo sicuro."""
    if not value:
        return ""
    if "<" not in value and "&" not in value:
        return value.strip()
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # noqa: BLE001 - HTML malformato: si ripiega su testo senza tag
        text = html.unescape(re.sub(r"<[^>]*>", " ", value))
    lines = [" ".join(line.split()) for line in text.replace("\xa0", " ").splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if line or (cleaned and cleaned[-1]):
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _is_date(match: re.Match) -> bool:
    if match.group("y1"):
        return int(match.group("y1")) < int(match.group("y2"))
    day, month = (match.group("d"), match.group("m")) if match.group("d") else (match.group("id"), match.group("im"))
    return 1 <= int(day) <= 31 and 1 <= int(month) <= 12


def _mask_dates(text: str) -> str:
    """Separa date e anni scolastici dal resto del testo: non diventano né spezzano telefoni."""
    return _DATE_IN_TEXT.sub(lambda match: " | " if _is_date(match) else match.group(0), text)


def find_phones(text: str | None) -> list[tuple[str, str]]:
    """Coppie (come scritto, normalizzato) trovate nel testo, senza duplicati."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _PHONE_IN_TEXT.finditer(_mask_dates(text or "")):
        raw = match.group(0).strip()
        digits = phone_digits(raw)
        if len(digits) < 8 or re.fullmatch(r"(19|20)\d{2}[01]\d[0-3]\d", digits):
            continue  # troppo corto o simile a una data
        normalized = normalize_phone(raw)
        if normalized and normalized not in seen:
            seen.add(normalized)
            found.append((raw, normalized))
    return found


def find_emails(text: str | None) -> list[str]:
    found: list[str] = []
    for match in _EMAIL_IN_TEXT.finditer(text or ""):
        value = match.group(0).strip(".").lower()
        if value not in found:
            found.append(value)
    return found
