"""Protezioni locali (SPEC §10, AC10): loopback, Host, Origin, CSRF, intestazioni, niente risorse esterne."""

from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

from kite_admissions.launcher import bind_loopback

from .conftest import BASE_URL, CSRF

TEMPLATES = Path(__file__).resolve().parents[1] / "kite_admissions" / "templates"


def test_post_without_csrf_token_is_rejected(client):
    client.get("/")
    response = client.post("/dati/chiudi", data={"csrf_token": "sbagliato"})
    assert response.status_code == 400
    response = client.post("/dati/chiudi", with_csrf=False, headers={"Origin": BASE_URL})
    assert response.status_code == 400


def test_post_from_external_page_is_rejected(client, app):
    calls = []
    app.extensions["kite"].shutdown_callback = lambda: calls.append(1)
    response = client.post("/dati/chiudi", headers={"Origin": "https://sito-esterno.example"})
    assert response.status_code == 403
    response = client.post("/dati/chiudi", headers={"Origin": "http://127.0.0.1:9999"})
    assert response.status_code == 403
    assert calls == []


def test_foreign_referer_without_origin_is_rejected(client, app):
    client.post("/dati/chiudi", data={"csrf_token": "x"})  # prepara la sessione
    response = client.open("/dati/chiudi", method="POST", with_csrf=False,
                           data={"csrf_token": CSRF},
                           headers={"Referer": "https://sito-esterno.example/pagina"})
    assert response.status_code == 403


def test_unexpected_host_is_rejected_against_dns_rebinding(client):
    response = client.get("/", base_url="http://attacker.example:8765")
    assert response.status_code == 400
    assert "csrf" not in response.get_data(as_text=True)


def test_non_loopback_client_is_rejected(client):
    response = client.get("/", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert response.status_code == 403


def test_mutations_are_not_possible_with_get(client, app):
    calls = []
    app.extensions["kite"].shutdown_callback = lambda: calls.append(1)
    assert client.get("/dati/chiudi").status_code == 405
    assert calls == []


def test_valid_local_post_is_accepted(client, app):
    calls = []
    app.extensions["kite"].shutdown_callback = lambda: calls.append(1)
    response = client.post("/dati/chiudi")
    assert response.status_code == 200
    assert calls == [1]


def test_security_headers(client):
    response = client.get("/")
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response.headers


def test_session_cookie_is_local_and_strict(client):
    response = client.get("/")
    cookie = response.headers.get("Set-Cookie", "")
    assert "kite_admissions_session=" in cookie
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_templates_load_no_external_resources():
    offenders = []
    for template in TEMPLATES.rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        for match in re.finditer(r"(?:src|href)\s*=\s*\"(https?://[^\"]*)\"", text):
            offenders.append(f"{template.name}: {match.group(1)}")
        if "<script>" in text or "onclick=" in text:
            offenders.append(f"{template.name}: script inline")
    assert offenders == []


def test_server_socket_listens_only_on_loopback():
    sock = bind_loopback(0)
    try:
        sock.listen(1)
        host, port = sock.getsockname()
        assert host == "127.0.0.1"
        try:
            lan_ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            lan_ip = "127.0.0.1"
        if lan_ip.startswith("127."):
            pytest.skip("nessun indirizzo di rete locale su questo PC")
        probe = socket.socket()
        probe.settimeout(1)
        with pytest.raises(OSError):
            probe.connect((lan_ip, port))
        probe.close()
    finally:
        sock.close()


def test_port_cannot_be_taken_twice():
    first = bind_loopback(0)
    try:
        port = first.getsockname()[1]
        second = bind_loopback(port)  # porta occupata: ne sceglie un'altra
        try:
            assert second.getsockname()[1] != port
        finally:
            second.close()
    finally:
        first.close()
