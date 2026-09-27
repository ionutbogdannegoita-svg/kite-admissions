"""v1.1 — Slice 3: economia in tre blocchi, riduzioni motivate, quota d'iscrizione, autorizzazione (IW06)."""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import uuid
import zipfile

import pytest

from kite_admissions.services import offers as offer_service
from kite_admissions.services.common import ValidationError

from .helpers import create_family, create_lead, revision

INTERVIEW = "/appuntamenti/00000000-0000-4000-8000-000000000000/colloquio?sezione=i"


def new_offer(client, family_id, expect=302, **fields):
    data = {"new_id": str(uuid.uuid4()), "proposed_fee": "4.860,00", "periodicity": "ANNUALE"}
    data.update(fields)
    response = client.post(f"/famiglie/{family_id}/offerte/nuova", data=data)
    assert response.status_code == expect, response.get_data(as_text=True)[:800]
    return data["new_id"]


def offer(db, offer_id):
    return db.one("SELECT * FROM Offer WHERE id = ?", (offer_id,))


def communicate(client, db, family_id, offer_id, **extra):
    data = {"revision": revision(db, "Offer", offer_id), **extra}
    return client.post(f"/famiglie/{family_id}/offerte/{offer_id}/comunica", data=data,
                       follow_redirects=True).get_data(as_text=True)


def edit(client, db, family_id, offer_id, **fields):
    data = {"proposed_fee": "4.860,00", "periodicity": "ANNUALE", "revision": revision(db, "Offer", offer_id)}
    data.update(fields)
    return client.post(f"/famiglie/{family_id}/offerte/{offer_id}/modifica", data=data)


@pytest.fixture
def family(client):
    family_id = create_family(client, display_name="Famiglia Esempio Economia")
    lead_id = create_lead(client, family_id, display_name="Luca Esempio", school_year="2027/2028",
                          grade="Primaria 1ª")
    return family_id, lead_id


def test_offer_form_shows_the_three_blocks_with_scope(client, family):
    family_id, lead_id = family
    page = client.get(f"/famiglie/{family_id}/offerte/nuova?ambito={lead_id}").get_data(as_text=True)
    for text in ("Listino standard", "Condizione riservata", "Importo finale comunicato", "Autorizzata da",
                 "Quota d'iscrizione", "Retta finale comunicata", "nessuna riduzione viene calcolata"):
        assert text in page, text
    assert 'name="reduction_reason_4"' in page and 'name="reduction_reason_5"' not in page
    assert "Per Luca Esempio · Primaria 1ª · 2027/2028" in page
    assert "Promozione autorizzata (serve «Autorizzata da»)" in page and "Fratelli</option>" in page


def test_reductions_and_enrollment_fee_are_saved_exactly_as_written(client, db, family):
    family_id, lead_id = family
    offer_id = new_offer(client, family_id, student_lead_id=lead_id, standard_fee="5.400,00",
                         reduction_reason_1="FRATELLI", reduction_amount_1="540",
                         reduction_reason_2="PAGAMENTO_ANNUALE", reduction_amount_2="100,00",
                         reduction_note_2="saldo a settembre", proposed_fee="4.760,00", enrollment_fee="300",
                         valid_until="2026-10-31", conditions="In 10 rate (esempio)")
    row = offer(db, offer_id)
    assert json.loads(row["reductions"]) == [
        {"reason": "FRATELLI", "amount_cents": 54000, "note": ""},
        {"reason": "PAGAMENTO_ANNUALE", "amount_cents": 10000, "note": "saldo a settembre"}]
    assert (row["standard_fee_cents"], row["proposed_fee_cents"], row["enrollment_fee_cents"]) == (540000, 476000, 30000)
    assert row["authorized_by"] is None and offer_service.consistency_warning(row) is None
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Per Luca Esempio · Primaria 1ª · 2027/2028" in page
    blocks = page.split('class="price-blocks"')[1]
    assert "Listino standard" in blocks and "Retta 5.400,00 €" in blocks
    assert "Fratelli −540,00 €" in blocks and "Pagamento annuale −100,00 €" in blocks
    assert "4.760,00 €" in blocks and "Quota d'iscrizione 300,00 €" in blocks
    assert "controlla prima di comunicarla" not in page


def test_no_discount_is_ever_computed(client, db, family):
    family_id, lead_id = family
    offer_id = new_offer(client, family_id, student_lead_id=lead_id, standard_fee="5.400,00",
                         reduction_reason_1="FRATELLI", reduction_amount_1="540", proposed_fee="")
    row = offer(db, offer_id)
    assert row["proposed_fee_cents"] is None  # 5.400 − 540 non viene calcolato
    page = communicate(client, db, family_id, offer_id)
    assert "manca la quota proposta" in page and offer(db, offer_id)["status"] == "BOZZA"


@pytest.mark.parametrize("fields, message", [
    ({"reduction_reason_1": "INVENTATO", "reduction_amount_1": "10"}, "voce non valida"),
    ({"reduction_reason_1": "FRATELLI", "reduction_amount_1": "0"}, "maggiore di zero"),
    ({"reduction_reason_1": "FRATELLI", "reduction_amount_1": ""}, "maggiore di zero"),
    ({"reduction_reason_1": "", "reduction_amount_1": "100"}, "scegli il motivo"),
    ({"reduction_reason_1": "ALTRO", "reduction_amount_1": "100"}, "descrivi la condizione"),
    ({"reduction_reason_1": "FRATELLI", "reduction_amount_1": "100", "reduction_note_1": "x" * 201}, "nota troppo lunga"),
    ({"standard_fee": "1.000", "reduction_reason_1": "FRATELLI", "reduction_amount_1": "600",
      "reduction_reason_2": "PROMOZIONE", "reduction_amount_2": "500"}, "superano la retta di listino"),
    ({"authorized_by": "x" * 121}, "Autorizzata da"),
    ({"enrollment_fee": "abc"}, "Quota d&#39;iscrizione"),
])
def test_invalid_reserved_conditions_are_rejected(client, db, family, fields, message):
    family_id, _ = family
    data = {"new_id": str(uuid.uuid4()), "proposed_fee": "500", "periodicity": "ANNUALE", **fields}
    response = client.post(f"/famiglie/{family_id}/offerte/nuova", data=data)
    assert response.status_code == 422 and message in response.get_data(as_text=True)
    assert db.scalar("SELECT count(*) FROM Offer") == 0


@pytest.mark.parametrize("reason", ["PROMOZIONE", "ACCORDO_DIREZIONE", "ALTRO"])
def test_discretionary_conditions_need_authorized_by(client, db, family, reason):
    family_id, lead_id = family
    offer_id = new_offer(client, family_id, student_lead_id=lead_id, standard_fee="5.400",
                         reduction_reason_1=reason, reduction_amount_1="540", reduction_note_1="Accordo (esempio)",
                         proposed_fee="4.860")
    page = communicate(client, db, family_id, offer_id, channel="Di persona")
    assert "manca «Autorizzata da»" in page and offer(db, offer_id)["status"] == "BOZZA"
    assert "Manca «Autorizzata da»" in client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert edit(client, db, family_id, offer_id, standard_fee="5.400", reduction_reason_1=reason,
                reduction_amount_1="540", reduction_note_1="Accordo (esempio)", proposed_fee="4.860",
                authorized_by="Direzione").status_code == 302
    page = communicate(client, db, family_id, offer_id, channel="Di persona")
    assert "segnata come comunicata" in page
    assert (offer(db, offer_id)["status"], offer(db, offer_id)["authorized_by"]) == ("COMUNICATA", "Direzione")


def test_standard_kite_reductions_need_no_authorization(client, db, family):
    family_id, lead_id = family
    offer_id = new_offer(client, family_id, student_lead_id=lead_id, standard_fee="5.400",
                         reduction_reason_1="FRATELLI", reduction_amount_1="540",
                         reduction_reason_2="PAGAMENTO_ANNUALE", reduction_amount_2="100", proposed_fee="4.760")
    assert "segnata come comunicata" in communicate(client, db, family_id, offer_id)
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Manca «Autorizzata da»" not in page


def test_communication_freezes_new_fields_and_is_idempotent(client, db, family, clock):
    family_id, lead_id = family
    offer_id = new_offer(client, family_id, student_lead_id=lead_id, standard_fee="5.400",
                         reduction_reason_1="PROMOZIONE", reduction_amount_1="200", authorized_by="Direzione",
                         proposed_fee="5.200", enrollment_fee="300")
    old_revision = revision(db, "Offer", offer_id)
    communicate(client, db, family_id, offer_id)
    stamp = offer(db, offer_id)["communicated_at"]
    clock.advance(minutes=2)
    again = client.post(f"/famiglie/{family_id}/offerte/{offer_id}/comunica", data={"revision": old_revision},
                        follow_redirects=True).get_data(as_text=True)
    assert "già comunicata" in again and offer(db, offer_id)["communicated_at"] == stamp
    response = edit(client, db, family_id, offer_id, reduction_reason_1="FRATELLI", reduction_amount_1="1",
                    enrollment_fee="1", authorized_by="Altro")
    assert response.status_code == 302
    row = offer(db, offer_id)
    assert (json.loads(row["reductions"])[0]["reason"], row["enrollment_fee_cents"], row["authorized_by"]) == (
        "PROMOZIONE", 30000, "Direzione")
    with pytest.raises(ValidationError):
        offer_service.update_draft(db, offer_id, family_id, offer_service.parse_offer(
            {"proposed_fee": "1", "periodicity": "ANNUALE"}), revision=row["revision"], now=clock())
    for assignment in ("reductions = '[]'", "enrollment_fee_cents = NULL", "authorized_by = 'Nessuno'"):
        with pytest.raises(sqlite3.IntegrityError, match="offer_frozen"):
            db.execute(f"UPDATE Offer SET {assignment} WHERE id = ?", (offer_id,))


def test_new_version_copies_conditions_but_asks_the_authorization_again(client, db, family):
    family_id, lead_id = family
    v1 = new_offer(client, family_id, student_lead_id=lead_id, standard_fee="5.400",
                   reduction_reason_1="ACCORDO_DIREZIONE", reduction_amount_1="400", authorized_by="Direzione",
                   proposed_fee="5.000", enrollment_fee="300")
    communicate(client, db, family_id, v1)
    v2 = str(uuid.uuid4())
    response = client.post(f"/famiglie/{family_id}/offerte/{v1}/nuova-versione", data={"new_id": v2})
    assert response.status_code == 302 and f"/offerte/{v2}/modifica" in response.headers["Location"]
    copy = offer(db, v2)
    assert json.loads(copy["reductions"]) == json.loads(offer(db, v1)["reductions"])
    assert copy["enrollment_fee_cents"] == 30000 and copy["authorized_by"] is None
    page = client.get(f"/famiglie/{family_id}/offerte/{v2}/modifica").get_data(as_text=True)
    assert "v1 autorizzata da: <strong>Direzione</strong>" in page and "va indicata di nuovo" in page
    assert re.search(r'id="authorized_by" name="authorized_by" value=""', page)
    assert "manca «Autorizzata da»" in communicate(client, db, family_id, v2)
    assert offer(db, v2)["status"] == "BOZZA"


def test_consistency_warning_is_visible_but_never_blocks(client, db, family):
    family_id, lead_id = family
    offer_id = new_offer(client, family_id, student_lead_id=lead_id, standard_fee="5.400",
                         reduction_reason_1="FRATELLI", reduction_amount_1="540", proposed_fee="4.900")
    warning = "Listino 5.400,00 € meno riduzioni 540,00 € = 4.860,00 €, ma la retta finale indicata è 4.900,00 €"
    assert warning in client.get(f"/famiglie/{family_id}/offerte/{offer_id}/modifica").get_data(as_text=True)
    assert warning in client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "segnata come comunicata" in communicate(client, db, family_id, offer_id)
    assert offer(db, offer_id)["proposed_fee_cents"] == 490000  # l'importo scritto, non quello calcolato


def test_v1_offers_without_reductions_look_as_before(client, db, family):
    family_id, _ = family
    new_offer(client, family_id, standard_fee="5.400,00", proposed_fee="4.900,00")
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Proposta familiare" in page and "Differenza dal listino: sconto 500,00 €" in page
    assert "Manca «Autorizzata da»" not in page


def test_reason_removed_from_the_catalog_is_kept_on_save(client, db, family):
    family_id, _ = family
    offer_id = new_offer(client, family_id, proposed_fee="1.000")
    db.execute("UPDATE Offer SET reductions = ? WHERE id = ?",
               ('[{"reason": "VECCHIO_MOTIVO", "amount_cents": 1000, "note": ""}]', offer_id))
    page = client.get(f"/famiglie/{family_id}/offerte/{offer_id}/modifica").get_data(as_text=True)
    assert "VECCHIO_MOTIVO (voce non più in elenco)" in page
    assert edit(client, db, family_id, offer_id, proposed_fee="1.000", reduction_reason_1="VECCHIO_MOTIVO",
                reduction_amount_1="10").status_code == 302
    assert json.loads(offer(db, offer_id)["reductions"])[0]["reason"] == "VECCHIO_MOTIVO"
    assert offer_service.needs_authorization(offer_service.reductions_of(offer(db, offer_id)))  # prudenza
    new_offer(client, family_id, expect=422, student_lead_id=family[1], reduction_reason_1="VECCHIO_MOTIVO",
              reduction_amount_1="10")


def test_offer_pages_return_to_the_interview(client, db, family):
    family_id, lead_id = family
    page = client.get(f"/famiglie/{family_id}/offerte/nuova?ambito={lead_id}&next={INTERVIEW}").get_data(as_text=True)
    assert f'name="next" value="{INTERVIEW}"' in page and f'href="{INTERVIEW}"' in page
    response = client.post(f"/famiglie/{family_id}/offerte/nuova", data={
        "new_id": str(uuid.uuid4()), "student_lead_id": lead_id, "proposed_fee": "4.000", "periodicity": "ANNUALE",
        "next": INTERVIEW})
    assert response.headers["Location"].endswith(INTERVIEW)
    offer_id = db.scalar("SELECT id FROM Offer")
    response = edit(client, db, family_id, offer_id, next=INTERVIEW)
    assert response.headers["Location"].endswith(INTERVIEW)
    communicate(client, db, family_id, offer_id)
    response = client.post(f"/famiglie/{family_id}/offerte/{offer_id}/nuova-versione",
                           data={"new_id": str(uuid.uuid4()), "next": INTERVIEW})
    assert "/modifica?next=" in response.headers["Location"]
    unsafe = client.get(f"/famiglie/{family_id}/offerte/nuova?next=https://example.org/x").get_data(as_text=True)
    assert 'name="next"' not in unsafe
    response = client.post(f"/famiglie/{family_id}/offerte/nuova", data={
        "new_id": str(uuid.uuid4()), "proposed_fee": "1", "periodicity": "ANNUALE", "next": "//example.org/x"})
    assert "example.org" not in response.headers["Location"]


def test_export_includes_the_enrollment_fee_in_euro(app, client, db, family):
    family_id, lead_id = family
    new_offer(client, family_id, student_lead_id=lead_id, reduction_reason_1="FRATELLI", reduction_amount_1="540",
              standard_fee="5.400", enrollment_fee="300")
    client.post("/dati/export")
    package = next(app.extensions["kite"].paths.exports_dir.glob("*.zip"))
    with zipfile.ZipFile(package) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("Offer.csv").decode("utf-8").lstrip("﻿")),
                                   delimiter=";"))
        document = json.loads(archive.read("kite-admissions.json"))
    assert rows[0]["enrollment_fee_eur"] == "300,00 €" and rows[0]["enrollment_fee_cents"] == "30000"
    assert json.loads(rows[0]["reductions"])[0]["reason"] == "FRATELLI"
    assert document["tables"]["Offer"][0]["enrollment_fee_cents"] == 30000
