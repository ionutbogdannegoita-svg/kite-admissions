"""Ripristino da backup verificato, anche con database corrente assente o danneggiato (SPEC §9.2, B2).

1. Il backup scelto si apre, si verifica (integrità, foreign key, schema, impronte) prima di tutto.
2. Database corrente sano: copia preventiva obbligatoria; se fallisce, il database non si tocca.
   Assente: nessuna copia. Danneggiato/non leggibile: si conserva il file quando possibile,
   senza che l'impossibilità blocchi il ripristino.
3. Sostituzione atomica dopo aver rimosso eventuali journal residui, poi nuova verifica di
   integrità, foreign key e contenuto rispetto al manifest. Se fallisce, nessun esito positivo.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import db as dbmod
from ..paths import Paths
from ..settings import DATA_KEYS, SettingsStore
from ..timeutil import ROME, to_iso
from . import backups

_JOURNAL_SUFFIXES = ("-journal", "-wal", "-shm")


class RestoreError(Exception):
    """Ripristino non eseguito o non verificato; il messaggio è mostrabile."""


@dataclass
class RestoreReport:
    backup_name: str
    backup_created_at: str
    restored_at: str
    previous_state: str
    preop_backup: str | None = None
    preserved: list[str] = field(default_factory=list)
    preserve_error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    reappeared: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    migrated: bool = False

    def as_settings(self) -> dict[str, Any]:
        return {
            "restored_at": self.restored_at,
            "backup_name": self.backup_name,
            "backup_created_at": self.backup_created_at,
            "previous_state": self.previous_state,
            "preop_backup": self.preop_backup,
            "preserved": self.preserved,
            "preserve_error": self.preserve_error,
            "reappeared": self.reappeared,
            "missing": self.missing,
        }


def _family_labels(db_path: Path) -> dict[str, str] | None:
    try:
        conn = dbmod.open_connection(db_path)
    except (sqlite3.Error, dbmod.DatabaseError):
        return None
    try:
        return {row["id"]: row["display_name"] for row in conn.execute("SELECT id, display_name FROM Family")}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def preserve_current(paths: Paths, now: datetime) -> list[str]:
    """Copia a parte del database danneggiato (e dei suoi journal), senza richiederne l'integrità."""
    stamp = now.astimezone(ROME).strftime("%Y%m%d-%H%M%S")
    target_dir = paths.preserved_dir / f"prima-del-ripristino-{stamp}"
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for suffix in ("",) + _JOURNAL_SUFFIXES:
        source = Path(str(paths.db_path) + suffix)
        if source.exists():
            destination = target_dir / source.name
            shutil.copy2(source, destination)
            saved.append(str(destination))
    return saved


def _remove_stale_journals(db_path: Path) -> None:
    """Un journal residuo verrebbe applicato al file ripristinato e lo danneggerebbe."""
    for suffix in _JOURNAL_SUFFIXES:
        journal = Path(str(db_path) + suffix)
        if journal.exists():
            journal.unlink()


def _swap_in(db_path: Path, prepared: Path) -> None:
    """Sostituisce il database. Se un altro programma tiene aperto il file, si ferma senza toccarlo."""
    displaced = db_path.with_name(db_path.name + ".sostituito.tmp")
    if db_path.exists():
        try:
            if displaced.exists():
                displaced.unlink()
            os.replace(db_path, displaced)
        except OSError as exc:
            prepared.unlink(missing_ok=True)
            raise RestoreError("Impossibile sostituire il database: è aperto da un altro programma? "
                               f"Chiudilo e riprova ({exc.__class__.__name__}).") from exc
    try:
        _remove_stale_journals(db_path)
        os.replace(prepared, db_path)
    except OSError as exc:
        if displaced.exists() and not db_path.exists():
            os.replace(displaced, db_path)
        prepared.unlink(missing_ok=True)
        raise RestoreError(f"Impossibile completare la sostituzione del database ({exc.__class__.__name__}).") from exc
    displaced.unlink(missing_ok=True)


def restore(
    paths: Paths,
    settings: SettingsStore,
    backup_path: Path,
    *,
    now: datetime,
    preop_backup: Callable[[], backups.BackupInfo],
) -> RestoreReport:
    workdir = Path(tempfile.mkdtemp(prefix=".tmp-ripristino-", dir=paths.backups_dir))
    try:
        try:
            validated = backups.validate_backup(backup_path, workdir)
        except backups.BackupError as exc:
            raise RestoreError(f"Backup non utilizzabile: {exc}") from exc
        manifest = validated.manifest

        current = dbmod.inspect(paths.db_path)
        report = RestoreReport(backup_name=backup_path.name, backup_created_at=manifest.get("created_at", ""),
                               restored_at=to_iso(now), previous_state=current.status)
        before = None
        if current.healthy:
            try:
                info = preop_backup()
            except backups.BackupError as exc:
                raise RestoreError(f"Copia preventiva del database attuale non riuscita: ripristino annullato ({exc}).") from exc
            report.preop_backup = info.name
            before = _family_labels(paths.db_path)
        elif current.status != "missing":
            try:
                report.preserved = preserve_current(paths, now)
            except OSError as exc:
                report.preserve_error = f"Il file danneggiato non è stato conservato ({exc.__class__.__name__})."

        temp_target = paths.data_dir / (paths.db_path.name + ".ripristino.tmp")
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(validated.db_file, temp_target)
        with open(temp_target, "r+b") as handle:
            os.fsync(handle.fileno())
        _swap_in(paths.db_path, temp_target)

        # Verifica dopo la sostituzione: integrità, foreign key e contenuto identico al backup.
        restored = dbmod.inspect(paths.db_path)
        if not restored.healthy:
            raise RestoreError(f"Verifica dopo il ripristino fallita: {restored.detail or restored.status}.")
        conn = dbmod.open_connection(paths.db_path)
        try:
            digests = backups.table_digests(conn)
        finally:
            conn.close()
        if digests != manifest.get("tables"):
            raise RestoreError("Verifica dopo il ripristino fallita: i dati non corrispondono al backup scelto.")
        report.counts = {table: info["count"] for table, info in digests.items()}
        if restored.status == "needs_migration":
            dbmod.migrate(paths.db_path, preop_backup)
            report.migrated = True

        after = _family_labels(paths.db_path) or {}
        if before is not None:
            report.reappeared = sorted(after[key] for key in after.keys() - before.keys())
            report.missing = sorted(before[key] for key in before.keys() - after.keys())

        backup_settings = validated.settings

        def mutate(data: dict[str, Any]) -> None:
            for key in DATA_KEYS:
                if key in backup_settings:
                    data[key] = backup_settings[key]
            data["last_import"] = None  # lo stato Calendar va ricontrollato dopo il ripristino
            data["post_restore"] = report.as_settings()

        settings.update(mutate)
        return report
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
