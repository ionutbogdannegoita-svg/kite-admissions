"""Percorsi locali: codice e dati separati (SPEC §9.1)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "KITE_ADMISSIONS_HOME"

# Cartelle con indizio di sincronizzazione cloud o di rete: il database attivo non deve stare lì.
_SYNCED_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "box sync")


def default_home() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "KITEAdmissions"


@dataclass(frozen=True)
class Paths:
    home: Path

    @classmethod
    def resolve(cls, home: str | os.PathLike | None = None) -> "Paths":
        chosen = home or os.environ.get(ENV_HOME) or default_home()
        return cls(Path(chosen).resolve())

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "admissions.sqlite3"

    @property
    def settings_path(self) -> Path:
        return self.home / "settings.json"

    @property
    def backups_dir(self) -> Path:
        return self.home / "backups"

    @property
    def auto_backups_dir(self) -> Path:
        return self.backups_dir / "automatici"

    @property
    def manual_backups_dir(self) -> Path:
        return self.backups_dir / "manuali"

    @property
    def preop_backups_dir(self) -> Path:
        return self.backups_dir / "pre-operazione"

    @property
    def preserved_dir(self) -> Path:
        """Copie dei database danneggiati conservate durante un ripristino (SPEC §9.2, B2)."""
        return self.backups_dir / "preservati"

    @property
    def exports_dir(self) -> Path:
        return self.home / "export"

    @property
    def google_dir(self) -> Path:
        """Credenziali Google protette: mai incluse in backup o export (SPEC §4.1)."""
        return self.home / "google"

    @property
    def logs_dir(self) -> Path:
        return self.home / "log"

    @property
    def lock_path(self) -> Path:
        return self.home / "instance.lock"

    @property
    def instance_info_path(self) -> Path:
        return self.home / "instance.json"

    def ensure(self) -> None:
        for folder in (
            self.home,
            self.data_dir,
            self.auto_backups_dir,
            self.manual_backups_dir,
            self.preop_backups_dir,
            self.preserved_dir,
            self.exports_dir,
            self.google_dir,
            self.logs_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def location_warning(self) -> str | None:
        """Avviso se i dati risiedono in una cartella sincronizzata o di rete."""
        text = str(self.home)
        if text.startswith("\\\\"):
            return "La cartella dati è su una condivisione di rete: spostala su un disco locale."
        lowered = text.lower()
        for marker in _SYNCED_MARKERS:
            if marker in lowered:
                return "La cartella dati sembra sincronizzata nel cloud: il database attivo deve restare fuori."
        return None
