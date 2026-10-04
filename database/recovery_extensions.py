from __future__ import annotations
import json
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS submission_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 application_id TEXT NOT NULL,
 phase TEXT NOT NULL,
 outcome TEXT NOT NULL DEFAULT 'STARTED',
 form_hash TEXT NOT NULL DEFAULT '',
 details TEXT NOT NULL DEFAULT '',
 data_json TEXT NOT NULL DEFAULT '{}',
 started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT,
 FOREIGN KEY(application_id) REFERENCES applications(id)
);
"""

def init_recovery(store):
    with store._connect() as con:
        con.executescript(SCHEMA)
        cols={r[1] for r in con.execute('PRAGMA table_info(applications)').fetchall()}
        for name, definition in {
            'recovery_status': "TEXT NOT NULL DEFAULT 'NONE'",
            'recovery_reason': "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in cols:
                con.execute(f'ALTER TABLE applications ADD COLUMN {name} {definition}')

def add_submission_attempt(store, application_id, phase, outcome='STARTED', *, form_hash='', details='', data=None):
    with store._connect() as con:
        cur=con.execute('INSERT INTO submission_attempts(application_id,phase,outcome,form_hash,details,data_json) VALUES(?,?,?,?,?,?)',
                        (application_id,phase,outcome,form_hash,details,json.dumps(data or {},ensure_ascii=False)))
        return int(cur.lastrowid)

def finish_submission_attempt(store, attempt_id, outcome, *, details='', data=None):
    with store._connect() as con:
        con.execute('UPDATE submission_attempts SET outcome=?,details=?,data_json=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',
                    (outcome,details,json.dumps(data or {},ensure_ascii=False),attempt_id))

def submission_attempt(store, attempt_id):
    with store._connect() as con:
        r=con.execute('SELECT * FROM submission_attempts WHERE id=?',(attempt_id,)).fetchone()
        return dict(r) if r else None

def submission_attempts(store, application_id, limit=100):
    with store._connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM submission_attempts WHERE application_id=? ORDER BY id DESC LIMIT ?',(application_id,limit))]

def set_application_recovery(store, application_id, status, reason):
    with store._connect() as con:
        con.execute('UPDATE applications SET recovery_status=?,recovery_reason=? WHERE id=?',(status,reason,application_id))

def latest_submission_attempt(store, application_id):
    rows = submission_attempts(store, application_id, limit=1)
    return rows[0] if rows else None
