"""Fixture comuni. Tutti i dati sono sintetici e chiaramente fittizi (dominio example.org, telefoni +39 000)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from flask.testing import FlaskClient

from kite_admissions.app import create_app
from kite_admissions.db import Database

PORT = 8765
BASE_URL = f"http://127.0.0.1:{PORT}"
ORIGIN = BASE_URL
CSRF = "token-di-prova-csrf"


class Clock:
    """Orologio controllabile: di default giovedì 1 ottobre 2026, 08:00 UTC (10:00 a Roma)."""

    def __init__(self, moment: datetime | None = None):
        self.moment = moment or datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs) -> None:
        self.moment = self.moment + timedelta(**kwargs)

    def set(self, moment: datetime) -> None:
        self.moment = moment


class LocalClient(FlaskClient):
    """Client che si comporta come il browser locale: Host/Origin corretti e token CSRF."""

    csrf_ready = False

    def open(self, *args, **kwargs):
        kwargs.setdefault("base_url", BASE_URL)
        method = (kwargs.get("method") or "GET").upper()
        if method == "POST" and kwargs.pop("with_csrf", True):
            if not self.csrf_ready:
                with self.session_transaction(base_url=BASE_URL) as session:
                    session["csrf"] = CSRF
                self.csrf_ready = True
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Origin", ORIGIN)
            kwargs["headers"] = headers
            data = kwargs.get("data")
            if data is None:
                data = {}
            if isinstance(data, dict):
                data = dict(data)
                data.setdefault("csrf_token", CSRF)
                kwargs["data"] = data
        else:
            kwargs.pop("with_csrf", None)
        return super().open(*args, **kwargs)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def home(tmp_path):
    return tmp_path / "kite-home"


@pytest.fixture
def make_app(home, clock):
    def factory(**services):
        app = create_app(home, port=PORT, clock=clock, testing=True, **services)
        app.test_client_class = LocalClient
        return app

    return factory


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    database = Database(app.extensions["kite"].paths.db_path)
    yield database
    database.close()


def extract(html: str, pattern: str) -> str:
    match = re.search(pattern, html)
    assert match, f"pattern non trovato: {pattern}"
    return match.group(1)
