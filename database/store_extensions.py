from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from security_hardening import redact

SCHEMA_EXT = """
CREATE TABLE IF NOT EXISTS cv_profiles (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, role_type TEXT NOT NULL DEFAULT '', variant TEXT NOT NULL DEFAULT '',
 file_path TEXT NOT NULL, file_type TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS profile_links (
 id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, url TEXT NOT NULL, profile_type TEXT NOT NULL DEFAULT 'other',
 enabled INTEGER NOT NULL DEFAULT 1, verification_status TEXT NOT NULL DEFAULT 'REVIEW', check_interval_days INTEGER NOT NULL DEFAULT 7,
 last_checked_at TEXT, last_status TEXT NOT NULL DEFAULT 'NEVER_CHECKED', notes TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, category TEXT NOT NULL,
 actor TEXT NOT NULL DEFAULT 'system', action TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'COMPLETED',
 details TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS candidate_facts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PROPOSED', source_type TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL DEFAULT '', source_detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, confirmed_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS application_snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'DRAFT',
 cv_id TEXT NOT NULL DEFAULT '', cv_name TEXT NOT NULL DEFAULT '', cv_variant TEXT NOT NULL DEFAULT '', cv_path TEXT NOT NULL DEFAULT '',
 cv_snapshot_json TEXT NOT NULL DEFAULT '{}', cover_letter_text TEXT NOT NULL DEFAULT '', form_fields_json TEXT NOT NULL DEFAULT '[]',
 source_url TEXT NOT NULL DEFAULT '', captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(application_id) REFERENCES applications(id)
);
CREATE TABLE IF NOT EXISTS application_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL, event_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'RECORDED',
 details TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL DEFAULT '{}', event_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(application_id) REFERENCES applications(id)
);
CREATE TABLE IF NOT EXISTS monitoring_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_type TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
 interval_days INTEGER NOT NULL DEFAULT 1, last_run_at TEXT, next_due_at TEXT, status TEXT NOT NULL DEFAULT 'READY',
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS web_push_subscriptions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 human_id TEXT NOT NULL,
 domain TEXT NOT NULL CHECK(domain='jobs') DEFAULT 'jobs',
 endpoint TEXT NOT NULL UNIQUE,
 p256dh TEXT NOT NULL,
 auth TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_web_push_subscriptions_human_domain ON web_push_subscriptions(human_id,domain);
"""


def init_extensions(store):
    with store._connect() as con:
        con.executescript(SCHEMA_EXT)


def add_audit(store, category: str, action: str, status: str = 'COMPLETED', details: str = '', data: dict[str, Any] | None = None, actor='system'):
    from identity.context import current_human
    from operations.correlation import references
    data={**(data or {}),**references()}
    human=current_human()
    if human is not None:
        actor=human.id
        data={**(data or {}),'human_id':human.id,'human_session_id':human.session_id}
    with store._connect() as con:
        con.execute('INSERT INTO audit_log(category,actor,action,status,details,data_json) VALUES(?,?,?,?,?,?)',
                    (category, actor, action, status, details, json.dumps(redact(data or {}), ensure_ascii=False)))


def audit(store, limit=500, start=None, end=None):
    q='SELECT * FROM audit_log WHERE 1=1'; args=[]
    if start: q += ' AND event_time>=?'; args.append(start)
    if end: q += ' AND event_time<?'; args.append(end)
    q += ' ORDER BY id DESC LIMIT ?'; args.append(limit)
    with store._connect() as con: return [dict(r) for r in con.execute(q,args)]


def add_cv(store, item):
    with store._connect() as con:
        con.execute('''INSERT INTO cv_profiles(id,name,role_type,variant,file_path,file_type,active,notes)
                       VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,role_type=excluded.role_type,
                       variant=excluded.variant,file_path=excluded.file_path,file_type=excluded.file_type,active=excluded.active,
                       notes=excluded.notes,updated_at=CURRENT_TIMESTAMP''',
                    (item['id'],item['name'],item.get('role_type',''),item.get('variant',''),item['file_path'],item.get('file_type',''),int(item.get('active',True)),item.get('notes','')))


def cvs(store, limit=200):
    with store._connect() as con: return [dict(r) for r in con.execute('SELECT * FROM cv_profiles ORDER BY updated_at DESC LIMIT ?', (limit,))]


def set_cv_active(store, cv_id, active):
    with store._connect() as con: con.execute('UPDATE cv_profiles SET active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(int(bool(active)),cv_id))


def delete_cv(store, cv_id):
    with store._connect() as con: con.execute('DELETE FROM cv_profiles WHERE id=?',(cv_id,))


def add_profile_link(store, item):
    with store._connect() as con:
        cur=con.execute('''INSERT INTO profile_links(label,url,profile_type,enabled,verification_status,check_interval_days,notes)
                           VALUES(?,?,?,?,?,?,?)''',(item['label'],item['url'],item.get('profile_type','other'),int(item.get('enabled',True)),item.get('verification_status','REVIEW'),int(item.get('check_interval_days',7)),item.get('notes','')))
        return int(cur.lastrowid)


def profile_links(store, limit=200):
    with store._connect() as con: return [dict(r) for r in con.execute('SELECT * FROM profile_links ORDER BY updated_at DESC LIMIT ?', (limit,))]


def update_profile_link(store, link_id, **fields):
    allowed={'label','url','profile_type','enabled','verification_status','check_interval_days','last_checked_at','last_status','notes'}
    fields={k:v for k,v in fields.items() if k in allowed}
    if not fields: return
    fields['updated_at']=datetime.now(timezone.utc).isoformat()
    sets=','.join(f'{k}=?' for k in fields); vals=list(fields.values())+[link_id]
    with store._connect() as con: con.execute(f'UPDATE profile_links SET {sets} WHERE id=?', vals)


def delete_profile_link(store, link_id):
    with store._connect() as con: con.execute('DELETE FROM profile_links WHERE id=?',(link_id,))


def add_monitoring_task(store, task):
    with store._connect() as con:
        cur=con.execute('''INSERT INTO monitoring_tasks(task_type,target,enabled,interval_days,last_run_at,next_due_at,status,notes)
                           VALUES(?,?,?,?,?,?,?,?)''',(task['task_type'],task.get('target',''),int(task.get('enabled',True)),int(task.get('interval_days',1)),task.get('last_run_at'),task.get('next_due_at'),task.get('status','READY'),task.get('notes','')))
        return int(cur.lastrowid)


def monitoring_tasks(store, limit=200):
    with store._connect() as con: return [dict(r) for r in con.execute('SELECT * FROM monitoring_tasks ORDER BY id DESC LIMIT ?', (limit,))]


def add_candidate_fact(store, item):
    status = item.get('status', 'PROPOSED')
    if status not in {'USER_CONFIRMED','PROPOSED','REVOKED'}:
        raise ValueError('Invalid candidate fact status')
    with store._connect() as con:
        cur = con.execute(
            "INSERT INTO candidate_facts(text,status,source_type,source_id,source_detail) VALUES(?,?,?,?,?)",
            (str(item['text']).strip(), status, item.get('source_type',''), item.get('source_id',''), item.get('source_detail',''))
        )
        return int(cur.lastrowid)


def candidate_facts(store, status=None, limit=500):
    q='SELECT * FROM candidate_facts'; args=[]
    if status:
        q += ' WHERE status=?'; args.append(status)
    q += ' ORDER BY id DESC LIMIT ?'; args.append(limit)
    with store._connect() as con:
        return [dict(r) for r in con.execute(q,args)]


def update_candidate_fact(store, fact_id, status):
    if status not in {'USER_CONFIRMED','REVOKED','PROPOSED'}:
        raise ValueError('Invalid candidate fact status')
    with store._connect() as con:
        con.execute(
            "UPDATE candidate_facts SET status=?, updated_at=CURRENT_TIMESTAMP, confirmed_at=CASE WHEN ?='USER_CONFIRMED' THEN CURRENT_TIMESTAMP ELSE confirmed_at END, revoked_at=CASE WHEN ?='REVOKED' THEN CURRENT_TIMESTAMP ELSE revoked_at END WHERE id=?",
            (status, status, status, fact_id)
        )


def get_action(store, action_id):
    with store._connect() as c:
        row = c.execute('SELECT * FROM actions WHERE id=?', (action_id,)).fetchone()
        return dict(row) if row else None


def add_application_snapshot(store, item):
    with store._connect() as con:
        cur=con.execute(
            """INSERT INTO application_snapshots(application_id,stage,cv_id,cv_name,cv_variant,cv_path,cv_snapshot_json,cover_letter_text,form_fields_json,source_url)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (item['application_id'],item.get('stage','DRAFT'),item.get('cv_id',''),item.get('cv_name',''),item.get('cv_variant',''),item.get('cv_path',''),
             json.dumps(item.get('cv_snapshot',{}),ensure_ascii=False),item.get('cover_letter_text',''),json.dumps(item.get('form_fields',[]),ensure_ascii=False),item.get('source_url','')))
        return int(cur.lastrowid)


def application_snapshots(store, application_id, limit=100):
    with store._connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM application_snapshots WHERE application_id=? ORDER BY id DESC LIMIT ?', (application_id,limit))]


def add_application_event(store, application_id, event_type, status='RECORDED', details='', data=None):
    from operations.correlation import references
    data={**(data or {}),**references()}
    with store._connect() as con:
        cur=con.execute('INSERT INTO application_events(application_id,event_type,status,details,data_json) VALUES(?,?,?,?,?)',
                        (application_id,event_type,status,details,json.dumps(redact(data or {}),ensure_ascii=False)))
        return int(cur.lastrowid)


def application_events(store, application_id, limit=200):
    with store._connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM application_events WHERE application_id=? ORDER BY id DESC LIMIT ?', (application_id,limit))]


def application_detail(store, application_id):
    with store._connect() as con:
        row=con.execute("SELECT a.*,j.title,j.company,j.url AS job_url,j.platform,j.location,j.remote FROM applications a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.id=?",(application_id,)).fetchone()
        if not row: return None
        out=dict(row)
        out['snapshots']=[dict(r) for r in con.execute('SELECT * FROM application_snapshots WHERE application_id=? ORDER BY id DESC',(application_id,))]
        out['events']=[dict(r) for r in con.execute('SELECT * FROM application_events WHERE application_id=? ORDER BY id DESC',(application_id,))]
        return out


def _install_job_push_hook():
    # Store already uses this extension module as its versioned extension surface.
    # Hook notification creation once so future job workflows inherit push without
    # duplicating network-delivery calls throughout the agent.
    from database.store import Store
    original = Store.add_notification
    if getattr(original, '_job_pwa_push_hook', False):
        return

    def add_notification_with_job_push(self, title, body, severity='INFO', domain='system', related_page=''):
        result = original(self, title, body, severity, domain, related_page)
        if domain == 'jobs' and str(severity).upper() in {'ACTION_REQUIRED', 'URGENT'}:
            try:
                from notifications.web_push import send_job_push
                send_job_push(self, title, body, severity, related_page)
            except Exception:
                # A failed external delivery channel must never break the local
                # notification ledger or Job Agent execution.
                pass
        return result

    add_notification_with_job_push._job_pwa_push_hook = True
    Store.add_notification = add_notification_with_job_push


_install_job_push_hook()
