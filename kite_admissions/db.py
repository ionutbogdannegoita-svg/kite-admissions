"""Accesso SQLite: connessioni, transazioni, ispezione e migrazioni dello schema.

Solo query predefinite con parametri. Foreign key attive su ogni connessione.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .schema import APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION, TABLE_ORDER, TABLES

SQLITE_HEADER = b"SQLite format 3\x00"


class DatabaseError(Exception):
    """Errore del database mostrabile all'utente."""


def fold(value: Any) -> str | None:
    """Testo in minuscolo senza accenti, per ricerche tolleranti."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKD", str(value))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def _uri(path: Path, mode: str) -> str:
    return f"{path.resolve().as_uri()}?mode={mode}"


def open_connection(path: Path, *, create: bool = False) -> sqlite3.Connection:
    """Connessione in autocommit: le transazioni sono esplicite (BEGIN IMMEDIATE)."""
    mode = "rwc" if create else "rw"
    conn = sqlite3.connect(_uri(Path(path), mode), uri=True, isolation_level=None, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise DatabaseError("Impossibile attivare le foreign key di SQLite.")
    conn.create_function("fold", 1, fold, deterministic=True)
    return conn


class Database:
    """Una connessione con transazioni annidabili (le interne si uniscono all'esterna)."""

    def __init__(self, path: Path, *, create: bool = False):
        self.path = Path(path)
        self.conn = open_connection(self.path, create=create)
        self._depth = 0
        self.committed_writes = 0

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self._depth:
            self._depth += 1
            try:
                yield self.conn
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield self.conn
        except BaseException:
            self._depth = 0
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise
        self._depth = 0
        self.conn.execute("COMMIT")
        self.committed_writes += 1

    def execute(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def one(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def scalar(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def close(self) -> None:
        self.conn.close()


@dataclass
class Inspection:
    """Esito del controllo di un file database."""

    status: str  # ok | missing | empty | needs_migration | too_new | foreign | corrupt | unreadable
    detail: str = ""
    version: int | None = None
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.status in ("ok", "needs_migration")


def check_connection(conn: sqlite3.Connection) -> list[str]:
    """Problemi di integrità e foreign key; lista vuota se il database è coerente."""
    problems: list[str] = []
    integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        problems.append("integrity_check: " + "; ".join(integrity[:3]))
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        problems.append(f"{len(violations)} violazioni di foreign key")
    return problems


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES}


def inspect(path: Path) -> Inspection:
    """Controlla il file senza crearlo: presenza, intestazione, integrità, foreign key e versione."""
    path = Path(path)
    try:
        if not path.exists() or path.stat().st_size == 0:
            return Inspection("missing")
        with open(path, "rb") as handle:
            header = handle.read(100)
    except OSError as exc:
        return Inspection("unreadable", f"file non leggibile ({exc.__class__.__name__})")
    if len(header) < 100 or not header.startswith(SQLITE_HEADER):
        return Inspection("corrupt", "il file non ha un'intestazione SQLite valida")
    try:
        conn = open_connection(path)
    except (sqlite3.Error, DatabaseError) as exc:
        return Inspection("corrupt", f"apertura non riuscita: {exc}")
    try:
        app_id = conn.execute("PRAGMA application_id").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        problems = check_connection(conn)
        if problems:
            return Inspection("corrupt", "; ".join(problems), version)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
        if version == 0 and not tables:
            return Inspection("empty", version=0)
        if app_id != APPLICATION_ID:
            return Inspection("foreign", "il file non è un database KITE Admissions", version)
        if version > SCHEMA_VERSION:
            return Inspection("too_new", f"schema {version} più recente di quello supportato ({SCHEMA_VERSION})", version)
        missing = [table for table in TABLES if table not in tables]
        if missing or version < 1:
            return Inspection("corrupt", "tabelle mancanti: " + ", ".join(missing), version)
        counts = table_counts(conn)
        status = "needs_migration" if version < SCHEMA_VERSION else "ok"
        return Inspection(status, version=version, counts=counts)
    except sqlite3.Error as exc:
        return Inspection("corrupt", f"lettura non riuscita: {exc}")
    finally:
        conn.close()


def _run_script(conn: sqlite3.Connection, script: str, version: int) -> None:
    try:
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + script
            + f"\nPRAGMA application_id = {APPLICATION_ID};\nPRAGMA user_version = {version};\nCOMMIT;"
        )
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def initialize(path: Path) -> None:
    """Crea lo schema corrente in un database nuovo o vuoto."""
    conn = open_connection(path, create=True)
    try:
        if conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0]:
            raise DatabaseError("Il database non è vuoto: inizializzazione rifiutata.")
        for version in range(1, SCHEMA_VERSION + 1):
            _run_script(conn, MIGRATIONS[version], version)
    finally:
        conn.close()


def migrate(path: Path, backup_before: Callable[[], Any]) -> int:
    """Aggiorna lo schema; la copia preventiva deve riuscire prima di toccare i dati (SPEC §9.2).

    Restituisce il numero di migrazioni applicate.
    """
    conn = open_connection(path)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    if current == SCHEMA_VERSION:
        return 0
    if current > SCHEMA_VERSION or current < 1:
        raise DatabaseError(f"Versione schema {current} non migrabile.")
    backup_before()  # un'eccezione qui interrompe l'aggiornamento
    conn = open_connection(path)
    try:
        for version in range(current + 1, SCHEMA_VERSION + 1):
            _run_script(conn, MIGRATIONS[version], version)
        problems = check_connection(conn)
        if problems:
            raise DatabaseError("Verifica dopo la migrazione fallita: " + "; ".join(problems))
    finally:
        conn.close()
    return SCHEMA_VERSION - current


def ordered_rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(f"SELECT * FROM {table} ORDER BY {TABLE_ORDER[table]}").fetchall()
