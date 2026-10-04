"""Explicit D migration for isolated development only; never called at startup."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from operations.backup import verify_backup
from operations.restore_guard import assert_not_quarantined, QUARANTINE_APPLICATION_ID
from operations.time_integrity import utc_now, utc_text

DDL = ('CREATE TABLE chief_evidence_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)', 'CREATE TABLE shared_evidence(domain TEXT NOT NULL,id TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(domain,id))', "CREATE TABLE evidence_verifications(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,evidence_id TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('UNVERIFIED','PARTIALLY_VERIFIED','VERIFIED','DISPUTED','STALE')),basis TEXT NOT NULL,actor TEXT NOT NULL,received_at TEXT NOT NULL,FOREIGN KEY(domain,evidence_id) REFERENCES shared_evidence(domain,id))", "CREATE TABLE evidence_contradictions(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,conflict_id TEXT NOT NULL,left_id TEXT NOT NULL,right_id TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('OPEN','RESOLVED')),reason TEXT NOT NULL,received_at TEXT NOT NULL,CHECK(left_id<>right_id),FOREIGN KEY(domain,left_id) REFERENCES shared_evidence(domain,id),FOREIGN KEY(domain,right_id) REFERENCES shared_evidence(domain,id))", 'CREATE TABLE evidence_quality_assessments(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,evidence_id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,FOREIGN KEY(domain,evidence_id) REFERENCES shared_evidence(domain,id))', "CREATE TABLE job_fact_evidence_links(id INTEGER PRIMARY KEY,domain TEXT NOT NULL CHECK(domain='jobs'),fact_id INTEGER NOT NULL,evidence_id TEXT NOT NULL,fingerprint TEXT NOT NULL,received_at TEXT NOT NULL,FOREIGN KEY(fact_id) REFERENCES candidate_facts(id),FOREIGN KEY(domain,evidence_id) REFERENCES shared_evidence(domain,id),UNIQUE(fact_id,evidence_id))", 'CREATE TABLE decision_ledger(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,event_key TEXT NOT NULL,correlation_id TEXT NOT NULL,record TEXT NOT NULL,UNIQUE(domain,event_key))', "CREATE TRIGGER d_chief_evidence_migrations_update BEFORE UPDATE ON chief_evidence_migrations BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_chief_evidence_migrations_delete BEFORE DELETE ON chief_evidence_migrations BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_shared_evidence_update BEFORE UPDATE ON shared_evidence BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_shared_evidence_delete BEFORE DELETE ON shared_evidence BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_evidence_verifications_update BEFORE UPDATE ON evidence_verifications BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_evidence_verifications_delete BEFORE DELETE ON evidence_verifications BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_evidence_contradictions_update BEFORE UPDATE ON evidence_contradictions BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_evidence_contradictions_delete BEFORE DELETE ON evidence_contradictions BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_evidence_quality_assessments_update BEFORE UPDATE ON evidence_quality_assessments BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_evidence_quality_assessments_delete BEFORE DELETE ON evidence_quality_assessments BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_job_fact_evidence_links_update BEFORE UPDATE ON job_fact_evidence_links BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_job_fact_evidence_links_delete BEFORE DELETE ON job_fact_evidence_links BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_decision_ledger_update BEFORE UPDATE ON decision_ledger BEGIN SELECT RAISE(ABORT,'Append-only D history'); END", "CREATE TRIGGER d_decision_ledger_delete BEFORE DELETE ON decision_ledger BEGIN SELECT RAISE(ABORT,'Append-only D history'); END")
TABLES = ('chief_evidence_migrations', 'shared_evidence', 'evidence_verifications', 'evidence_contradictions', 'evidence_quality_assessments', 'job_fact_evidence_links', 'decision_ledger')
CHECKSUM = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()


def schema_ready(con):
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not names.intersection(TABLES):
        return False
    if not set(TABLES) <= names:
        raise ValueError('Incomplete evidence migration.')
    if [tuple(r) for r in con.execute('SELECT version,name,checksum FROM chief_evidence_migrations')] != [(1, 'checkpoint-d-evidence', CHECKSUM)]:
        raise ValueError('Unsupported evidence migration version/checksum.')
    actual = {r[0] for r in con.execute('SELECT sql FROM sqlite_master')}
    if not set(DDL) <= actual:
        raise ValueError('Evidence schema differs from the accepted migration.')
    return True


def legacy_digest(con):
    """Exact legacy SQL/row representation, including sequence state; no data output."""
    data = [('schema', [tuple(r) for r in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE tbl_name NOT IN ('chief_evidence_migrations','shared_evidence','evidence_verifications','evidence_contradictions','evidence_quality_assessments','job_fact_evidence_links','decision_ledger') ORDER BY type,name")])]
    for name, sql in con.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT IN ('chief_evidence_migrations','shared_evidence','evidence_verifications','evidence_contradictions','evidence_quality_assessments','job_fact_evidence_links','decision_ledger') ORDER BY name"):
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
        from database.migrations import schema_ready as c_ready
        if not c_ready(con):
            raise ValueError('Accepted C schema is required.')
        ready = schema_ready(con)
        before = legacy_digest(con)
        if before != saved_digest:
            raise ValueError('Fixture changed after backup; create and verify a fresh recovery point.')
        if ready:
            return 'ALREADY_APPLIED'
        for statement in DDL:
            con.execute(statement)
        con.execute('INSERT INTO chief_evidence_migrations VALUES(?,?,?,?)',
                    (1, 'checkpoint-d-evidence', CHECKSUM, utc_text(utc_now())))
        if before != legacy_digest(con):
            raise ValueError('Migration changed legacy state.')
        schema_ready(con)
    return 'APPLIED'
