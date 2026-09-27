"""Aiuti per i test: creazione di dati sintetici attraverso l'interfaccia HTTP."""

from __future__ import annotations

import re
import uuid

from kite_admissions.db import Database


def location_id(response, prefix: str) -> str:
    assert response.status_code == 302, response.get_data(as_text=True)[:500]
    match = re.search(prefix + r"/([0-9a-f-]{36})", response.headers["Location"])
    assert match, response.headers["Location"]
    return match.group(1)


def create_family(client, **fields) -> str:
    data = {"new_id": str(uuid.uuid4()), "display_name": "Famiglia Esempio Alfa"}
    data.update(fields)
    response = client.post("/famiglie/nuova", data=data)
    return location_id(response, "/famiglie")


def create_lead(client, family_id: str, **fields) -> str:
    data = {"new_id": str(uuid.uuid4()), "display_name": "Alunno Esempio Uno", "school_year": "2027/2028"}
    data.update(fields)
    response = client.post(f"/famiglie/{family_id}/richieste/nuova", data=data)
    assert response.status_code == 302, response.get_data(as_text=True)[:800]
    return data["new_id"]


def revision(db: Database, table: str, row_id: str) -> int:
    return db.scalar(f"SELECT revision FROM {table} WHERE id = ?", (row_id,))


def interactions(db: Database, **where):
    sql = "SELECT * FROM Interaction"
    if where:
        sql += " WHERE " + " AND ".join(f"{column} = ?" for column in where)
    return db.all(sql + " ORDER BY occurred_at, rowid", list(where.values()))
