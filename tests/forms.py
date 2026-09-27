"""Lettura di un modulo HTML come lo invierebbe il browser (per i test del colloquio, v1.1)."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

_TEXT_TYPES = {"hidden", "text", "date", "datetime-local", "number", "search", "email", "tel"}


class _FormParser(HTMLParser):
    def __init__(self, form_id: str):
        super().__init__(convert_charrefs=True)
        self.form_id = form_id
        self.inside = False
        self.pairs: list[tuple[str, str]] = []
        self.buttons: list[tuple[str, str]] = []
        self._select: dict[str, Any] | None = None
        self._option: dict[str, Any] | None = None
        self._textarea: dict[str, Any] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == self.form_id:
            self.inside = True
            return
        if not self.inside:
            return
        name = attrs.get("name")
        if tag == "input" and name and "disabled" not in attrs:
            kind = (attrs.get("type") or "text").lower()
            if kind in _TEXT_TYPES:
                self.pairs.append((name, attrs.get("value") or ""))
            elif kind in ("checkbox", "radio") and "checked" in attrs:
                self.pairs.append((name, attrs["value"] if attrs.get("value") is not None else "on"))
        elif tag == "button" and name:
            self.buttons.append((name, attrs.get("value") or ""))
        elif tag == "select" and name:
            self._select = {"name": name, "selected": None, "first": None}
        elif tag == "option" and self._select is not None:
            self._option = {"value": attrs.get("value"), "text": "", "selected": "selected" in attrs}
        elif tag == "textarea" and name:
            self._textarea = {"name": name, "text": ""}

    def handle_data(self, data):
        if self._option is not None:
            self._option["text"] += data
        if self._textarea is not None:
            self._textarea["text"] += data

    def handle_endtag(self, tag):
        if tag == "form" and self.inside:
            self.inside = False
        elif tag == "option" and self._option is not None and self._select is not None:
            value = self._option["value"] if self._option["value"] is not None else self._option["text"].strip()
            if self._select["first"] is None:
                self._select["first"] = value
            if self._option["selected"]:
                self._select["selected"] = value
            self._option = None
        elif tag == "select" and self._select is not None:
            chosen = self._select["selected"] if self._select["selected"] is not None else self._select["first"]
            if chosen is not None:
                self.pairs.append((self._select["name"], chosen))
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            text = self._textarea["text"]
            self.pairs.append((self._textarea["name"], text[1:] if text.startswith("\n") else text))
            self._textarea = None


def read_form(html: str, form_id: str) -> dict[str, Any]:
    """Campi del modulo con i valori mostrati: stringa, oppure lista per i nomi ripetuti."""
    parser = _FormParser(form_id)
    parser.feed(html)
    result: dict[str, Any] = {}
    for name, value in parser.pairs:
        if name in result:
            current = result[name]
            result[name] = (current if isinstance(current, list) else [current]) + [value]
        else:
            result[name] = value
    return result


def buttons(html: str, form_id: str) -> list[tuple[str, str]]:
    parser = _FormParser(form_id)
    parser.feed(html)
    return parser.buttons


def with_changes(fields: dict[str, Any], changes: dict[str, Any] | None = None, **more: Any) -> dict[str, Any]:
    """Copia dei campi con alcuni valori cambiati; None toglie il campo (casella non spuntata)."""
    data = {key: (list(value) if isinstance(value, list) else value) for key, value in fields.items()}
    for key, value in {**(changes or {}), **more}.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data
