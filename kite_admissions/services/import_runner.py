"""Una sola operazione Calendar alla volta, con avanzamento e riepilogo (SPEC §4.2).

L'aggiornamento parte solo dal pulsante e gira in un thread finché non termina: non esiste
alcun servizio in background, scheduler o nuovo tentativo automatico.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterator

from .calendar_import import ImportSummary, Preview

log = logging.getLogger("kite_admissions")


class RunnerBusy(Exception):
    pass


class ImportRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.progress: dict[str, Any] = {}
        self.summary: ImportSummary | None = None
        self.preview: Preview | None = None
        self.error: str | None = None
        self.started_at: datetime | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def report(self, **values: Any) -> None:
        self.progress.update(values)

    def start(self, job: Callable[[Callable[..., None]], tuple[ImportSummary, Preview | None]],
              started_at: datetime) -> bool:
        """Avvia l'aggiornamento; False se un'altra operazione Calendar è in corso."""
        if not self._lock.acquire(blocking=False):
            return False
        self.progress = {"phase": "Avvio", "pages": 0, "to_verify": 0, "verified": 0}
        self.error = None
        self.started_at = started_at

        def target() -> None:
            try:
                summary, preview = job(self.report)
                self.summary = summary
                if preview is not None:
                    self.preview = preview
            except Exception:  # noqa: BLE001 - riportato nell'interfaccia, dettagli nel log
                log.exception("Aggiornamento Calendar non riuscito")
                self.error = "Errore imprevisto durante l'aggiornamento: nessun dato è stato cancellato."
            finally:
                self._lock.release()

        self._thread = threading.Thread(target=target, name="kite-calendar-update", daemon=True)
        self._thread.start()
        return True

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Per import selezionati e ripristino: mai insieme a un aggiornamento in corso."""
        if not self._lock.acquire(blocking=False):
            raise RunnerBusy("Aggiornamento da Google Calendar in corso: attendi che termini.")
        try:
            yield
        finally:
            self._lock.release()

    def clear_preview(self) -> None:
        self.preview = None
