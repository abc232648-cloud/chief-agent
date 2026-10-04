"""Explicit additive indexes only; never auto-migrate an operational database."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from operations.backup import verify_backup
from operations.restore_guard import assert_not_quarantined, QUARANTINE_APPLICATION_ID

WHERE = "domain='farming' AND kind='poultry_journal_v1'"
STOCK = "coalesce('entity:'||json_extract(data_json,'$.payload.entity_id'),'legacy:'||json_extract(data_json,'$.payload.location'))"
DDL = {
    'farm_records_kind': 'CREATE INDEX farm_records_kind ON domain_records(domain,kind,id)',
    'farm_journal_event': "CREATE UNIQUE INDEX farm_journal_event ON domain_records(json_extract(data_json,'$.payload.event_id')) WHERE "+WHERE,
    'farm_journal_corrects': "CREATE INDEX farm_journal_corrects ON domain_records(json_extract(data_json,'$.payload.corrects')) WHERE "+WHERE,
    'farm_journal_actor': "CREATE INDEX farm_journal_actor ON domain_records(json_extract(data_json,'$.actor_id'),id) WHERE "+WHERE,
    'farm_journal_stock': 'CREATE INDEX farm_journal_stock ON domain_records('+STOCK+',id) WHERE '+WHERE,
    'farm_photo_identity': "CREATE UNIQUE INDEX farm_photo_identity ON domain_records(json_extract(data_json,'$.id')) WHERE domain='farming' AND kind='farm_photo_v1'",
    'farm_photo_work': "CREATE INDEX farm_photo_work ON domain_records(json_extract(data_json,'$.work_id'),id) WHERE domain='farming' AND kind='farm_photo_v1'",
}


def schema_ready(con):
    found = dict(con.execute("SELECT name,sql FROM sqlite_master WHERE type='index'"))
    present = set(DDL) & found.keys()
    if not present:
        return False
    if any(found.get(name) != sql for name,sql in DDL.items()):
        raise ValueError('Incomplete or altered Farm index migration.')
    return True


def legacy_digest(con):
    schema = [tuple(r) for r in con.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name') if r[1] not in DDL]
    rows=[]
    for name, in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted='"'+name.replace('"','""')+'"'
        rows.append((name, sorted(repr(tuple(r)) for r in con.execute('SELECT * FROM '+quoted))))
    return hashlib.sha256(repr((schema,rows)).encode()).hexdigest()


def migrate_isolated(store, *, isolated_root, backup_directory, manifest_sha256,
                     expected_source, expected_schema, restore_directory):
    root=Path(isolated_root).resolve(strict=True)
    if json.loads((root/'.chief-isolated-development.json').read_text()) != {'purpose':'ISOLATED_DEVELOPMENT'}:
        raise PermissionError('An explicit isolated fixture is required.')
    path=store.path.resolve(strict=True)
    backup=Path(backup_directory).resolve(strict=True)
    restored=Path(restore_directory).resolve(strict=True)
    if not all(p.is_relative_to(root) and p != root for p in (path,backup,restored)):
        raise PermissionError('Database, backup and restore must be inside the isolated fixture.')
    assert_not_quarantined(path)
    manifest=verify_backup(backup,manifest_sha256=manifest_sha256,expected_source=expected_source,expected_schema=expected_schema)
    receipt=json.loads((restored/'restore-receipt.json').read_text())
    if receipt.get('manifest_sha256')!=manifest_sha256 or receipt.get('backup_id')!=manifest['backup_id'] or receipt.get('status')!='QUARANTINED':
        raise ValueError('Verified quarantined restore required.')
    with closing(sqlite3.connect((restored/'state.sqlite3').as_uri()+'?mode=ro',uri=True)) as drill:
        if drill.execute('PRAGMA application_id').fetchone()[0]!=QUARANTINE_APPLICATION_ID:
            raise PermissionError('Restore must remain quarantined.')
        restored_digest=legacy_digest(drill)
    with closing(sqlite3.connect((backup/'state.sqlite3').as_uri()+'?mode=ro&immutable=1',uri=True)) as saved:
        saved_digest=legacy_digest(saved)
    if saved_digest!=restored_digest:
        raise ValueError('Restore does not preserve original state.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        from database.identity_migrations import schema_ready as identity_ready
        if not identity_ready(con):raise ValueError('Accepted identity schema is required.')
        before=legacy_digest(con)
        if before!=saved_digest:raise ValueError('Fixture changed after backup. Make a fresh recovery point.')
        if schema_ready(con):return 'ALREADY_APPLIED'
        for statement in DDL.values():con.execute(statement)
        if legacy_digest(con)!=before:raise ValueError('Migration changed existing state.')
        schema_ready(con)
    return 'APPLIED'
