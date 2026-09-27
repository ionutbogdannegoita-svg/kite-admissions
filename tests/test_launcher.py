"""AC01: avvio reale con Waitress, istanza singola, solo loopback, chiusura dall'interfaccia."""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _start(home: Path) -> subprocess.Popen:
    env = dict(os.environ, KITE_ADMISSIONS_NO_DIALOG="1", PYTHONUTF8="1")
    env.pop("KITE_ADMISSIONS_HOME", None)
    return subprocess.Popen(
        [sys.executable, "-m", "kite_admissions", "--home", str(home), "--port", "0", "--no-browser"],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


def _wait_instance(home: Path, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    info_path = home / "instance.json"
    while time.monotonic() < deadline:
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                with urllib.request.urlopen(info["url"] + "health", timeout=2) as response:
                    if json.loads(response.read())["app"] == "kite-admissions":
                        return info
            except (OSError, ValueError, KeyError):
                pass
        time.sleep(0.2)
    raise AssertionError("l'applicazione non si è avviata")


def test_single_instance_start_and_shutdown(tmp_path):
    home = tmp_path / "home"
    first = _start(home)
    try:
        info = _wait_instance(home)
        assert info["url"].startswith("http://127.0.0.1:")
        assert (home / "data" / "admissions.sqlite3").is_file()

        second = _start(home)
        assert second.wait(timeout=30) == 0  # nessun secondo server: apre quello attivo
        assert json.loads((home / "instance.json").read_text(encoding="utf-8"))["pid"] == info["pid"]
        assert first.poll() is None

        # Host diverso da 127.0.0.1/localhost: respinto.
        request = urllib.request.Request(info["url"] + "health", headers={"Host": "attacker.example"})
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request, timeout=3)
        assert rejected.value.code == 400

        # Chiusura dall'interfaccia, come il pulsante «Chiudi applicazione».
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(info["url"], timeout=5) as response:
            html = response.read().decode("utf-8")
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        body = urllib.parse.urlencode({"csrf_token": token}).encode()
        origin = info["url"].rstrip("/")
        request = urllib.request.Request(info["url"] + "dati/chiudi", data=body, headers={"Origin": origin})
        with opener.open(request, timeout=5) as response:
            assert response.status == 200
        assert first.wait(timeout=30) == 0
        assert not (home / "instance.json").exists()
    finally:
        if first.poll() is None:
            first.kill()
