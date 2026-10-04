"""Read-only operational schema preflight; never initializes or migrates."""
from contextlib import closing
import hashlib
import importlib
import json
from pathlib import Path
import re
import sqlite3
from operations.restore_guard import assert_not_quarantined


def fingerprint(con):
    rows=[tuple(r) for r in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_stat%' ORDER BY type,name")]
    return hashlib.sha256(json.dumps(rows,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()


def preflight(database,expected_schema):
    if not isinstance(expected_schema,str) or not re.fullmatch('[0-9a-f]{64}',expected_schema):raise ValueError('An explicit schema fingerprint is required.')
    path=Path(database)
    if not path.is_absolute():raise ValueError('Operational database path must be absolute.')
    for item in (path,*path.parents):
        if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):raise PermissionError('Operational state paths must not use links.')
    path=path.resolve(strict=True);assert_not_quarantined(path)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
        con.execute('PRAGMA query_only=ON')
        if fingerprint(con)!=expected_schema:raise ValueError('Unexpected database schema; explicit verified migration is required.')
        for module in ('migrations','evidence_migrations','execution_migrations','identity_migrations'):
            if not importlib.import_module('database.'+module).schema_ready(con):raise ValueError('Required accepted schema is missing.')
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={'agent_controls','agent_runs','control_state','fact_history','domain_requests','domain_records','domain_reminders','notification_preferences','site_access'}
        columns={r[1] for r in con.execute('PRAGMA table_info(notifications)')}
        if not required<=tables or not {'domain','presented','related_page'}<=columns:raise ValueError('Operational initialization is incomplete; startup will not migrate it.')
        if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Database integrity preflight failed.')
        if con.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Database foreign-key preflight failed.')
    return {'status':'READY','schema_sha256':expected_schema,'schema_migration':False}
