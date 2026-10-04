"""Explicit C migration for isolated development only; never called at startup."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from operations.backup import verify_backup
from operations.restore_guard import assert_not_quarantined, QUARANTINE_APPLICATION_ID
from operations.time_integrity import utc_now, utc_text

DDL = (
    'CREATE TABLE chief_schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)',
    "CREATE TABLE component_modes(kind TEXT NOT NULL CHECK(kind IN ('component','capability')),id TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN ('ENABLED','DISABLED','SHADOW','MAINTENANCE')),revision INTEGER NOT NULL CHECK(revision>0),changed_at TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(kind,id))",
)
CHECKSUM = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()


def schema_ready(con):
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not names.intersection({'chief_schema_migrations', 'component_modes'}):
        return False
    if not {'chief_schema_migrations', 'component_modes'} <= names:
        raise ValueError('Incomplete component migration.')
    if [tuple(r) for r in con.execute('SELECT version,name,checksum FROM chief_schema_migrations')] != [(1, 'checkpoint-c-components', CHECKSUM)]:
        raise ValueError('Unsupported component migration version/checksum.')
    for name, ddl in zip(('chief_schema_migrations', 'component_modes'), DDL):
        if con.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0] != ddl:
            raise ValueError('Component schema differs from the accepted migration.')
    return True


def legacy_digest(con):
    """Exact legacy SQL/row representation, including sequence state; no data output."""
    data = [('schema', [tuple(r) for r in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE tbl_name NOT IN ('component_modes','chief_schema_migrations') ORDER BY type,name")])]
    for name, sql in con.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT IN ('component_modes','chief_schema_migrations') ORDER BY name"):
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
        if schema_ready(con):
            return 'ALREADY_APPLIED'
        before = legacy_digest(con)
        if before != saved_digest:
            raise ValueError('Fixture changed after backup; create and verify a fresh recovery point.')
        for statement in DDL:
            con.execute(statement)
        con.execute('INSERT INTO chief_schema_migrations VALUES(?,?,?,?)',
                    (1, 'checkpoint-c-components', CHECKSUM, utc_text(utc_now())))
        if before != legacy_digest(con):
            raise ValueError('Migration changed legacy state.')
        schema_ready(con)
    return 'APPLIED'
