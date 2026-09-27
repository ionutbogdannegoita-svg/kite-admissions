"""v1.1 — Slice 2: dati dell'alunno, età e verifica informativa, fonte del contatto (IW10, parte di IW12)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

import pytest

from kite_admissions.services import families as family_service
from kite_admissions.services import leads as lead_service
from kite_admissions.services.common import StaleWriteError

from .helpers import create_family, create_lead, revision

FACTS = {
    "display_name": "Alunno Esempio Fatti", "school_year": "2027/2028", "grade": "Primaria 1ª",
    "birth_date": "2021-03-14", "birth_year": "2019",  # l'anno segue la data completa
    "current_school": "Scuola dell'infanzia Esempio", "current_grade": "Infanzia", "origin": "Latina (esempio)",
    "lang_IT": "MADRELINGUA", "lang_EN": "BASE", "other_lang_1_name": "Lingua Esempio",
    "other_lang_1_level": "AVANZATO", "bilingual_context": "1",
    "profile_note": "Ama disegnare (esempio)", "educational_review": "1",
    "educational_review_note": "Colloquio con il coordinamento didattico",
}


def lead_row(db, lead_id):
    return db.one("SELECT * FROM StudentLead WHERE id = ?", (lead_id,))


def test_lead_form_saves_student_facts(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Fatti")
    lead_id = create_lead(client, family_id, **FACTS)
    row = lead_row(db, lead_id)
    assert (row["birth_date"], row["birth_year"]) == ("2021-03-14", 2021)
    assert (row["current_school"], row["current_grade"], row["origin"]) == (
        "Scuola dell'infanzia Esempio", "Infanzia", "Latina (esempio)")
    assert json.loads(row["languages"]) == [{"code": "IT", "level": "MADRELINGUA"}, {"code": "EN", "level": "BASE"},
                                            {"code": "ALTRA", "name": "Lingua Esempio", "level": "AVANZATO"}]
    assert (row["bilingual_context"], row["profile_note"]) == (1, "Ama disegnare (esempio)")
    assert (row["educational_review"], row["educational_review_note"]) == (1, "Colloquio con il coordinamento didattico")

    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "Nato/a il 14/03/2021" in page and "5 anni (6 al 31/12/2027)" in page
    assert "età regolare per la Primaria 1ª" in page
    assert "Approfondimento" in page and "Colloquio con il coordinamento didattico" in page
    assert "Ora: Scuola dell&#39;infanzia Esempio · Infanzia" in page
    assert "Lingue (dichiarate): IT madrelingua/bilingue · EN base · Lingua Esempio avanzato" in page


def test_removing_the_flag_clears_the_note(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Flag")
    lead_id = create_lead(client, family_id, **FACTS)
    data = {**FACTS, "educational_review": "", "revision": revision(db, "StudentLead", lead_id)}
    assert client.post(f"/famiglie/{family_id}/richieste/{lead_id}/modifica", data=data).status_code == 302
    row = lead_row(db, lead_id)
    assert (row["educational_review"], row["educational_review_note"]) == (0, None)
    assert json.loads(row["languages"])[0] == {"code": "IT", "level": "MADRELINGUA"}  # il resto resta


@pytest.mark.parametrize("change", [
    {"birth_date": "2027-01-01"},  # nel futuro rispetto all'orologio dei test (01/10/2026)
    {"birth_date": "2021-02-30"},
    {"lang_IT": "PERFETTO"},
    {"other_lang_1_name": "Lingua", "other_lang_1_level": ""},
    {"profile_note": "x" * 501},
    {"educational_review_note": "x" * 301},
    {"bilingual_context": "2"},
])
def test_invalid_student_facts_are_rejected(client, db, change):
    family_id = create_family(client, display_name="Famiglia Esempio Errori")
    data = {"new_id": str(uuid.uuid4()), **FACTS, **change}
    response = client.post(f"/famiglie/{family_id}/richieste/nuova", data=data)
    assert response.status_code == 422
    assert db.scalar("SELECT count(*) FROM StudentLead WHERE family_id = ?", (family_id,)) == 0


def test_age_check_is_informative_and_never_blocks(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Età")
    lead_id = create_lead(client, family_id, display_name="Alunno Esempio Piccolo", school_year="2027/2028",
                          grade="Primaria 1ª", birth_date="2022-06-01")
    assert lead_row(db, lead_id)["birth_date"] == "2022-06-01"  # salvato comunque
    page = client.get(f"/famiglie/{family_id}").get_data(as_text=True)
    assert "età da verificare" in page and "Verifica informativa" in page


def _lead(birth_date=None, birth_year=None, school_year="2027/2028", grade="Primaria 1ª"):
    return {"birth_date": birth_date, "birth_year": birth_year, "school_year": school_year, "grade": grade}


TODAY = date(2026, 10, 1)


@pytest.mark.parametrize("grade, born, status", [
    ("Infanzia", "2024-12-31", "REGOLARE"),
    ("Infanzia", "2023-01-10", "REGOLARE"),  # 4 anni al 31/12: sezione successiva, sempre Infanzia
    ("Infanzia", "2025-01-01", "ANTICIPO"),
    ("Infanzia", "2025-04-30", "ANTICIPO"),
    ("Infanzia", "2025-05-01", "DA_VERIFICARE"),
    ("Infanzia", "2021-06-01", "DA_VERIFICARE"),  # 6 anni al 31/12: età da Primaria
    ("Primaria 1ª", "2021-12-31", "REGOLARE"),
    ("Primaria 1ª", "2022-01-01", "ANTICIPO"),
    ("Primaria 1ª", "2022-04-30", "ANTICIPO"),
    ("Primaria 1ª", "2022-05-01", "DA_VERIFICARE"),
    ("Primaria 1ª", "2020-06-01", "DA_VERIFICARE"),  # già 7 anni al 31/12
])
def test_age_rules_at_the_boundaries(grade, born, status):
    info = lead_service.age_info(_lead(born, int(born[:4]), grade=grade), TODAY)
    assert info.status == status, info.message
    assert info.deadline == date(2027, 12, 31)


def test_age_calculation_details():
    leap = date(2020, 2, 29)
    assert lead_service.age_on(leap, date(2021, 2, 28)) == 0
    assert lead_service.age_on(leap, date(2021, 3, 1)) == 1
    assert lead_service.age_on(leap, date(2024, 2, 29)) == 4
    info = lead_service.age_info(_lead("2020-02-29", 2020, grade="Primaria 1ª", school_year="2026/2027"), TODAY)
    assert info.status == "REGOLARE" and info.age_today == 6
    other = lead_service.age_info(_lead("2019-05-10", 2019, grade="Primaria 2ª"), TODAY)
    assert other.status is None and other.age_at_deadline == 8
    assert lead_service.age_info(_lead(None, 2021), TODAY).approximate
    assert lead_service.age_info(_lead(None, None), TODAY) is None
    no_year = lead_service.age_info(_lead("2021-03-14", 2021, school_year=None), TODAY)
    assert no_year.deadline is None and no_year.status is None and no_year.age_today == 5


def test_new_year_copy_keeps_stable_facts_only(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Anno")
    lead_id = create_lead(client, family_id, **FACTS)
    form = lead_service.copy_for_new_year(lead_row(db, lead_id))
    assert form["school_year"] == "2028/2029" and form["birth_date"] == "2021-03-14"
    assert form["lang_IT"] == "MADRELINGUA" and form["other_lang_1_name"] == "Lingua Esempio"
    assert form["bilingual_context"] == "1"
    assert not any(form[key] for key in ("grade", "current_school", "current_grade", "profile_note",
                                         "educational_review", "educational_review_note", "notes"))
    page = client.get(f"/famiglie/{family_id}/richieste/nuova?da={lead_id}").get_data(as_text=True)
    assert 'value="2021-03-14"' in page and "Ama disegnare" not in page


def test_contact_source_uses_the_closed_list_and_keeps_v1_values(client, db):
    rejected = client.post("/famiglie/nuova", data={"new_id": str(uuid.uuid4()), "display_name": "Famiglia Esempio X",
                                                    "contact_source": "Open day"})
    assert rejected.status_code == 422 and db.scalar("SELECT count(*) FROM Family") == 0
    family_id = create_family(client, display_name="Famiglia Esempio Fonte", contact_source="Altra famiglia KITE",
                              contact_source_detail="Segnalati da una famiglia (esempio)")
    row = db.one("SELECT contact_source, contact_source_detail FROM Family WHERE id = ?", (family_id,))
    assert tuple(row) == ("Altra famiglia KITE", "Segnalati da una famiglia (esempio)")
    assert "fonte: Altra famiglia KITE (Segnalati da una famiglia (esempio))" in client.get(
        f"/famiglie/{family_id}").get_data(as_text=True)

    # Un valore della V1 fuori lista resta selezionato e si salva invariato; un altro valore fuori lista no.
    db.execute("UPDATE Family SET contact_source = 'Open day' WHERE id = ?", (family_id,))
    page = client.get(f"/famiglie/{family_id}/modifica").get_data(as_text=True)
    assert "Open day (valore precedente)" in page
    base = {"display_name": "Famiglia Esempio Fonte", "contact_source_detail": ""}
    kept = client.post(f"/famiglie/{family_id}/modifica", data={**base, "contact_source": "Open day",
                                                               "revision": revision(db, "Family", family_id)})
    assert kept.status_code == 302
    assert db.scalar("SELECT contact_source FROM Family WHERE id = ?", (family_id,)) == "Open day"
    other = client.post(f"/famiglie/{family_id}/modifica", data={**base, "contact_source": "Sito web",
                                                                "revision": revision(db, "Family", family_id)})
    assert other.status_code == 422


def test_forms_return_to_a_safe_next_page(client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Ritorno")
    lead_id = create_lead(client, family_id)
    target = "/appuntamenti/00000000-0000-4000-8000-000000000000/colloquio?sezione=a"
    page = client.get(f"/famiglie/{family_id}/richieste/{lead_id}/modifica?next={target}").get_data(as_text=True)
    assert f'name="next" value="{target.replace("&", "&amp;")}"' in page
    data = {"display_name": "Alunno Esempio Uno", "revision": revision(db, "StudentLead", lead_id), "next": target}
    response = client.post(f"/famiglie/{family_id}/richieste/{lead_id}/modifica", data=data)
    assert response.headers["Location"].endswith(target)
    unsafe = client.get(f"/famiglie/{family_id}/richieste/{lead_id}/modifica?next=https://example.org/x")
    assert 'name="next"' not in unsafe.get_data(as_text=True)
    data = {"display_name": "Alunno Esempio Uno", "revision": revision(db, "StudentLead", lead_id),
            "next": "//example.org/x"}
    response = client.post(f"/famiglie/{family_id}/richieste/{lead_id}/modifica", data=data)
    assert response.headers["Location"].endswith(f"/famiglie/{family_id}#richieste")
    family = client.post(f"/famiglie/{family_id}/modifica", data={
        "display_name": "Famiglia Esempio Ritorno", "revision": revision(db, "Family", family_id), "next": target})
    assert family.headers["Location"].endswith(target)


def test_dedicated_updates_touch_only_their_fields(app, client, db):
    family_id = create_family(client, display_name="Famiglia Esempio Servizi", preliminary_notes="Nota (esempio)")
    lead_id = create_lead(client, family_id, notes="Nota della richiesta (esempio)")
    now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    current = revision(db, "StudentLead", lead_id)
    new = lead_service.update_facts(db, lead_id, family_id, {"current_school": "Scuola Esempio", "birth_date": None},
                                    revision=current, now=now)
    assert new == current + 1 == revision(db, "StudentLead", lead_id)
    assert lead_row(db, lead_id)["notes"] == "Nota della richiesta (esempio)"
    with pytest.raises(StaleWriteError):
        lead_service.update_facts(db, lead_id, family_id, {"grade": "Infanzia"}, revision=current, now=now)
    with pytest.raises(ValueError):
        lead_service.update_facts(db, lead_id, family_id, {"notes": "x"}, revision=new, now=now)
    family_rev = revision(db, "Family", family_id)
    assert family_service.update_contact_source(db, family_id, "Passaparola", "Amici (esempio)",
                                                revision=family_rev, now=now) == family_rev + 1
    row = db.one("SELECT * FROM Family WHERE id = ?", (family_id,))
    assert (row["contact_source"], row["contact_source_detail"], row["preliminary_notes"]) == (
        "Passaparola", "Amici (esempio)", "Nota (esempio)")
