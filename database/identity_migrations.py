"""Explicit F migration for isolated development only; never called at startup."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from operations.backup import verify_backup
from operations.restore_guard import assert_not_quarantined, QUARANTINE_APPLICATION_ID
from operations.time_integrity import utc_now, utc_text

DDL = ('CREATE TABLE chief_identity_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT NOT '
 'NULL,applied_at TEXT NOT NULL)',
 'CREATE TABLE human_identities(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT '
 'NULL,role TEXT NOT NULL,domains TEXT NOT NULL,enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),created_at '
 'TEXT NOT NULL)',
 'CREATE TABLE human_sessions(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,human_id TEXT NOT '
 'NULL,created_at TEXT NOT NULL,last_seen TEXT NOT NULL,expires_at TEXT NOT NULL,reauth_at TEXT NOT '
 'NULL,revoked INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(human_id) REFERENCES human_identities(id))',
 'CREATE TABLE human_login_limits(username_hash TEXT PRIMARY KEY,failures INTEGER NOT NULL,locked_until '
 'TEXT)',
 'CREATE TABLE human_delegations(id TEXT PRIMARY KEY,issuer TEXT NOT NULL,recipient TEXT NOT NULL,domain '
 'TEXT NOT NULL,resource TEXT NOT NULL,expires_at TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,FOREIGN '
 'KEY(issuer) REFERENCES human_identities(id),FOREIGN KEY(recipient) REFERENCES human_identities(id))',
 'CREATE TABLE human_emergencies(id TEXT PRIMARY KEY,issuer TEXT NOT NULL,recipient TEXT NOT NULL,domain '
 'TEXT NOT NULL,expires_at TEXT NOT NULL,reason_ref TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,FOREIGN '
 'KEY(issuer) REFERENCES human_identities(id),FOREIGN KEY(recipient) REFERENCES human_identities(id))',
 'CREATE TABLE human_security_events(id INTEGER PRIMARY KEY,human_id TEXT,session_id TEXT,operation TEXT NOT '
 'NULL,domain TEXT,resource TEXT,outcome TEXT NOT NULL,authority TEXT,received_at TEXT NOT NULL)',
 'CREATE TABLE human_action_approvals(id INTEGER PRIMARY KEY,action_id INTEGER NOT NULL,human_id TEXT NOT '
 'NULL,session_id TEXT NOT NULL,action_digest TEXT NOT NULL,authority TEXT NOT NULL,approved_at TEXT NOT '
 'NULL,FOREIGN KEY(action_id) REFERENCES actions(id))',
 'CREATE TRIGGER f_chief_identity_migrations_update BEFORE UPDATE ON chief_identity_migrations BEGIN SELECT '
 "RAISE(ABORT,'Append-only F history'); END",
 'CREATE TRIGGER f_chief_identity_migrations_delete BEFORE DELETE ON chief_identity_migrations BEGIN SELECT '
 "RAISE(ABORT,'Append-only F history'); END",
 'CREATE TRIGGER f_human_security_events_update BEFORE UPDATE ON human_security_events BEGIN SELECT '
 "RAISE(ABORT,'Append-only F history'); END",
 'CREATE TRIGGER f_human_security_events_delete BEFORE DELETE ON human_security_events BEGIN SELECT '
 "RAISE(ABORT,'Append-only F history'); END",
 'CREATE TRIGGER f_human_action_approvals_update BEFORE UPDATE ON human_action_approvals BEGIN SELECT '
 "RAISE(ABORT,'Append-only F history'); END",
 'CREATE TRIGGER f_human_action_approvals_delete BEFORE DELETE ON human_action_approvals BEGIN SELECT '
 "RAISE(ABORT,'Append-only F history'); END")
TABLES = ('chief_identity_migrations', 'human_identities', 'human_sessions', 'human_login_limits', 'human_delegations', 'human_emergencies', 'human_security_events', 'human_action_approvals')
CHECKSUM = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()


def schema_ready(con):
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not names.intersection(TABLES):
        return False
    if not set(TABLES) <= names:
        raise ValueError('Incomplete identity migration.')
    if [tuple(r) for r in con.execute('SELECT version,name,checksum FROM chief_identity_migrations')] != [(1, 'checkpoint-f-identity', CHECKSUM)]:
        raise ValueError('Unsupported identity migration version/checksum.')
    actual = {r[0] for r in con.execute('SELECT sql FROM sqlite_master')}
    if not set(DDL) <= actual:
        raise ValueError('Identity schema differs from the accepted migration.')
    return True


def legacy_digest(con):
    """Exact legacy SQL/row representation, including sequence state; no data output."""
    data = [('schema', [tuple(r) for r in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE tbl_name NOT IN ('chief_identity_migrations','human_identities','human_sessions','human_login_limits','human_delegations','human_emergencies','human_security_events','human_action_approvals') ORDER BY type,name")])]
    for name, sql in con.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT IN ('chief_identity_migrations','human_identities','human_sessions','human_login_limits','human_delegations','human_emergencies','human_security_events','human_action_approvals') ORDER BY name"):
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
        from database.execution_migrations import schema_ready as c_ready
        if not c_ready(con):
            raise ValueError('Accepted E schema is required.')
        ready = schema_ready(con)
        before = legacy_digest(con)
        if before != saved_digest:
            raise ValueError('Fixture changed after backup; create and verify a fresh recovery point.')
        if ready:
            return 'ALREADY_APPLIED'
        for statement in DDL:
            con.execute(statement)
        con.execute('INSERT INTO chief_identity_migrations VALUES(?,?,?,?)',
                    (1, 'checkpoint-f-identity', CHECKSUM, utc_text(utc_now())))
        if before != legacy_digest(con):
            raise ValueError('Migration changed legacy state.')
        schema_ready(con)
    return 'APPLIED'
