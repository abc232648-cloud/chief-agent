from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from operations.restore_guard import assert_not_quarantined

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "database" / "worker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
 platform TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', remote INTEGER NOT NULL DEFAULT 0,
 fit_score REAL, scam_status TEXT, confidence REAL, status TEXT NOT NULL DEFAULT 'NEW',
 raw_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, canonical_url TEXT NOT NULL DEFAULT '', dedupe_key TEXT NOT NULL DEFAULT '', rank_score REAL, duplicate_of TEXT
);
CREATE TABLE IF NOT EXISTS applications (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
 submitted_at TEXT, notes TEXT NOT NULL DEFAULT '', draft_json TEXT NOT NULL DEFAULT '{}', cv_path TEXT NOT NULL DEFAULT '', cover_letter_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS actions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, action TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'PENDING', priority INTEGER NOT NULL DEFAULT 50, payload_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'INFO',
 read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'platform',
 protocol TEXT NOT NULL, verification_status TEXT NOT NULL DEFAULT 'REVIEW', confidence REAL, notes TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS commands (
 id INTEGER PRIMARY KEY AUTOINCREMENT, instruction TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'QUEUED',
 result TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, period TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS worker_status (
 id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL DEFAULT 'IDLE', message TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

class ClosingConnection(sqlite3.Connection):
    """Commit/rollback with normal SQLite semantics, then release the connection."""
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB, *, initialize=True, expected_schema=None):
        self.path = Path(path)
        self.operational = not initialize
        assert_not_quarantined(self.path)
        if initialize and (self.path.parent/'.chief-production.json').exists():
            raise PermissionError('Production state requires explicit read-only schema preflight before opening.')
        if not initialize:
            from deployment.schema import preflight
            preflight(self.path,expected_schema)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        assert_not_quarantined(self.path)
        con = sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=10,factory=ClosingConnection) if self.operational else sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        try:
            con.row_factory = sqlite3.Row
            from operations.storage_health import configure_connection
            configure_connection(con)
        except BaseException:
            con.close()
            raise
        if self.operational:
            forbidden={sqlite3.SQLITE_CREATE_INDEX,sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_CREATE_TRIGGER,sqlite3.SQLITE_CREATE_VIEW,sqlite3.SQLITE_DROP_INDEX,sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_DROP_TRIGGER,sqlite3.SQLITE_DROP_VIEW,sqlite3.SQLITE_ALTER_TABLE,sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH}
            forbidden.update({sqlite3.SQLITE_CREATE_TEMP_INDEX,sqlite3.SQLITE_CREATE_TEMP_TABLE,sqlite3.SQLITE_CREATE_TEMP_TRIGGER,sqlite3.SQLITE_CREATE_TEMP_VIEW,sqlite3.SQLITE_DROP_TEMP_INDEX,sqlite3.SQLITE_DROP_TEMP_TABLE,sqlite3.SQLITE_DROP_TEMP_TRIGGER,sqlite3.SQLITE_DROP_TEMP_VIEW})
            def authorize(action,name,value,*args):
                if action in forbidden or (action==sqlite3.SQLITE_PRAGMA and value is not None and name.lower() in {'writable_schema','schema_version','user_version','application_id'}):return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            con.set_authorizer(authorize)
        return con

    def _init(self):
        with self._connect() as con:
            con.executescript(SCHEMA)
            con.execute("INSERT OR IGNORE INTO worker_status(id,status,message) VALUES(1,'IDLE','Waiting for work')")
            app_columns = {row[1] for row in con.execute("PRAGMA table_info(applications)").fetchall()}
            for name, definition in {
                'draft_json': "TEXT NOT NULL DEFAULT '{}'",
                'cv_path': "TEXT NOT NULL DEFAULT ''",
                'cover_letter_path': "TEXT NOT NULL DEFAULT ''",
                'submitted_at': "TEXT",
                'submission_receipt_json': "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if name not in app_columns:
                    con.execute(f"ALTER TABLE applications ADD COLUMN {name} {definition}")
            columns = {row[1] for row in con.execute("PRAGMA table_info(jobs)").fetchall()}
            for name, definition in {
                "canonical_url": "TEXT NOT NULL DEFAULT ''",
                "dedupe_key": "TEXT NOT NULL DEFAULT ''",
                "rank_score": "REAL",
                "duplicate_of": "TEXT",
            }.items():
                if name not in columns:
                    con.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")

    def counts(self) -> dict[str, int]:
        with self._connect() as c:
            return {k: c.execute(q).fetchone()[0] for k,q in {
                'jobs': 'SELECT COUNT(*) FROM jobs',
                'applications': 'SELECT COUNT(*) FROM applications',
                'actions': "SELECT COUNT(*) FROM actions WHERE status='PENDING'",
                'notifications': "SELECT COUNT(*) FROM notifications WHERE read=0",
                'reports': 'SELECT COUNT(*) FROM reports',
                'commands': "SELECT COUNT(*) FROM commands WHERE status='QUEUED'",
            }.items()}

    def worker(self) -> dict[str, Any]:
        with self._connect() as c:
            r=c.execute('SELECT status,message,updated_at FROM worker_status WHERE id=1').fetchone()
            return dict(r)

    def set_worker(self, status: str, message: str = '') -> None:
        with self._connect() as c:
            c.execute("UPDATE worker_status SET status=?,message=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",(status,message))

    def queue_command(self, instruction: str) -> int:
        with self._connect() as c:
            cur=c.execute('INSERT INTO commands(instruction) VALUES(?)',(instruction,))
            return int(cur.lastrowid)

    def commands(self, limit=50):
        with self._connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM commands ORDER BY id DESC LIMIT ?', (limit,))]

    def next_queued_command(self):
        # Atomically claim one command so two worker processes cannot execute it twice.
        con = self._connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT * FROM commands WHERE status='QUEUED' ORDER BY id ASC LIMIT 1").fetchone()
            if not row:
                con.commit(); return None
            con.execute("UPDATE commands SET status='PROCESSING' WHERE id=? AND status='QUEUED'", (row['id'],))
            claimed = con.execute('SELECT * FROM commands WHERE id=?', (row['id'],)).fetchone()
            con.commit()
            return dict(claimed) if claimed else None
        finally:
            con.close()

    def next_approved_action(self):
        # Atomically claim a user-approved action for the worker.
        con = self._connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT * FROM actions WHERE status='APPROVED' ORDER BY priority DESC,id ASC LIMIT 1").fetchone()
            if not row:
                con.commit(); return None
            con.execute("UPDATE actions SET status='EXECUTING' WHERE id=? AND status='APPROVED'", (row['id'],))
            claimed = con.execute('SELECT * FROM actions WHERE id=?', (row['id'],)).fetchone()
            con.commit()
            return dict(claimed) if claimed else None
        finally:
            con.close()

    def reset_executing_actions(self):
        # A crash may follow an external side effect. Never replay it automatically.
        with self._connect() as c:
            rows=c.execute("SELECT id FROM actions WHERE status='EXECUTING'").fetchall()
            c.execute("UPDATE actions SET status='REVIEW',resolved_at=CURRENT_TIMESTAMP WHERE status='EXECUTING'")
        for row in rows:
            self.add_notification('Interrupted action requires review',f"Action #{row['id']} was interrupted. Its external outcome was not established; inspect the application before retrying.",'ACTION_REQUIRED',domain='jobs',related_page='applicationArchive')

    def update_command(self, command_id: int, status: str, result: str = '') -> None:
        with self._connect() as c:
            c.execute("UPDATE commands SET status=?,result=?,processed_at=CURRENT_TIMESTAMP WHERE id=?", (status,result,command_id))

    def add_action(self, name: str, action: str, description='', priority=50, payload=None) -> int:
        with self._connect() as c:
            cur=c.execute('INSERT INTO actions(name,action,description,priority,payload_json) VALUES(?,?,?,?,?)',
                          (name,action,description,priority,json.dumps(payload or {})))
            return int(cur.lastrowid)

    def actions(self, pending_only=True, limit=100):
        q='SELECT * FROM actions WHERE status=\'PENDING\' ORDER BY priority DESC,id DESC LIMIT ?' if pending_only else 'SELECT * FROM actions ORDER BY id DESC LIMIT ?'
        with self._connect() as c: return [dict(r) for r in c.execute(q,(limit,))]

    def resolve_action(self, action_id: int, status: str) -> None:
        if status not in {'APPROVED','REJECTED','DONE'}: raise ValueError('Invalid action status')
        with self._connect() as c: c.execute('UPDATE actions SET status=?,resolved_at=CURRENT_TIMESTAMP WHERE id=?',(status,action_id))

    def find_job_by_dedupe_key(self, key: str):
        with self._connect() as c:
            row = c.execute("SELECT * FROM jobs WHERE dedupe_key=? AND status!='DUPLICATE' ORDER BY updated_at DESC LIMIT 1", (key,)).fetchone()
            return dict(row) if row else None

    def add_job(self, job: dict[str, Any]) -> None:
        with self._connect() as c:
            c.execute(
                """INSERT INTO jobs(
                    id,title,company,url,platform,location,remote,fit_score,scam_status,confidence,status,raw_json,
                    canonical_url,dedupe_key,rank_score,duplicate_of
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, company=excluded.company, url=excluded.url, platform=excluded.platform,
                    location=excluded.location, remote=excluded.remote, fit_score=excluded.fit_score,
                    scam_status=excluded.scam_status, confidence=excluded.confidence, status=excluded.status,
                    raw_json=excluded.raw_json, canonical_url=excluded.canonical_url, dedupe_key=excluded.dedupe_key,
                    rank_score=excluded.rank_score, duplicate_of=excluded.duplicate_of, updated_at=CURRENT_TIMESTAMP""",
                (
                    job['id'], job.get('title',''), job.get('company',''), job.get('url',''), job.get('platform',''),
                    job.get('location',''), int(bool(job.get('remote'))), job.get('fit_score'), job.get('scam_status'),
                    job.get('confidence'), job.get('status','NEW'), json.dumps(job), job.get('canonical_url',''),
                    job.get('dedupe_key',''), job.get('rank_score'), job.get('duplicate_of')
                )
            )

    def jobs(self, limit=100, *, include_duplicates=True):
        where = "" if include_duplicates else " WHERE status!='DUPLICATE'"
        with self._connect() as c:
            return [dict(r) for r in c.execute(
                f'SELECT * FROM jobs{where} ORDER BY rank_score DESC, updated_at DESC LIMIT ?', (limit,)
            )]


    def add_application(self, application_id: str, job_id: str, status='DRAFT', notes='', draft=None, cv_path='', cover_letter_path='') -> None:
        with self._connect() as c:
            c.execute(
                """INSERT INTO applications(id,job_id,status,notes,draft_json,cv_path,cover_letter_path)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET job_id=excluded.job_id,status=excluded.status,notes=excluded.notes,
                draft_json=excluded.draft_json,cv_path=excluded.cv_path,cover_letter_path=excluded.cover_letter_path""",
                (application_id, job_id, status, notes, json.dumps(draft or {}, ensure_ascii=False), cv_path, cover_letter_path)
            )

    def mark_application_submitted(self, application_id: str, submitted_at: str, receipt: dict[str, Any]) -> None:
        with self._connect() as c:
            cur = c.execute("UPDATE applications SET status='SUBMITTED', submitted_at=?, submission_receipt_json=? WHERE id=? AND (submitted_at IS NULL OR submitted_at='')",
                            (submitted_at, json.dumps(receipt, ensure_ascii=False), application_id))
            if cur.rowcount != 1:
                raise ValueError('Application was already submitted or does not exist')

    def application_form_fields(self, application_id: str) -> list[dict[str, Any]]:
        with self._connect() as c:
            row = c.execute("SELECT form_fields_json FROM application_snapshots WHERE application_id=? AND stage='FORM_FILLED' ORDER BY id DESC LIMIT 1", (application_id,)).fetchone()
            if not row: return []
            try: return json.loads(row['form_fields_json'] or '[]')
            except json.JSONDecodeError: return []

    def applications(self, limit=100):
        with self._connect() as c:
            return [dict(r) for r in c.execute('''SELECT a.*,j.title,j.company FROM applications a LEFT JOIN jobs j ON j.id=a.job_id ORDER BY a.created_at DESC LIMIT ?''',(limit,))]

    def latest_submission_attempt(self, application_id, limit=1):
        with self._connect() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM submission_attempts WHERE application_id=? ORDER BY id DESC LIMIT ?', (application_id, limit))]
            return rows[0] if rows else None

    def add_notification(self,title,body,severity='INFO',domain='system',related_page=''):
        from control.notifications import initialize
        initialize(self)
        with self._connect() as c:c.execute('INSERT INTO notifications(title,body,severity,domain,related_page,presented) VALUES(?,?,?,?,?,0)',(title,body,severity,domain,related_page))

    def notifications(self, limit=50):
        with self._connect() as c: return [dict(r) for r in c.execute('SELECT * FROM notifications ORDER BY id DESC LIMIT ?',(limit,))]

    def add_source(self, source: dict[str,Any]):
        with self._connect() as c: c.execute('''INSERT INTO sources(id,name,url,kind,protocol,verification_status,confidence,notes) VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,kind=excluded.kind,protocol=excluded.protocol,
          verification_status=excluded.verification_status,confidence=excluded.confidence,notes=excluded.notes,updated_at=CURRENT_TIMESTAMP''',
          (source['id'],source['name'],source['url'],source.get('kind','platform'),source.get('protocol','HTTPS'),source.get('verification_status','REVIEW'),source.get('confidence'),source.get('notes','')))

    def sources(self, limit=100):
        with self._connect() as c: return [dict(r) for r in c.execute('SELECT * FROM sources ORDER BY updated_at DESC LIMIT ?',(limit,))]

    def add_report(self,period,path):
        with self._connect() as c: c.execute('INSERT INTO reports(period,path) VALUES(?,?)',(period,path))

    def reports(self, limit=100):
        with self._connect() as c: return [dict(r) for r in c.execute('SELECT * FROM reports ORDER BY id DESC LIMIT ?',(limit,))]

# v19 extensions: candidate CV library, profile monitoring, and whole-system audit log.
from .store_extensions import init_extensions, add_audit, audit, add_cv, cvs, set_cv_active, delete_cv, add_profile_link, profile_links, update_profile_link, delete_profile_link, add_monitoring_task, monitoring_tasks
_old_init = Store._init
def _init_v19(self):
    _old_init(self)
    init_extensions(self)
Store._init = _init_v19
Store.add_audit = add_audit
Store.audit = audit
Store.add_cv = add_cv
Store.cvs = cvs
Store.set_cv_active = set_cv_active
Store.delete_cv = delete_cv
Store.add_profile_link = add_profile_link
Store.profile_links = profile_links
Store.update_profile_link = update_profile_link
Store.delete_profile_link = delete_profile_link
Store.add_monitoring_task = add_monitoring_task
Store.monitoring_tasks = monitoring_tasks

# Audit important state mutations without changing existing call sites.
_original_queue_command = Store.queue_command
def _queue_command_audit(self, instruction):
    cid = _original_queue_command(self, instruction)
    add_audit(self, 'command', f'Queued dashboard command #{cid}', details=instruction, data={'command_id': cid})
    return cid
Store.queue_command = _queue_command_audit
_original_add_application = Store.add_application
def _add_application_audit(self, *args, **kwargs):
    result = _original_add_application(self, *args, **kwargs)
    add_audit(self, 'application', 'Saved application record', data={'application_id': args[0] if args else None, 'job_id': args[1] if len(args)>1 else None})
    return result
Store.add_application = _add_application_audit
# v20: explicit user-controlled candidate fact governance.
from .store_extensions import add_candidate_fact, candidate_facts, update_candidate_fact
Store.add_candidate_fact = add_candidate_fact
Store.candidate_facts = candidate_facts
Store.update_candidate_fact = update_candidate_fact

# v21: controlled application execution and approved-action retrieval.
from .store_extensions import get_action, add_application_snapshot, application_snapshots, add_application_event, application_events, application_detail
Store.get_action = get_action
Store.add_application_snapshot = add_application_snapshot
Store.application_snapshots = application_snapshots
Store.add_application_event = add_application_event
Store.application_events = application_events
Store.application_detail = application_detail

# v24: conservative submission recovery and duplicate-submission protection state.
from .recovery_extensions import init_recovery, add_submission_attempt, finish_submission_attempt, submission_attempt, submission_attempts, set_application_recovery
_old_init_v20 = Store._init
# Preserve the previous initialization wrapper and add the v24 migration after it.
def _init_v24(self):
    _old_init_v20(self)
    init_recovery(self)
Store._init = _init_v24
Store.add_submission_attempt = add_submission_attempt
Store.finish_submission_attempt = finish_submission_attempt
Store.submission_attempt = submission_attempt
Store.submission_attempts = submission_attempts
Store.set_application_recovery = set_application_recovery

# v25: persistent scheduler/report helpers.
def _report_exists(self, period):
    with self._connect() as c:
        return c.execute('SELECT 1 FROM reports WHERE period=? LIMIT 1', (period,)).fetchone() is not None
Store.report_exists = _report_exists
