"""Correzioni emerse dal collaudo manuale sul server reale."""

from __future__ import annotations

import uuid

from kite_admissions.gcal.fake import FakeCalendarSource
from kite_admissions.services import calendar_import
from kite_admissions.web.filters import rfc_datetime

from . import dataset
from .calendar_data import CAL, timed
from .helpers import create_family


def test_google_update_time_is_shown_in_rome_time():
    assert rfc_datetime("2026-09-22T09:00:00.000Z") == "mar 22/09/2026 11:00"
    assert rfc_datetime("non-una-data") == "non-una-data"
    assert rfc_datetime(None) == ""


def test_unverified_count_uses_singular_and_plural():
    summary = calendar_import.ImportSummary("x", CAL, "a", "b", listing_ok=True, not_verified=1)
    assert summary.problem() == "1 appuntamento non verificato: controlla Google Calendar."
    summary.not_verified = 3
    assert summary.problem() == "3 appuntamenti non verificati: controlla Google Calendar."


def test_calendar_state_is_to_be_rechecked_after_restore(make_app):
    fake = FakeCalendarSource()
    app = make_app(calendar_source=fake)
    client = app.test_client()
    state = app.extensions["kite"]
    state.settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Test"}))
    fake.put(CAL, timed("evt-1", "2026-10-05T09:30:00+02:00"))
    dataset.refresh(app, client)
    assert state.settings.load()["last_import"]["status"] == "OK"
    client.post("/dati/backup")
    backup = next(state.paths.manual_backups_dir.glob("*.zip"))
    client.post(f"/dati/ripristino/manuale/{backup.name}", data={"confirm_text": "RIPRISTINA"})
    assert state.settings.load()["last_import"]["status"] == "RESTORED"
    client.post("/dati/verifica-ripristino", data={"checked": "1"})
    assert "per ricontrollare gli appuntamenti" in client.get("/").get_data(as_text=True)
    fake.list_error = calendar_import.SourceError("network")
    dataset.refresh(app, client)
    last = state.settings.load()["last_import"]
    assert last["status"] == "FAILED" and last["full_check_at"] is None  # nessun controllo completo ereditato


def test_overdue_next_step_is_highlighted(client):
    family_id = create_family(client, display_name="Famiglia Esempio In Ritardo")
    client.post(f"/famiglie/{family_id}/follow-up/nuovo", data={"new_id": str(uuid.uuid4()), "action": "RICHIAMARE",
                                                               "due_on": "2026-09-28"})
    listing = client.get("/famiglie/").get_data(as_text=True)
    assert "Richiamare entro il 28/09/2026" in listing and "Scaduto" in listing
    detail = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Scaduto" in detail.split("Prossimo passo")[1]


def test_unexpected_update_error_is_recorded_and_visible(make_app, monkeypatch):
    fake = FakeCalendarSource()
    app = make_app(calendar_source=fake)
    client = app.test_client()
    state = app.extensions["kite"]
    state.settings.update(lambda d: d.__setitem__("calendar", {"id": CAL, "summary": "Test"}))

    def broken(*args, **kwargs):
        raise RuntimeError("guasto simulato")

    monkeypatch.setattr(calendar_import, "refresh", broken)
    dataset.refresh(app, client)
    assert state.import_runner.error and "nessun dato è stato cancellato" in state.import_runner.error
    last = state.settings.load()["last_import"]
    assert last["status"] == "FAILED" and "Errore imprevisto" in last["problem"]
    assert "Errore imprevisto" in client.get("/appuntamenti/aggiornamento").get_data(as_text=True)


def test_family_subtitle_has_no_dangling_separator(client):
    family_id = create_family(client, display_name="Famiglia Esempio Sottotitolo", primary_adult_name="Adulto Esempio")
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    subtitle = page.split('<div class="sub">')[1].split("</div>")[0]
    assert subtitle.strip() == "Adulto Esempio"
