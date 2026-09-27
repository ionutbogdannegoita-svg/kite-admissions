"""Slice 1: Family + StudentLead (AC04, AC05, AC10 parti pertinenti)."""

from __future__ import annotations

import uuid

import pytest

from kite_admissions.services import leads as lead_service

from .helpers import create_family, create_lead, interactions, location_id, revision


def test_family_with_missing_contacts_is_allowed(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Senza Recapiti")
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Famiglia Esempio Senza Recapiti" in page
    assert "Nessun recapito registrato" in page
    row = db.one("SELECT * FROM Family WHERE id = ?", (family_id,))
    assert row["primary_phone"] is None and row["primary_email"] is None
    assert row["created_by"] == "Ionut" and row["revision"] == 1


def test_family_and_first_child_created_together(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Beta", primary_adult_name="Adulto Esempio Beta",
                              primary_phone="+39 0773 000 002", primary_email="beta@example.org",
                              lead_display_name="Bambina Esempio Beta", lead_school_year="2027/2028",
                              lead_grade="Primaria 1ª")
    lead = db.one("SELECT * FROM StudentLead WHERE family_id = ?", (family_id,))
    assert lead["display_name"] == "Bambina Esempio Beta" and lead["status"] == "IN_CORSO"
    family = db.one("SELECT * FROM Family WHERE id = ?", (family_id,))
    assert family["primary_phone_norm"] == "+390773000002"
    assert family["primary_email_norm"] == "beta@example.org"


def test_invalid_data_saves_nothing(client, db):
    response = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "display_name": "  "})
    assert response.status_code == 422
    response = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "display_name": "Famiglia X",
                                                    "primary_phone": "12"})
    assert response.status_code == 422
    assert "telefono" in response.get_data(as_text=True)
    # Figlio non valido: nemmeno la famiglia viene salvata (nessuna creazione a metà).
    response = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "display_name": "Famiglia Y",
                                                    "lead_display_name": "Bimbo", "lead_school_year": "2026/2030"})
    assert response.status_code == 422
    assert db.scalar("SELECT count(*) FROM Family") == 0
    assert db.scalar("SELECT count(*) FROM StudentLead") == 0


def test_duplicate_phone_is_signalled_and_can_be_confirmed(client, db):
    create_family(client, display_name="Famiglia Esempio Gamma", primary_phone="+39 0773 000 003")
    new_id = str(uuid.uuid4())
    data = {"new_id": new_id, "display_name": "Nucleo Esempio Delta", "primary_phone": "0773 000 003"}
    response = client.post("/famiglie/nuova", data=data)
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Possibili doppioni" in page and "stesso telefono" in page and "Famiglia Esempio Gamma" in page
    assert db.scalar("SELECT count(*) FROM Family") == 1
    response = client.post("/famiglie/nuova", data={**data, "confirm_distinct": "1"})
    assert location_id(response, "/famiglie") == new_id
    assert db.scalar("SELECT count(*) FROM Family WHERE primary_phone_norm = '+390773000003'") == 2


def test_duplicate_email_and_similar_label_are_signalled(client, db):
    create_family(client, display_name="Famiglia Esempio Épsilon", primary_email="Epsilon@Example.org")
    response = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "display_name": "Altra",
                                                    "primary_email": "epsilon@example.org"})
    assert "stessa email" in response.get_data(as_text=True)
    response = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()),
                                                    "display_name": "esempio epsilon"})
    assert "etichetta simile" in response.get_data(as_text=True)


def test_duplicate_child_name_is_signalled(client):
    family_id = create_family(client, display_name="Famiglia Esempio Zeta")
    create_lead(client, family_id, display_name="Bambino Esempio Zeta")
    response = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "display_name": "Nucleo diverso",
                                                    "lead_display_name": "bambino esempio zeta"})
    assert "stesso nome del bambino" in response.get_data(as_text=True)


def test_double_submit_does_not_duplicate_family(client, db):
    data = {"new_id": str(uuid.uuid4()), "display_name": "Famiglia Esempio Eta"}
    first = client.post("/famiglie/nuova", data=data)
    second = client.post("/famiglie/nuova", data=data)
    assert location_id(first, "/famiglie") == location_id(second, "/famiglie") == data["new_id"]
    assert db.scalar("SELECT count(*) FROM Family") == 1


def test_stale_edit_from_second_tab_is_rejected(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Theta")
    old_revision = revision(db, "Family", family_id)
    ok = client.post(f"/famiglie/{family_id}/modifica", data={"display_name": "Famiglia Esempio Theta",
                                                              "primary_phone": "+39 0773 000 009",
                                                              "revision": old_revision})
    assert ok.status_code == 302
    stale = client.post(f"/famiglie/{family_id}/modifica", data={"display_name": "Famiglia Theta sovrascritta",
                                                                 "revision": old_revision})
    assert stale.status_code == 409
    assert "altra scheda" in stale.get_data(as_text=True)
    row = db.one("SELECT * FROM Family WHERE id = ?", (family_id,))
    assert row["display_name"] == "Famiglia Esempio Theta" and row["primary_phone"] == "+39 0773 000 009"


def test_search_by_label_child_phone_email_and_filters(client):
    alfa = create_family(client, display_name="Famiglia Esempio Nicolò", primary_phone="+39 0773 222 334",
                         primary_email="nicolo.test@example.org")
    beta = create_family(client, display_name="Famiglia Esempio Beta")
    create_lead(client, alfa, display_name="Chiara Esempio", school_year="2027/2028")
    create_lead(client, beta, display_name="Marco Esempio", school_year="2028/2029")

    def found(**params):
        page = client.get("/famiglie/", query_string=params).get_data(as_text=True)
        return {"alfa": "Famiglia Esempio Nicolò" in page, "beta": "Famiglia Esempio Beta" in page}

    assert found(q="nicolo") == {"alfa": True, "beta": False}  # senza accento
    assert found(q="chiara") == {"alfa": True, "beta": False}  # nome bambino
    assert found(q="222 334") == {"alfa": True, "beta": False}  # telefono parziale
    assert found(q="NICOLO.TEST@example.org") == {"alfa": True, "beta": False}  # email
    assert found(anno="2028/2029") == {"alfa": False, "beta": True}
    assert found(stato="IN_CORSO") == {"alfa": True, "beta": True}
    assert found(q="100%_") == {"alfa": False, "beta": False}  # caratteri jolly trattati come testo


def test_siblings_have_distinct_requests_and_outcomes(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Fratelli")
    first = create_lead(client, family_id, display_name="Primo Esempio", school_year="2027/2028")
    second = create_lead(client, family_id, display_name="Seconda Esempio", school_year="2027/2028")
    response = client.post(f"/famiglie/{family_id}/richieste/{first}/stato", data={
        "new_status": "ISCRITTO", "closed_on": "2026-10-01", "enrollment_ref": "DOMANDA-TEST-001",
        "revision": revision(db, "StudentLead", first)})
    assert response.status_code == 302
    response = client.post(f"/famiglie/{family_id}/richieste/{second}/stato", data={
        "new_status": "NON_PROSEGUE", "closed_on": "2026-10-01", "closure_reason": "Trasferimento (fittizio)",
        "revision": revision(db, "StudentLead", second)})
    assert response.status_code == 302
    statuses = {row["id"]: row["status"] for row in db.all("SELECT id, status FROM StudentLead")}
    assert statuses == {first: "ISCRITTO", second: "NON_PROSEGUE"}
    enrolled = interactions(db, student_lead_id=first)
    assert [(r["type"], r["previous_state"], r["next_state"], r["origin"]) for r in enrolled] == [
        ("LEAD_ENROLLED", "IN_CORSO", "ISCRITTO", "Ionut")]
    assert enrolled[0]["family_id"] == family_id and enrolled[0]["occurred_at"].endswith("Z")
    assert "DOMANDA-TEST-001" in enrolled[0]["text"]
    stopped = interactions(db, student_lead_id=second)
    assert [(r["type"], r["previous_state"], r["next_state"]) for r in stopped] == [
        ("LEAD_NOT_CONTINUING", "IN_CORSO", "NON_PROSEGUE")]
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Iscritto" in page and "Non prosegue" in page


def test_reopening_requires_note_and_records_one_transition(client, db):
    family_id = create_family(client)
    lead_id = create_lead(client, family_id)
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "NON_PROSEGUE", "closed_on": "2026-10-01", "revision": revision(db, "StudentLead", lead_id)})
    missing_note = client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "IN_CORSO", "revision": revision(db, "StudentLead", lead_id)})
    assert missing_note.status_code == 422
    reopened = client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "IN_CORSO", "note": "La famiglia ha richiamato (esempio)",
        "revision": revision(db, "StudentLead", lead_id)})
    assert reopened.status_code == 302
    rows = interactions(db, student_lead_id=lead_id)
    assert [r["type"] for r in rows] == ["LEAD_NOT_CONTINUING", "LEAD_REOPENED"]
    assert (rows[1]["previous_state"], rows[1]["next_state"]) == ("NON_PROSEGUE", "IN_CORSO")
    lead = db.one("SELECT * FROM StudentLead WHERE id = ?", (lead_id,))
    assert lead["status"] == "IN_CORSO" and lead["closed_on"] is None


def test_same_status_and_ordinary_edits_add_no_interaction(client, db):
    family_id = create_family(client)
    lead_id = create_lead(client, family_id)
    response = client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "IN_CORSO", "revision": revision(db, "StudentLead", lead_id)})
    assert response.status_code == 302
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/modifica", data={
        "display_name": "Alunno Esempio Rinominato", "school_year": "2027/2028", "grade": "Infanzia",
        "revision": revision(db, "StudentLead", lead_id)})
    client.get(f"/famiglie/{family_id}")
    client.get("/famiglie/")
    assert interactions(db) == []


def test_pause_requires_future_review_date(client, db, clock):
    family_id = create_family(client)
    lead_id = create_lead(client, family_id)
    url = f"/famiglie/{family_id}/richieste/{lead_id}/stato"
    assert client.post(url, data={"new_status": "IN_PAUSA", "revision": 1}).status_code == 422
    assert client.post(url, data={"new_status": "IN_PAUSA", "review_on": "2026-09-01", "revision": 1}).status_code == 422
    assert client.post(url, data={"new_status": "IN_PAUSA", "review_on": "2027-01-15", "revision": 1}).status_code == 302
    rows = interactions(db, student_lead_id=lead_id)
    assert [(r["type"], r["previous_state"], r["next_state"]) for r in rows] == [
        ("LEAD_STATUS_CHANGED", "IN_CORSO", "IN_PAUSA")]
    # Spostare la data di riesame è una modifica ordinaria: nessuna nuova Interaction.
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/modifica", data={
        "display_name": "Alunno Esempio Uno", "review_on": "2027-02-01", "revision": revision(db, "StudentLead", lead_id)})
    assert db.scalar("SELECT review_on FROM StudentLead WHERE id = ?", (lead_id,)) == "2027-02-01"
    assert len(interactions(db, student_lead_id=lead_id)) == 1


def test_enrollment_requires_reference(client, db):
    family_id = create_family(client)
    lead_id = create_lead(client, family_id)
    response = client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "ISCRITTO", "closed_on": "2026-10-01", "revision": 1})
    assert response.status_code == 422
    assert db.scalar("SELECT status FROM StudentLead WHERE id = ?", (lead_id,)) == "IN_CORSO"


def test_stale_status_change_is_rejected_without_interaction(client, db):
    family_id = create_family(client)
    lead_id = create_lead(client, family_id)
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/modifica", data={
        "display_name": "Nome aggiornato", "revision": 1})
    response = client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "NON_PROSEGUE", "closed_on": "2026-10-01", "revision": 1})
    assert response.status_code == 409
    assert interactions(db) == []


def test_transition_and_state_change_are_atomic(client, db, monkeypatch):
    family_id = create_family(client)
    lead_id = create_lead(client, family_id)
    monkeypatch.setattr(lead_service, "transition_type", lambda *_: "TIPO_NON_VALIDO")
    with pytest.raises(Exception):
        client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
            "new_status": "NON_PROSEGUE", "closed_on": "2026-10-01", "revision": 1})
    assert db.scalar("SELECT status FROM StudentLead WHERE id = ?", (lead_id,)) == "IN_CORSO"
    assert interactions(db) == []


def test_new_request_for_another_year_keeps_the_closed_one(client, db):
    family_id = create_family(client)
    lead_id = create_lead(client, family_id, display_name="Alunno Esempio Anni", school_year="2026/2027",
                          origin="Scuola Esempio")
    client.post(f"/famiglie/{family_id}/richieste/{lead_id}/stato", data={
        "new_status": "NON_PROSEGUE", "closed_on": "2026-10-01", "revision": 1})
    page = client.get(f"/famiglie/{family_id}/richieste/nuova?da={lead_id}").get_data(as_text=True)
    assert "2027/2028" in page and "Scuola Esempio" in page
    create_lead(client, family_id, display_name="Alunno Esempio Anni", school_year="2027/2028")
    rows = db.all("SELECT school_year, status FROM StudentLead WHERE family_id = ? ORDER BY school_year", (family_id,))
    assert [(r["school_year"], r["status"]) for r in rows] == [("2026/2027", "NON_PROSEGUE"), ("2027/2028", "IN_CORSO")]


def test_similar_request_same_year_is_signalled(client, db):
    family_id = create_family(client)
    create_lead(client, family_id, display_name="Luca Esempio", school_year="2027/2028")
    data = {"new_id": str(uuid.uuid4()), "display_name": "luca esempio", "school_year": "2027/2028"}
    response = client.post(f"/famiglie/{family_id}/richieste/nuova", data=data)
    assert response.status_code == 200 and "Richiesta simile" in response.get_data(as_text=True)
    assert db.scalar("SELECT count(*) FROM StudentLead") == 1
    client.post(f"/famiglie/{family_id}/richieste/nuova", data={**data, "confirm_similar": "1"})
    assert db.scalar("SELECT count(*) FROM StudentLead") == 2


def test_request_of_another_family_is_not_reachable(client):
    first = create_family(client, display_name="Famiglia Esempio Uno")
    second = create_family(client, display_name="Famiglia Esempio Due")
    lead_id = create_lead(client, first)
    assert client.get(f"/famiglie/{second}/richieste/{lead_id}/modifica").status_code == 404
    response = client.post(f"/famiglie/{second}/richieste/{lead_id}/stato", data={
        "new_status": "NON_PROSEGUE", "closed_on": "2026-10-01", "revision": 1})
    assert response.status_code == 404


def test_archive_is_reversible_and_searchable(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Archivio")
    assert client.post(f"/famiglie/{family_id}/archivia").status_code == 302
    assert "Famiglia Esempio Archivio" not in client.get("/famiglie/").get_data(as_text=True)
    assert "Famiglia Esempio Archivio" in client.get("/famiglie/?vista=archiviate").get_data(as_text=True)
    assert db.scalar("SELECT archived_at FROM Family WHERE id = ?", (family_id,)) is not None
    client.post(f"/famiglie/{family_id}/riattiva")
    assert db.scalar("SELECT archived_at FROM Family WHERE id = ?", (family_id,)) is None
    assert "Famiglia Esempio Archivio" in client.get("/famiglie/").get_data(as_text=True)


def test_unknown_family_returns_404(client):
    assert client.get(f"/famiglie/{uuid.uuid4()}").status_code == 404
    assert client.get("/famiglie/non-un-id").status_code == 404
