"""Backup SQLite coerenti e verificati (SPEC §9.2, DEC-011).

Ogni backup è un file .zip autonomo con:
- `admissions.sqlite3`: copia creata con l'API di backup SQLite (mai copia cieca del file aperto);
- `settings.json`: configurazione non segreta (i token Google non sono mai inclusi);
- `manifest.json`: data, tipo, versione schema, conteggi e impronta SHA-256 di ogni tabella.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..db import SQLITE_HEADER, check_connection, open_connection, ordered_rows
from ..paths import Paths
from ..schema import APPLICATION_ID, SCHEMA_VERSION, TABLES
from ..timeutil import ROME, to_iso, utc_now

BACKUP_FORMAT = "kite-admissions-backup"
DB_MEMBER = "admissions.sqlite3"
SETTINGS_MEMBER = "settings.json"
MANIFEST_MEMBER = "manifest.json"

KIND_AUTO = "automatico"
KIND_MANUAL = "manuale"
KIND_PREOP = "pre-operazione"

_NAME_RE = re.compile(r"^kite-admissions-[a-z0-9-]+\.zip$")


class BackupError(Exception):
    """Backup non creato o non valido; il messaggio è mostrabile."""


@dataclass
class BackupInfo:
    path: Path
    kind: str
    created_at: str
    schema_version: int | None
    operation: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @property
    def name(self) -> str:
        return self.path.name


def kind_dir(paths: Paths, kind: str) -> Path:
    return {
        KIND_AUTO: paths.auto_backups_dir,
        KIND_MANUAL: paths.manual_backups_dir,
        KIND_PREOP: paths.preop_backups_dir,
    }[kind]


def table_digests(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Conteggio e impronta di ogni tabella, con righe in ordine di chiave primaria."""
    result: dict[str, dict[str, Any]] = {}
    for table in TABLES:
        digest = hashlib.sha256()
        count = 0
        for row in ordered_rows(conn, table):
            digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        result[table] = {"count": count, "sha256": digest.hexdigest()}
    return result


def snapshot_database(db_path: Path, dest: Path) -> dict[str, Any]:
    """Copia coerente del database in uso tramite l'API di backup, poi verificata."""
    source = open_connection(db_path)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
            problems = check_connection(target)
            if problems:
                raise BackupError("La copia non supera la verifica di integrità: " + "; ".join(problems))
            version = target.execute("PRAGMA user_version").fetchone()[0]
            app_id = target.execute("PRAGMA application_id").fetchone()[0]
            if app_id != APPLICATION_ID:
                raise BackupError("Il database attivo non è riconosciuto come KITE Admissions.")
            digests = table_digests(target)
        finally:
            target.close()
    finally:
        source.close()
    return {"schema_version": version, "tables": digests}


def _stamp_local(now: datetime) -> str:
    return now.astimezone(ROME).strftime("%Y%m%d-%H%M%S")


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise BackupError("Impossibile trovare un nome libero per il backup.")


def backup_name(kind: str, now: datetime, operation: str | None = None) -> str:
    if kind == KIND_AUTO:
        return f"kite-admissions-automatico-{now.astimezone(ROME).strftime('%Y-%m-%d')}.zip"
    if kind == KIND_MANUAL:
        return f"kite-admissions-manuale-{_stamp_local(now)}.zip"
    return f"kite-admissions-pre-{operation or 'operazione'}-{_stamp_local(now)}.zip"


def create_backup(
    paths: Paths,
    settings_data: dict[str, Any],
    kind: str,
    *,
    operation: str | None = None,
    now: datetime | None = None,
    replace: bool = False,
) -> BackupInfo:
    """Crea un backup verificato. Scrive su file temporaneo e sostituisce atomicamente."""
    now = now or utc_now()
    destination_dir = kind_dir(paths, kind)
    destination_dir.mkdir(parents=True, exist_ok=True)
    final_path = destination_dir / backup_name(kind, now, operation)
    if not replace:
        final_path = _unique(final_path)
    workdir = Path(tempfile.mkdtemp(prefix=".tmp-", dir=paths.backups_dir))
    try:
        temp_db = workdir / DB_MEMBER
        try:
            snapshot = snapshot_database(paths.db_path, temp_db)
        except sqlite3.Error as exc:
            raise BackupError(f"Copia del database non riuscita: {exc}") from exc
        manifest = {
            "format": BACKUP_FORMAT,
            "format_version": 1,
            "kind": kind,
            "operation": operation,
            "created_at": to_iso(now),
            "app_version": __version__,
            "schema_version": snapshot["schema_version"],
            "tables": snapshot["tables"],
        }
        safe_settings = {key: value for key, value in settings_data.items()}
        temp_zip = workdir / "backup.zip"
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(temp_db, DB_MEMBER)
            archive.writestr(SETTINGS_MEMBER, json.dumps(safe_settings, ensure_ascii=False, indent=2))
            archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, ensure_ascii=False, indent=2))
        with open(temp_zip, "r+b") as handle:  # su Windows fsync richiede accesso in scrittura
            os.fsync(handle.fileno())
        try:
            os.replace(temp_zip, final_path)
        except OSError as exc:
            raise BackupError(f"Impossibile salvare il backup ({exc.__class__.__name__}).") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    counts = {table: info["count"] for table, info in manifest["tables"].items()}
    return BackupInfo(final_path, kind, manifest["created_at"], manifest["schema_version"], operation, counts)


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read(MANIFEST_MEMBER).decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise BackupError(f"Backup illeggibile ({exc.__class__.__name__}).") from exc
    if manifest.get("format") != BACKUP_FORMAT:
        raise BackupError("Il file non è un backup KITE Admissions.")
    return manifest


def list_backups(paths: Paths) -> list[BackupInfo]:
    """Backup presenti, dal più recente. I file illeggibili sono elencati con l'errore."""
    found: list[BackupInfo] = []
    for kind in (KIND_AUTO, KIND_MANUAL, KIND_PREOP):
        folder = kind_dir(paths, kind)
        if not folder.is_dir():
            continue
        for path in folder.glob("*.zip"):
            try:
                manifest = read_manifest(path)
            except BackupError as exc:
                found.append(BackupInfo(path, kind, "", None, error=str(exc)))
                continue
            counts = {table: info.get("count", 0) for table, info in manifest.get("tables", {}).items()}
            found.append(
                BackupInfo(path, kind, manifest.get("created_at", ""), manifest.get("schema_version"),
                           manifest.get("operation"), counts)
            )
    found.sort(key=lambda item: item.created_at, reverse=True)
    return found


def any_backup(paths: Paths) -> bool:
    return any(item.error is None for item in list_backups(paths))


def find_backup(paths: Paths, kind: str, name: str) -> Path:
    """Percorso di un backup scelto nell'interfaccia, senza uscire dalla sua cartella."""
    if kind not in (KIND_AUTO, KIND_MANUAL, KIND_PREOP) or not _NAME_RE.match(name):
        raise BackupError("Backup non trovato.")
    folder = kind_dir(paths, kind).resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise BackupError("Backup non trovato.")
    return path


@dataclass
class ValidatedBackup:
    db_file: Path
    settings: dict[str, Any]
    manifest: dict[str, Any]


def validate_backup(path: Path, workdir: Path) -> ValidatedBackup:
    """Estrae e verifica un backup prima di usarlo: apribile, integro, FK, schema, impronte."""
    manifest = read_manifest(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if DB_MEMBER not in names:
                raise BackupError("Il backup non contiene il database.")
            db_file = workdir / DB_MEMBER
            with archive.open(DB_MEMBER) as source, open(db_file, "wb") as target:
                shutil.copyfileobj(source, target)
            settings = {}
            if SETTINGS_MEMBER in names:
                settings = json.loads(archive.read(SETTINGS_MEMBER).decode("utf-8"))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise BackupError(f"Backup illeggibile ({exc.__class__.__name__}).") from exc
    with open(db_file, "rb") as handle:
        if not handle.read(16).startswith(SQLITE_HEADER):
            raise BackupError("Il database nel backup non è un file SQLite valido.")
    try:
        conn = sqlite3.connect(db_file)
        try:
            problems = check_connection(conn)
            if problems:
                raise BackupError("Backup non integro: " + "; ".join(problems))
            if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise BackupError("Il backup non contiene un database KITE Admissions.")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if not 1 <= version <= SCHEMA_VERSION:
                raise BackupError(f"Schema del backup ({version}) non compatibile con questa versione ({SCHEMA_VERSION}).")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
            missing = [table for table in TABLES if table not in tables]
            if missing:
                raise BackupError("Nel backup mancano tabelle: " + ", ".join(missing))
            digests = table_digests(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise BackupError(f"Il database nel backup non si apre: {exc}") from exc
    if digests != manifest.get("tables"):
        raise BackupError("Il contenuto del backup non corrisponde al suo manifest.")
    if not isinstance(settings, dict):
        settings = {}
    return ValidatedBackup(db_file, settings, manifest)
