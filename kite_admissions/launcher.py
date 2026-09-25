"""Avvio «Avvia KITE Admissions»: una sola istanza, solo loopback, browser di sistema (SPEC §2).

Uso: pythonw -m kite_admissions [--home CARTELLA] [--port PORTA] [--no-browser]
Un secondo avvio non crea un altro server: apre nel browser l'istanza già attiva.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import APP_ID, APP_NAME, __version__
from .paths import Paths

log = logging.getLogger("kite_admissions")

PREFERRED_PORT = 8765
LOOPBACK = "127.0.0.1"


class InstanceLock:
    """Blocco su file tenuto per tutta la vita del processo: impedisce un secondo server."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def acquire(self) -> bool:
        handle = open(self.path, "a+b")
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._handle.close()
        self._handle = None


def bind_loopback(preferred_port: int) -> socket.socket:
    """Socket su 127.0.0.1 con uso esclusivo della porta; se occupata, porta libera casuale."""
    candidates = [preferred_port, 0] if preferred_port else [0]
    last_error: OSError | None = None
    for port in candidates:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((LOOPBACK, port))
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    raise OSError(f"Nessuna porta locale disponibile: {last_error}")


def _configure_logging(paths: Paths) -> None:
    handler = RotatingFileHandler(paths.logs_dir / "kite-admissions.log", maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _notify(message: str) -> None:
    """Messaggio visibile anche senza console (pythonw)."""
    log.error(message)
    if sys.platform == "win32" and not os.environ.get("KITE_ADMISSIONS_NO_DIALOG"):
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    elif sys.stderr:
        print(message, file=sys.stderr)


def _read_instance(paths: Paths) -> dict | None:
    try:
        return json.loads(paths.instance_info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _is_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "health", timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data.get("app") == APP_ID
    except (OSError, ValueError):
        return False


def _open_existing(paths: Paths, open_browser: bool) -> int:
    """Seconda istanza: apre quella già attiva e termina."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        info = _read_instance(paths)
        if info and _is_alive(info["url"]):
            log.info("Istanza già attiva su %s: nessun secondo server", info["url"])
            if open_browser:
                webbrowser.open(info["url"])
            return 0
        time.sleep(0.5)
    _notify("KITE Admissions risulta già avviato ma non risponde. Attendi qualche secondo e riprova.")
    return 1


def _schedule_shutdown(state) -> None:
    """Chiusura richiesta dall'interfaccia: attende le richieste in corso, poi arresta il server."""

    def worker() -> None:
        time.sleep(0.5)  # lascia partire la pagina di conferma
        try:
            state.gate.close("Chiusura dell'applicazione in corso", timeout=15)
        except Exception:  # noqa: BLE001 - la chiusura procede comunque
            pass
        log.info("Chiusura richiesta dall'interfaccia")
        import _thread

        _thread.interrupt_main()
        time.sleep(10)
        os._exit(0)  # ultima garanzia se il ciclo del server non si fosse fermato

    threading.Thread(target=worker, name="kite-shutdown", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kite_admissions", description=f"Avvia {APP_NAME}")
    parser.add_argument("--home", help="cartella dati (predefinita: %%LOCALAPPDATA%%\\KITEAdmissions)")
    parser.add_argument("--port", type=int, default=PREFERRED_PORT, help="porta locale preferita")
    parser.add_argument("--no-browser", action="store_true", help="non aprire il browser")
    args = parser.parse_args(argv)

    paths = Paths.resolve(args.home)
    try:
        paths.ensure()
    except OSError as exc:
        _notify(f"Impossibile preparare la cartella dati {paths.home}: {exc}")
        return 1
    _configure_logging(paths)

    lock = InstanceLock(paths.lock_path)
    if not lock.acquire():
        return _open_existing(paths, not args.no_browser)

    try:
        return _serve(paths, args)
    finally:
        try:
            paths.instance_info_path.unlink()
        except OSError:
            pass
        lock.release()


def _serve(paths: Paths, args: argparse.Namespace) -> int:
    from waitress import create_server

    from .app import create_app
    from .settings import write_json_atomic

    try:
        sock = bind_loopback(args.port)
    except OSError as exc:
        _notify(str(exc))
        return 1
    port = sock.getsockname()[1]
    try:
        app = create_app(paths.home, port=port)
    except Exception as exc:  # noqa: BLE001 - errore mostrato anche senza console
        log.exception("Avvio non riuscito")
        sock.close()
        _notify(f"Avvio di {APP_NAME} non riuscito: {exc}")
        return 1
    state = app.extensions["kite"]
    state.shutdown_callback = lambda: _schedule_shutdown(state)
    server = create_server(app, sockets=[sock], threads=6, channel_timeout=300, ident=APP_NAME)
    url = f"http://{LOOPBACK}:{port}/"
    write_json_atomic(paths.instance_info_path, {"pid": os.getpid(), "port": port, "url": url})
    log.info("%s %s in ascolto su %s (dati: %s)", APP_NAME, __version__, url, paths.home)
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.close()
        except Exception:  # noqa: BLE001
            pass
        log.info("%s arrestato", APP_NAME)
    return 0
