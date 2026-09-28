"""Slice 0: database, schema, vincoli, transazioni, migrazioni, avvio."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

from kite_admissions import db as dbmod
from kite_admissions.app import create_app
from kite_admissions.paths import Paths
from kite_admissions.schema import APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION, TABLES
from kite_admissions.services import backups

from .conftest import PORT

TS = "2026-10-01T08:00:00.000000Z"
F1 = "11111111-1111-4111-8111-111111111111"
F2 = "22222222-2222-4222-8222-222222222222"
L1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
L2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
A1 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
O1 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
O2 = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
I1 = "ffffffff-ffff-4fff-8fff-ffffffffffff"

AUDIT = {"created_at": TS, "updated_at": TS, "created_by": "Ionut", "updated_by": "Ionut"}


def insert(conn, table, **values):
    values = {**AUDIT, **values}
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(values.values()))


def family(conn, fid=F1, name="Famiglia Esempio Alfa"):
    insert(conn, "Family", id=fid, display_name=name)


def lead(conn, lid=L1, fid=F1, name="Alunno Esempio Uno"):
    insert(conn, "StudentLead", id=lid, family_id=fid, display_name=name, status="IN_CORSO")


def appointment(conn, aid=A1, **extra):
    values = dict(id=aid, calendar_id="cal-test@example.org", google_event_id="evt-001",
                  src_title="Visita Esempio", src_start_at=TS, src_end_at=TS, src_status="CONFIRMED",
                  verification_state="VERIFIED", last_synced_at=TS, last_checked_at=TS,
                  created_by="Calendar import", updated_by="Calendar import")
    values.update(extra)
    insert(conn, "Appointment", **values)


@pytest.fixture
def conn(app):
    connection = dbmod.open_connection(app.extensions["kite"].paths.db_path)
    yield connection
    connection.close()


def test_fresh_start_creates_exactly_seven_tables(app, conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    assert tables == set(TABLES)
    assert len(TABLES) == 7
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
    assert conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert app.extensions["kite"].recovery is None


def test_database_location_is_identifiable_and_outside_code(app):
    paths = app.extensions["kite"].paths
    assert paths.db_path.name == "admissions.sqlite3"
    assert paths.db_path.parent == paths.home / "data"
    assert paths.db_path.is_file()
    code_dir = Path(__file__).resolve().parents[1]
    assert code_dir not in paths.db_path.parents


def test_default_home_is_local_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("KITE_ADMISSIONS_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    paths = Paths.resolve()
    assert paths.home == (tmp_path / "KITEAdmissions").resolve()
    assert paths.db_path == paths.home / "data" / "admissions.sqlite3"
    assert paths.settings_path == paths.home / "settings.json"
    assert paths.backups_dir == paths.home / "backups"


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        lead(conn, fid=F2)


def test_lead_of_another_family_is_rejected_by_database(conn):
    family(conn, F1)
    family(conn, F2, "Famiglia Esempio Beta")
    lead(conn, L2, F2)
    with pytest.raises(sqlite3.IntegrityError):
        appointment(conn, family_id=F1, student_lead_id=L2)
    with pytest.raises(sqlite3.IntegrityError):
        appointment(conn, family_id=None, student_lead_id=L2)
    appointment(conn, family_id=F2, student_lead_id=L2)


def test_check_constraints(conn):
    family(conn)
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "StudentLead", id=L1, family_id=F1, display_name="X", status="SCONOSCIUTO")
    with pytest.raises(sqlite3.IntegrityError):  # anno scolastico non consecutivo
        insert(conn, "StudentLead", id=L1, family_id=F1, display_name="X", status="IN_CORSO",
               school_year="2026/2028")
    with pytest.raises(sqlite3.IntegrityError):  # IN_PAUSA senza data di riesame
        insert(conn, "StudentLead", id=L1, family_id=F1, display_name="X", status="IN_PAUSA")
    with pytest.raises(sqlite3.IntegrityError):  # ISCRITTO senza data/riferimento
        insert(conn, "StudentLead", id=L1, family_id=F1, display_name="X", status="ISCRITTO")
    with pytest.raises(sqlite3.IntegrityError):  # data inesistente
        insert(conn, "FollowUp", id=I1, family_id=F1, action="RICHIAMARE", due_on="2026-02-30",
               status="APERTO")
    with pytest.raises(sqlite3.IntegrityError):  # etichetta vuota
        insert(conn, "Family", id=F2, display_name="   ")
    with pytest.raises(sqlite3.IntegrityError):  # autore non previsto
        insert(conn, "Family", id=F2, display_name="Y", created_by="Qualcuno")
    with pytest.raises(sqlite3.IntegrityError):  # timestamp non UTC normalizzato
        insert(conn, "Family", id=F2, display_name="Y", created_at="2026-10-01 10:00")


def test_appointment_identity_is_unique(conn):
    appointment(conn)
    with pytest.raises(sqlite3.IntegrityError):
        appointment(conn, aid="99999999-9999-4999-8999-999999999999")


def test_all_day_and_timed_are_exclusive(conn):
    with pytest.raises(sqlite3.IntegrityError):
        appointment(conn, src_start_date="2026-10-02", src_end_date="2026-10-03")
    appointment(conn, src_start_at=None, src_end_at=None, src_start_date="2026-10-02",
                src_end_date="2026-10-03")


def test_transaction_rolls_back_everything(app):
    database = dbmod.Database(app.extensions["kite"].paths.db_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as conn:
            family(conn)
            lead(conn, fid=F2)  # famiglia inesistente: fallisce a metà
    assert database.scalar("SELECT count(*) FROM Family") == 0
    assert database.committed_writes == 0
    with database.transaction() as conn:
        family(conn)
        with database.transaction():  # annidata: si unisce alla transazione esterna
            lead(conn)
    assert database.scalar("SELECT count(*) FROM StudentLead") == 1
    database.close()


def _offer(conn, oid, version, status="BOZZA", previous=None, lead_id=None, **extra):
    values = dict(id=oid, family_id=F1, student_lead_id=lead_id, version_no=version,
                  previous_offer_id=previous, proposed_fee_cents=100000, periodicity="ANNUALE",
                  status=status)
    if status != "BOZZA":
        values.update(communicated_at=TS, communicated_by="Ionut")
    values.update(extra)
    insert(conn, "Offer", **values)


def test_offer_versions_unique_in_scope_including_null_lead(conn):
    family(conn)
    _offer(conn, O1, 1, status="COMUNICATA")
    with pytest.raises(sqlite3.IntegrityError):
        _offer(conn, O2, 1, status="COMUNICATA")
    _offer(conn, O2, 2, previous=O1)
    with pytest.raises(sqlite3.IntegrityError):  # una sola bozza per ambito
        _offer(conn, I1, 3, previous=O2)


def test_offer_predecessor_must_share_scope(conn):
    family(conn)
    lead(conn)
    _offer(conn, O1, 1, status="COMUNICATA")
    with pytest.raises(sqlite3.IntegrityError, match="offer_predecessor_scope"):
        _offer(conn, O2, 1, previous=O1, lead_id=L1)


def test_communicated_offer_is_frozen_in_database(conn):
    family(conn)
    _offer(conn, O1, 1, status="COMUNICATA")
    with pytest.raises(sqlite3.IntegrityError, match="offer_frozen"):
        conn.execute("UPDATE Offer SET proposed_fee_cents = 1 WHERE id = ?", (O1,))
    with pytest.raises(sqlite3.IntegrityError, match="offer_frozen"):
        conn.execute("UPDATE Offer SET communicated_at = ? WHERE id = ?", ("2026-10-02T08:00:00.000000Z", O1))
    with pytest.raises(sqlite3.IntegrityError, match="offer_(status_flow|frozen)"):
        conn.execute("UPDATE Offer SET status = 'BOZZA', communicated_at = NULL, communicated_by = NULL WHERE id = ?", (O1,))
    with pytest.raises(sqlite3.IntegrityError, match="offer_status_flow"):
        conn.execute("UPDATE Offer SET status = 'BOZZA' WHERE id = ?", (O1,))
    conn.execute("UPDATE Offer SET status = 'RITIRATA', withdrawn_at = ?, withdrawal_reason = 'Errore' WHERE id = ?",
                 (TS, O1))
    with pytest.raises(sqlite3.IntegrityError, match="offer_(status_flow|frozen)"):
        conn.execute("UPDATE Offer SET status = 'COMUNICATA', withdrawn_at = NULL, withdrawal_reason = NULL WHERE id = ?", (O1,))


def test_transition_interactions_are_immutable(conn):
    family(conn)
    lead(conn)
    insert(conn, "Interaction", id=I1, family_id=F1, student_lead_id=L1, type="LEAD_ENROLLED",
           occurred_at=TS, text="Iscrizione", origin="Ionut", previous_state="IN_CORSO", next_state="ISCRITTO")
    with pytest.raises(sqlite3.IntegrityError, match="interaction_transition_fixed"):
        conn.execute("UPDATE Interaction SET text = 'altro' WHERE id = ?", (I1,))


def test_transition_requires_states_and_matching_type(conn):
    family(conn)
    lead(conn)
    with pytest.raises(sqlite3.IntegrityError):  # stati mancanti
        insert(conn, "Interaction", id=I1, family_id=F1, student_lead_id=L1, type="LEAD_ENROLLED",
               occurred_at=TS, text="x", origin="Ionut")
    with pytest.raises(sqlite3.IntegrityError):  # tipo incoerente con lo stato successivo
        insert(conn, "Interaction", id=I1, family_id=F1, student_lead_id=L1, type="LEAD_ENROLLED",
               occurred_at=TS, text="x", origin="Ionut", previous_state="IN_CORSO", next_state="NON_PROSEGUE")
    appointment(conn)
    # Transizione Calendar su appuntamento non collegato: famiglia nulla ammessa.
    insert(conn, "Interaction", id=I1, appointment_id=A1, type="CALENDAR_CANCELLED", occurred_at=TS,
           text="Annullato", origin="Calendar import", previous_state="CONFIRMED", next_state="CANCELLED_SOURCE",
           created_by="Calendar import", updated_by="Calendar import")


def test_inspect_detects_states(tmp_path):
    missing = tmp_path / "assente.sqlite3"
    assert dbmod.inspect(missing).status == "missing"
    garbage = tmp_path / "rotto.sqlite3"
    garbage.write_bytes(b"questo non e' un database" * 20)
    assert dbmod.inspect(garbage).status == "corrupt"
    foreign = tmp_path / "estraneo.sqlite3"
    conn = sqlite3.connect(foreign)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    assert dbmod.inspect(foreign).status == "foreign"
    newer = tmp_path / "recente.sqlite3"
    dbmod.initialize(newer)
    conn = sqlite3.connect(newer)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    assert dbmod.inspect(newer).status == "too_new"
    good = tmp_path / "buono.sqlite3"
    dbmod.initialize(good)
    inspection = dbmod.inspect(good)
    assert inspection.status == "ok" and inspection.counts == {table: 0 for table in TABLES}


def test_corrupted_database_starts_in_recovery_mode(home, clock):
    paths = Paths.resolve(home)
    paths.ensure()
    paths.db_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84 + b"danneggiato" * 400)
    app = create_app(home, port=PORT, clock=clock, testing=True)
    from .conftest import LocalClient

    app.test_client_class = LocalClient
    state = app.extensions["kite"]
    assert state.recovery is not None and state.recovery.reason == "corrupt"
    response = app.test_client().get("/")
    assert response.status_code == 302 and "/dati/ripristino" in response.headers["Location"]
    assert app.test_client().get("/health").get_json()["status"] == "recovery"


def test_missing_database_with_backups_does_not_silently_start_empty(make_app, home):
    app = make_app()
    state = app.extensions["kite"]
    backups.create_backup(state.paths, state.settings.load(), backups.KIND_MANUAL, now=state.now())
    state.paths.db_path.unlink()
    second = make_app()
    assert second.extensions["kite"].recovery.reason == "missing"
    assert not state.paths.db_path.exists()


NEXT = SCHEMA_VERSION + 1


def _patch_next(monkeypatch, script):
    """Una versione successiva finta, sopra lo schema corrente."""
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", NEXT)
    monkeypatch.setattr(dbmod, "MIGRATIONS", {**MIGRATIONS, NEXT: script})


def test_migration_requires_successful_preoperation_backup(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    dbmod.initialize(path)
    _patch_next(monkeypatch, "CREATE INDEX ix_prova ON Family (contact_source);")

    def failing_backup():
        raise backups.BackupError("disco pieno (simulato)")

    with pytest.raises(backups.BackupError):
        dbmod.migrate(path, failing_backup)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("SELECT count(*) FROM sqlite_schema WHERE name = 'ix_prova'").fetchone()[0] == 0
    conn.close()

    calls = []
    assert dbmod.migrate(path, lambda: calls.append("backup")) == 1
    assert calls == ["backup"]
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == NEXT
    conn.close()


def test_failed_migration_script_leaves_database_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    dbmod.initialize(path)
    _patch_next(monkeypatch, "CREATE INDEX ix_ok ON Family (contact_source); CREATE INDEX ix_ko ON Tabella_Inesistente (x);")
    with pytest.raises(sqlite3.Error):
        dbmod.migrate(path, lambda: None)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("SELECT count(*) FROM sqlite_schema WHERE name = 'ix_ok'").fetchone()[0] == 0
    conn.close()


def test_app_start_migrates_with_preoperation_backup(make_app, monkeypatch):
    first = make_app()
    paths = first.extensions["kite"].paths
    _patch_next(monkeypatch, "CREATE INDEX ix_prova ON Family (contact_source);")
    monkeypatch.setattr(backups, "SCHEMA_VERSION", NEXT)
    second = make_app()
    assert second.extensions["kite"].recovery is None
    preop = list(paths.preop_backups_dir.glob("*.zip"))
    assert len(preop) == 1 and "pre-migrazione" in preop[0].name
    assert backups.read_manifest(preop[0])["schema_version"] == SCHEMA_VERSION


def test_backup_is_consistent_while_database_is_in_use(app):
    state = app.extensions["kite"]
    writer = dbmod.Database(state.paths.db_path)
    writer.execute("BEGIN IMMEDIATE")
    family(writer.conn)
    info = backups.create_backup(state.paths, state.settings.load(), backups.KIND_MANUAL, now=state.now())
    assert info.counts["Family"] == 0  # la transazione non confermata non entra nella copia
    writer.execute("COMMIT")
    writer.close()
    info = backups.create_backup(state.paths, state.settings.load(), backups.KIND_MANUAL, now=state.now())
    assert info.counts["Family"] == 1
    with zipfile.ZipFile(info.path) as archive:
        names = set(archive.namelist())
    assert names == {"admissions.sqlite3", "settings.json", "manifest.json"}


def test_health_endpoint(client):
    data = client.get("/health").get_json()
    assert data == {"app": "kite-admissions", "version": "1.1.0", "status": "ok", "schema_version": 2}


def test_views_render(client):
    for path in ("/", "/appuntamenti/", "/famiglie/", "/dati/"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "KITE" in response.get_data(as_text=True)
