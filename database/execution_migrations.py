"""Explicit E migration for isolated development only; never called at startup."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from operations.backup import verify_backup
from operations.restore_guard import assert_not_quarantined, QUARANTINE_APPLICATION_ID
from operations.time_integrity import utc_now, utc_text

DDL = ('CREATE TABLE chief_execution_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT NOT '
 'NULL,applied_at TEXT NOT NULL)',
 'CREATE TABLE model_states(model_id TEXT PRIMARY KEY,state TEXT NOT NULL CHECK(state IN '
 "('ENABLED','SHADOW','DISABLED')),actor TEXT NOT NULL,changed_at TEXT NOT NULL)",
 'CREATE TABLE model_assignments(domain TEXT NOT NULL,agent TEXT NOT NULL,record TEXT NOT NULL,actor TEXT '
 'NOT NULL,changed_at TEXT NOT NULL,PRIMARY KEY(domain,agent))',
 'CREATE TABLE installation_policies(id INTEGER PRIMARY KEY CHECK(id=1),record TEXT NOT NULL,actor TEXT NOT '
 'NULL,changed_at TEXT NOT NULL)',
 'CREATE TABLE shadow_evaluations(id TEXT PRIMARY KEY,domain TEXT NOT NULL,model_id TEXT NOT NULL,record '
 'TEXT NOT NULL,received_at TEXT NOT NULL)',
 'CREATE TABLE sop_definitions(domain TEXT NOT NULL,id TEXT NOT NULL,version TEXT NOT NULL,digest TEXT NOT '
 'NULL,record TEXT NOT NULL,received_at TEXT NOT NULL,PRIMARY KEY(domain,id,version))',
 'CREATE TABLE sop_runs(domain TEXT NOT NULL,id TEXT NOT NULL,definition_id TEXT NOT NULL,version TEXT NOT '
 'NULL,digest TEXT NOT NULL,record TEXT NOT NULL,state TEXT NOT NULL,step TEXT,revision INTEGER NOT '
 'NULL,PRIMARY KEY(domain,id),FOREIGN KEY(domain,definition_id,version) REFERENCES '
 'sop_definitions(domain,id,version))',
 'CREATE TABLE sop_events(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,run_id TEXT NOT NULL,step TEXT,state '
 'TEXT NOT NULL,reason TEXT NOT NULL,approval_ref TEXT,received_at TEXT NOT NULL,FOREIGN KEY(domain,run_id) '
 'REFERENCES sop_runs(domain,id))',
 'CREATE TRIGGER e_chief_execution_migrations_update BEFORE UPDATE ON chief_execution_migrations BEGIN '
 "SELECT RAISE(ABORT,'Append-only E history'); END",
 'CREATE TRIGGER e_chief_execution_migrations_delete BEFORE DELETE ON chief_execution_migrations BEGIN '
 "SELECT RAISE(ABORT,'Append-only E history'); END",
 'CREATE TRIGGER e_shadow_evaluations_update BEFORE UPDATE ON shadow_evaluations BEGIN SELECT '
 "RAISE(ABORT,'Append-only E history'); END",
 'CREATE TRIGGER e_shadow_evaluations_delete BEFORE DELETE ON shadow_evaluations BEGIN SELECT '
 "RAISE(ABORT,'Append-only E history'); END",
 'CREATE TRIGGER e_sop_definitions_update BEFORE UPDATE ON sop_definitions BEGIN SELECT '
 "RAISE(ABORT,'Append-only E history'); END",
 'CREATE TRIGGER e_sop_definitions_delete BEFORE DELETE ON sop_definitions BEGIN SELECT '
 "RAISE(ABORT,'Append-only E history'); END",
 "CREATE TRIGGER e_sop_events_update BEFORE UPDATE ON sop_events BEGIN SELECT RAISE(ABORT,'Append-only E "
 "history'); END",
 "CREATE TRIGGER e_sop_events_delete BEFORE DELETE ON sop_events BEGIN SELECT RAISE(ABORT,'Append-only E "
 "history'); END")
TABLES = ('chief_execution_migrations', 'model_states', 'model_assignments', 'installation_policies', 'shadow_evaluations', 'sop_definitions', 'sop_runs', 'sop_events')
CHECKSUM = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()


def schema_ready(con):
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not names.intersection(TABLES):
        return False
    if not set(TABLES) <= names:
        raise ValueError('Incomplete execution migration.')
    if [tuple(r) for r in con.execute('SELECT version,name,checksum FROM chief_execution_migrations')] != [(1, 'checkpoint-e-execution', CHECKSUM)]:
        raise ValueError('Unsupported execution migration version/checksum.')
    actual = {r[0] for r in con.execute('SELECT sql FROM sqlite_master')}
    if not set(DDL) <= actual:
        raise ValueError('Execution schema differs from the accepted migration.')
    return True


def legacy_digest(con):
    """Exact legacy SQL/row representation, including sequence state; no data output."""
    data = [('schema', [tuple(r) for r in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE tbl_name NOT IN ('chief_execution_migrations','model_states','model_assignments','installation_policies','shadow_evaluations','sop_definitions','sop_runs','sop_events') ORDER BY type,name")])]
    for name, sql in con.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT IN ('chief_execution_migrations','model_states','model_assignments','installation_policies','shadow_evaluations','sop_definitions','sop_runs','sop_events') ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        rows = sorted(repr(tuple(r)) for r in con.execute('SELECT * FROM ' + quoted))
        data.append((name, sql, rows))
    return hashlib.sha256(repr(data).encode()).hexdigest()


def migrate_isolated(store, *, isolated_root, backup_directory, manifest_sha256,
                     expected_source, expected_schema, restore_directory):
    """Require an explicit development marker and verified B.5 backup/restore drill.

    Migrates the original disposable fixture, NOT the quarantined restore.
    No environment switch, API endpoint, autostart or production migration CLI.
    """
    root = Path(isolated_root).resolve(strict=True)
    if json.loads((root / '.chief-isolated-development.json').read_text()) != {'purpose': 'ISOLATED_DEVELOPMENT'}:
        raise PermissionError('An explicit isolated-development fixture is required.')
    path = store.path.resolve(strict=True)
    if not path.is_relative_to(root) or path == root:
        raise PermissionError('Migration database is outside the isolated fixture.')
    assert_not_quarantined(path)
    manifest = verify_backup(backup_directory, manifest_sha256=manifest_sha256,
                             expected_source=expected_source, expected_schema=expected_schema)
    restored = Path(restore_directory).resolve(strict=True)
    if not restored.is_relative_to(root) or not Path(backup_directory).resolve().is_relative_to(root):
        raise PermissionError('Backup and restore drill must be inside the isolated fixture.')
    receipt = json.loads((restored / 'restore-receipt.json').read_text())
    if receipt.get('manifest_sha256') != manifest_sha256 or receipt.get('backup_id') != manifest['backup_id'] or receipt.get('status') != 'QUARANTINED':
        raise ValueError('Verified isolated restore receipt is required.')
    with closing(sqlite3.connect((restored/'state.sqlite3').as_uri()+'?mode=ro', uri=True)) as drill:
        if drill.execute('PRAGMA application_id').fetchone()[0] != QUARANTINE_APPLICATION_ID:
            raise PermissionError('Restore quarantine must remain intact.')
        restored_digest = legacy_digest(drill)
    with closing(sqlite3.connect((Path(backup_directory).resolve()/'state.sqlite3').as_uri()+'?mode=ro&immutable=1', uri=True)) as saved:
        saved_digest = legacy_digest(saved)
    if restored_digest != saved_digest:
        raise ValueError('Restore drill does not preserve the backed-up legacy rows.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        from database.evidence_migrations import schema_ready as c_ready
        if not c_ready(con):
            raise ValueError('Accepted D schema is required.')
        ready = schema_ready(con)
        before = legacy_digest(con)
        if before != saved_digest:
            raise ValueError('Fixture changed after backup; create and verify a fresh recovery point.')
        if ready:
            return 'ALREADY_APPLIED'
        for statement in DDL:
            con.execute(statement)
        con.execute('INSERT INTO chief_execution_migrations VALUES(?,?,?,?)',
                    (1, 'checkpoint-e-execution', CHECKSUM, utc_text(utc_now())))
        if before != legacy_digest(con):
            raise ValueError('Migration changed legacy state.')
        schema_ready(con)
    return 'APPLIED'
