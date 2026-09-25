"""Slice 5: proposte economiche versionate (AC06 e vincoli pertinenti di AC10)."""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from kite_admissions.db import Database
from kite_admissions.services import offers as offer_service
from kite_admissions.services import timeline as timeline_service
from kite_admissions.services.common import ValidationError

from .helpers import create_family, create_lead


def offer_rows(db, family_id):
    return db.all("SELECT * FROM Offer WHERE family_id = ? ORDER BY student_lead_id IS NOT NULL, version_no",
                  (family_id,))


def new_offer(client, family_id, **fields):
    data = {"new_id": str(uuid.uuid4()), "proposed_fee": "1.000,00", "periodicity": "ANNUALE"}
    data.update(fields)
    response = client.post(f"/famiglie/{family_id}/offerte/nuova", data=data)
    assert response.status_code == 302, response.get_data(as_text=True)[:600]
    return data["new_id"]


def communicate(client, db, family_id, offer_id, **extra):
    data = {"revision": db.scalar("SELECT revision FROM Offer WHERE id = ?", (offer_id,))}
    data.update(extra)
    return client.post(f"/famiglie/{family_id}/offerte/{offer_id}/comunica", data=data, follow_redirects=True)


def test_parse_euro_accepts_italian_and_plain_formats():
    cases = {"1.000,50": 100050, "1000,5": 100050, "1000.50": 100050, "1000": 100000, "5.400": 540000,
             "€ 900,00": 90000, "1,250.00": 125000, "0,99": 99}
    for text, cents in cases.items():
        assert offer_service.parse_euro(text, "x", "Importo") == cents, text
    for bad in ("12,345,6", "1.00.0", "abc", "-5"):
        with pytest.raises(ValidationError):
            offer_service.parse_euro(bad, "x", "Importo")


def test_draft_in_family_and_child_scopes_with_structured_services(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Offerte")
    lead_id = create_lead(client, family_id, display_name="Alunno Esempio Offerte")
    family_offer = new_offer(client, family_id, proposed_fee="9.000,00", conditions="Due fratelli (esempio)")
    child_offer = new_offer(client, family_id, student_lead_id=lead_id, standard_fee="5.400,00",
                            proposed_fee="4.900,00", service_description_1="Mensa", service_amount_1="80",
                            service_periodicity_1="MENSILE", service_included_1="0",
                            service_description_2="Materiale didattico", service_included_2="1",
                            valid_until="2026-11-30")
    rows = {row["id"]: row for row in offer_rows(db, family_id)}
    assert rows[family_offer]["student_lead_id"] is None and rows[family_offer]["version_no"] == 1
    child = rows[child_offer]
    assert (child["version_no"], child["status"], child["currency"]) == (1, "BOZZA", "EUR")
    assert (child["standard_fee_cents"], child["proposed_fee_cents"]) == (540000, 490000)
    assert offer_service.services_of(child) == [
        {"description": "Mensa", "amount_cents": 8000, "periodicity": "MENSILE", "included": False},
        {"description": "Materiale didattico", "amount_cents": None, "periodicity": None, "included": True}]
    assert offer_service.discount_cents(child) == 50000
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Proposta familiare" in page and "Per Alunno Esempio Offerte" in page
    assert "sconto 500,00 €" in page and "80,00 € mensile" in page


def test_communicate_once_even_with_double_click(client, db, clock):
    family_id = create_family(client)
    offer_id = new_offer(client, family_id)
    revision = db.scalar("SELECT revision FROM Offer WHERE id = ?", (offer_id,))
    first = client.post(f"/famiglie/{family_id}/offerte/{offer_id}/comunica",
                        data={"revision": revision, "channel": "Di persona"}, follow_redirects=True)
    assert "segnata come comunicata" in first.get_data(as_text=True)
    stamp = db.one("SELECT communicated_at, version_no, revision FROM Offer WHERE id = ?", (offer_id,))
    clock.advance(minutes=3)
    second = client.post(f"/famiglie/{family_id}/offerte/{offer_id}/comunica",
                         data={"revision": revision, "channel": "Email"}, follow_redirects=True)
    assert "già comunicata" in second.get_data(as_text=True)
    after = db.one("SELECT * FROM Offer WHERE id = ?", (offer_id,))
    assert (after["communicated_at"], after["version_no"], after["revision"]) == tuple(stamp)
    assert after["communicated_at"] == "2026-10-01T08:00:00.000000Z" and after["communicated_by"] == "Ionut"
    assert after["communication_channel"] == "Di persona"
    assert db.scalar("SELECT count(*) FROM Offer") == 1


def test_concurrent_communications_register_a_single_timestamp(app, client, db, clock):
    family_id = create_family(client)
    offer_id = new_offer(client, family_id)
    path = app.extensions["kite"].paths.db_path
    results, errors = [], []
    barrier = threading.Barrier(4)

    def worker():
        database = Database(path)
        try:
            barrier.wait()
            results.append(offer_service.communicate(database, offer_id, family_id, revision=1, channel=None,
                                                     now=clock()))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            database.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and sorted(results) == [False, False, False, True]
    assert db.scalar("SELECT status FROM Offer WHERE id = ?", (offer_id,)) == "COMUNICATA"


def test_communicated_offer_is_frozen_in_backend(client, db):
    family_id = create_family(client)
    offer_id = new_offer(client, family_id, proposed_fee="1.000,00")
    communicate(client, db, family_id, offer_id)
    response = client.post(f"/famiglie/{family_id}/offerte/{offer_id}/modifica", data={
        "proposed_fee": "1,00", "periodicity": "ANNUALE", "revision": 2}, follow_redirects=True)
    assert "congelato" in response.get_data(as_text=True)
    assert db.scalar("SELECT proposed_fee_cents FROM Offer WHERE id = ?", (offer_id,)) == 100000
    with pytest.raises(ValidationError):
        offer_service.update_draft(db, offer_id, family_id, offer_service.parse_offer(
            {"proposed_fee": "1,00", "periodicity": "ANNUALE"}), revision=2, now=None)
    with pytest.raises(sqlite3.IntegrityError, match="offer_frozen"):
        db.execute("UPDATE Offer SET conditions = 'manomissione' WHERE id = ?", (offer_id,))
    response = client.post(f"/famiglie/{family_id}/offerte/{offer_id}/elimina", data={"confirm": "1", "revision": 2},
                           follow_redirects=True)
    assert "si ritira" in response.get_data(as_text=True) and db.scalar("SELECT count(*) FROM Offer") == 1


def test_new_version_keeps_previous_as_current_until_communicated(client, db):
    """Esempio della SPEC: v1 comunicata a 1.000 €/anno, v2 in bozza a 900 €/anno."""
    family_id = create_family(client)
    lead_id = create_lead(client, family_id)
    v1 = new_offer(client, family_id, student_lead_id=lead_id, proposed_fee="1.000,00")
    communicate(client, db, family_id, v1)
    v2 = str(uuid.uuid4())
    response = client.post(f"/famiglie/{family_id}/offerte/{v1}/nuova-versione", data={"new_id": v2})
    assert response.status_code == 302 and f"/offerte/{v2}/modifica" in response.headers["Location"]
    copy = db.one("SELECT * FROM Offer WHERE id = ?", (v2,))
    assert (copy["version_no"], copy["status"], copy["previous_offer_id"], copy["proposed_fee_cents"]) == (
        2, "BOZZA", v1, 100000)
    client.post(f"/famiglie/{family_id}/offerte/{v2}/modifica", data={
        "proposed_fee": "900", "periodicity": "ANNUALE", "revision": 1})
    scope = offer_service.family_offer_scopes(db, family_id)[0]
    assert scope.current["id"] == v1 and scope.draft["id"] == v2
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert re.search(r"Proposta corrente: <strong>v1</strong> · 1\.000,00 €", page)
    first_stamp = db.scalar("SELECT communicated_at FROM Offer WHERE id = ?", (v1,))
    communicate(client, db, family_id, v2)
    scope = offer_service.family_offer_scopes(db, family_id)[0]
    assert scope.current["id"] == v2 and scope.current["proposed_fee_cents"] == 90000
    old = db.one("SELECT * FROM Offer WHERE id = ?", (v1,))
    assert old["status"] == "COMUNICATA" and old["communicated_at"] == first_stamp  # conservata e invariata
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Sostituita" in page and re.search(r"Proposta corrente: <strong>v2</strong> · 900,00 €", page)


def test_only_one_draft_per_scope(client, db):
    family_id = create_family(client)
    v1 = new_offer(client, family_id)
    response = client.post(f"/famiglie/{family_id}/offerte/nuova", data={
        "new_id": str(uuid.uuid4()), "proposed_fee": "500", "periodicity": "ANNUALE"})
    assert f"/offerte/{v1}/modifica" in response.headers["Location"]
    communicate(client, db, family_id, v1)
    client.post(f"/famiglie/{family_id}/offerte/{v1}/nuova-versione", data={"new_id": str(uuid.uuid4())})
    response = client.post(f"/famiglie/{family_id}/offerte/{v1}/nuova-versione", data={"new_id": str(uuid.uuid4())})
    assert "/modifica" in response.headers["Location"]
    assert db.scalar("SELECT count(*) FROM Offer WHERE status = 'BOZZA'") == 1
    assert db.scalar("SELECT count(*) FROM Offer") == 2


def test_withdrawal_keeps_history_and_revives_nothing(client, db):
    family_id = create_family(client)
    v1 = new_offer(client, family_id, proposed_fee="1.000")
    communicate(client, db, family_id, v1)
    v2 = str(uuid.uuid4())
    client.post(f"/famiglie/{family_id}/offerte/{v1}/nuova-versione", data={"new_id": v2})
    communicate(client, db, family_id, v2)
    response = client.post(f"/famiglie/{family_id}/offerte/{v2}/ritira", data={"reason": "", "revision": 2},
                           follow_redirects=True)
    assert "motivo" in response.get_data(as_text=True)
    client.post(f"/famiglie/{family_id}/offerte/{v2}/ritira", data={"reason": "Registrazione errata (esempio)",
                                                                   "revision": 2})
    withdrawn = db.one("SELECT * FROM Offer WHERE id = ?", (v2,))
    assert withdrawn["status"] == "RITIRATA" and withdrawn["withdrawal_reason"] == "Registrazione errata (esempio)"
    assert withdrawn["communicated_at"] is not None and withdrawn["withdrawn_at"] == "2026-10-01T08:00:00.000000Z"
    scope = offer_service.family_offer_scopes(db, family_id)[0]
    assert scope.current is None
    assert db.scalar("SELECT status FROM Offer WHERE id = ?", (v1,)) == "COMUNICATA"
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Nessuna proposta corrente" in page
    v3 = str(uuid.uuid4())  # per riproporre le vecchie condizioni si crea una nuova versione
    client.post(f"/famiglie/{family_id}/offerte/{v1}/nuova-versione", data={"new_id": v3})
    repropose = db.one("SELECT * FROM Offer WHERE id = ?", (v3,))
    assert (repropose["version_no"], repropose["previous_offer_id"], repropose["proposed_fee_cents"]) == (3, v1, 100000)


def test_offer_must_be_complete_and_fresh_before_communication(client, db):
    family_id = create_family(client)
    incomplete = str(uuid.uuid4())
    client.post(f"/famiglie/{family_id}/offerte/nuova", data={"new_id": incomplete, "proposed_fee": ""})
    page = communicate(client, db, family_id, incomplete).get_data(as_text=True)
    assert "manca la quota proposta" in page and "manca la periodicità" in page
    assert db.scalar("SELECT status FROM Offer WHERE id = ?", (incomplete,)) == "BOZZA"
    client.post(f"/famiglie/{family_id}/offerte/{incomplete}/modifica", data={
        "proposed_fee": "750", "periodicity": "MENSILE", "revision": 1})
    stale = client.post(f"/famiglie/{family_id}/offerte/{incomplete}/comunica", data={"revision": 1},
                        follow_redirects=True).get_data(as_text=True)
    assert "cambiata dopo l&#39;apertura" in stale
    assert db.scalar("SELECT status FROM Offer WHERE id = ?", (incomplete,)) == "BOZZA"
    response = client.post(f"/famiglie/{family_id}/offerte/nuova", data={
        "new_id": str(uuid.uuid4()), "proposed_fee": "100", "periodicity": "ANNUALE",
        "service_amount_1": "50"})
    assert response.status_code == 422  # importo di servizio senza descrizione


def test_scope_must_belong_to_the_family(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio A")
    other = create_family(client, display_name="Famiglia Esempio B")
    other_lead = create_lead(client, other)
    response = client.post(f"/famiglie/{family_id}/offerte/nuova", data={
        "new_id": str(uuid.uuid4()), "proposed_fee": "100", "periodicity": "ANNUALE", "student_lead_id": other_lead})
    assert response.status_code == 422 and db.scalar("SELECT count(*) FROM Offer") == 0


def test_communicated_offers_appear_in_timeline(client, db):
    family_id = create_family(client)
    offer_id = new_offer(client, family_id, proposed_fee="4.200,00")
    assert not [item for item in timeline_service.family_timeline(db, family_id) if item.kind.startswith("Proposta")]
    communicate(client, db, family_id, offer_id, channel="Telefono")
    items = [item for item in timeline_service.family_timeline(db, family_id) if item.kind == "Proposta comunicata"]
    assert len(items) == 1 and "4.200,00 € annuale" in items[0].title and "Telefono" in items[0].detail


def test_offers_never_send_anything_outside():
    source = Path(offer_service.__file__).read_text(encoding="utf-8")
    web_source = (Path(offer_service.__file__).parents[1] / "web" / "offers.py").read_text(encoding="utf-8")
    for forbidden in ("smtplib", "requests", "urllib.request", "http.client", "socket"):
        assert forbidden not in source and forbidden not in web_source
