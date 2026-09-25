"""Esporta dati: CSV leggibili delle tabelle operative e JSON completo (SPEC §9.3).

Nessun invio: il pacchetto resta in locale. I CSV usano «;» e UTF-8 con BOM (Excel in
italiano) e neutralizzano le formule. Le credenziali Google non vengono mai lette.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..db import Database, ordered_rows
from ..labels import euro
from ..paths import Paths
from ..schema import SCHEMA_VERSION, TABLES
from ..timeutil import ROME, to_iso

OPERATIONAL_TABLES = TABLES[:6]
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NAME_RE = re.compile(r"^kite-admissions-export-[0-9]{8}-[0-9]{6}(-[0-9]+)?\.zip$")
_MONEY_COLUMNS = ("standard_fee_cents", "proposed_fee_cents")


def safe_cell(value: Any) -> str:
    """Testo per CSV che un foglio di calcolo non interpreta come formula."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_START):
        return "'" + text
    return text


def _csv_bytes(columns: list[str], rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([safe_cell(value) for value in row])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _table_csv(db: Database, table: str) -> bytes:
    rows = ordered_rows(db.conn, table)
    columns = [description[0] for description in db.conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
    values = [list(row) for row in rows]
    if table == "Offer":
        extra = [name.replace("_cents", "_eur") for name in _MONEY_COLUMNS]
        indexes = [columns.index(name) for name in _MONEY_COLUMNS]
        columns = columns + extra
        values = [row + [euro(row[index]) for index in indexes] for row in values]
    return _csv_bytes(columns, values)


def export_package(db: Database, paths: Paths, settings_data: dict[str, Any], now: datetime) -> Path:
    stamp = now.astimezone(ROME).strftime("%Y%m%d-%H%M%S")
    paths.exports_dir.mkdir(parents=True, exist_ok=True)
    target = paths.exports_dir / f"kite-admissions-export-{stamp}.zip"
    index = 2
    while target.exists():
        target = paths.exports_dir / f"kite-admissions-export-{stamp}-{index}.zip"
        index += 1
    document = {
        "format": "kite-admissions-export",
        "format_version": 1,
        "exported_at": to_iso(now),
        "app_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "settings": settings_data,
        "tables": {table: [dict(row) for row in ordered_rows(db.conn, table)] for table in TABLES},
    }
    fd, temp_name = tempfile.mkstemp(prefix=".export-", suffix=".tmp", dir=paths.exports_dir)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for table in OPERATIONAL_TABLES:
                archive.writestr(f"{table}.csv", _table_csv(db, table))
            archive.writestr("kite-admissions.json", json.dumps(document, ensure_ascii=False, indent=2))
            archive.writestr("LEGGIMI.txt", (
                "Export di KITE Admissions.\r\n"
                "- CSV (separatore ';', UTF-8): una tabella operativa per file, per consultazione.\r\n"
                "- kite-admissions.json: tutte le tabelle con ID, relazioni, versioni delle offerte, esclusioni\r\n"
                "  Calendar e configurazione non segreta.\r\n"
                "Orari in UTC (formato ISO). Importi in centesimi di euro; per le offerte anche in euro.\r\n"
                "Contiene dati personali: conservalo con la stessa cautela del database.\r\n"
                "Per ripristinare l'applicazione usa un backup, non questo export.\r\n").encode("utf-8"))
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return target


def list_exports(paths: Paths) -> list[Path]:
    if not paths.exports_dir.is_dir():
        return []
    return sorted((path for path in paths.exports_dir.glob("kite-admissions-export-*.zip")), reverse=True)


def find_export(paths: Paths, name: str) -> Path | None:
    if not _NAME_RE.match(name):
        return None
    folder = paths.exports_dir.resolve()
    path = (folder / name).resolve()
    return path if path.parent == folder and path.is_file() else None
