"""Protezioni locali proporzionate (SPEC §10).

- solo loopback: accessi remoti respinti anche se arrivassero;
- Host atteso (contro il DNS rebinding), Origin/Referer della stessa origine sulle modifiche;
- token CSRF di sessione su ogni POST; nessuna modifica via GET;
- intestazioni che impediscono script, immagini remote e incorniciamento.
"""

from __future__ import annotations

import hmac
import secrets
import threading
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlsplit

from flask import abort, request, session

LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD"})
CSRF_FIELD = "csrf_token"

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
    "base-uri 'none'"
)


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


def allowed_origins(port: int) -> frozenset[str]:
    return frozenset(f"http://{host}" for host in allowed_hosts(port))


def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def protect_request(port: int) -> None:
    """Da chiamare in before_request."""
    if request.remote_addr not in LOOPBACK_ADDRESSES:
        abort(403, description="non_local")
    if request.host not in allowed_hosts(port):
        abort(400, description="host")
    if request.method in SAFE_METHODS:
        return
    origins = allowed_origins(port)
    origin = request.headers.get("Origin")
    if origin is not None:
        if origin not in origins:
            abort(403, description="origin")
    else:
        referer = request.headers.get("Referer")
        if referer:
            parts = urlsplit(referer)
            if f"{parts.scheme}://{parts.netloc}" not in origins:
                abort(403, description="origin")
    sent = request.form.get(CSRF_FIELD) or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("csrf") or ""
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="csrf")


def apply_security_headers(response, *, static: bool = False):
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if not static:
        # Pagine con dati personali: niente copie nella cache del browser.
        response.headers["Cache-Control"] = "no-store"
    return response


class GateBusy(Exception):
    """Operazione esclusiva non avviabile: altre richieste non terminano o gate già chiuso."""


class RequestGate:
    """Sospende le richieste durante ripristino e chiusura, attendendo quelle in corso."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._active = 0
        self._closed = False
        self.reason: str | None = None

    def enter(self) -> bool:
        with self._cond:
            if self._closed:
                return False
            self._active += 1
            return True

    def exit(self) -> None:
        with self._cond:
            self._active -= 1
            self._cond.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self, reason: str, timeout: float = 30.0) -> None:
        """Chiude il gate e attende che le richieste in corso terminino."""
        with self._cond:
            if self._closed:
                raise GateBusy(self.reason or "operazione in corso")
            self._closed = True
            self.reason = reason
            if not self._cond.wait_for(lambda: self._active == 0, timeout=timeout):
                self._closed = False
                self.reason = None
                self._cond.notify_all()
                raise GateBusy("richieste ancora in corso")

    def open(self) -> None:
        with self._cond:
            self._closed = False
            self.reason = None
            self._cond.notify_all()

    @contextmanager
    def exclusive(self, reason: str, timeout: float = 30.0) -> Iterator[None]:
        self.close(reason, timeout)
        try:
            yield
        finally:
            self.open()
