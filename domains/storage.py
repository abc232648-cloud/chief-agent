import json
import time
from contextlib import nullcontext
from .record_access import allowed_kinds


SCHEMA='''
CREATE TABLE IF NOT EXISTS domain_requests(command_id INTEGER PRIMARY KEY, domain TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS domain_records(id INTEGER PRIMARY KEY, domain TEXT NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS domain_reminders(id INTEGER PRIMARY KEY, domain TEXT NOT NULL, title TEXT NOT NULL, due_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'SCHEDULED');
'''


def initialize(store):
    if getattr(store,'operational',False):return
    with store._connect() as con:con.executescript(SCHEMA)


class DomainStorage:
    """Namespace is supplied by the trusted runtime, never an action payload."""
    def __init__(self,store,context,connection=None):
        self._store=store;self.context=context;self.connection=connection

    def _connect(self):
        return nullcontext(self.connection) if self.connection is not None else self._store._connect()

    def record(self,kind,data):
        self.context.require(self.context.domain+'.records.write')
        allowed=allowed_kinds(self.context.domain)
        if allowed is not None and kind not in allowed:raise PermissionError('Record kind is outside this legacy agent storage grant.')
        with self._connect() as con:
            row=con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                            (self.context.domain,kind,json.dumps(data),time.time()))
            return row.lastrowid

    def remind(self,title,due_at):
        self.context.require(self.context.domain+'.reminders.write')
        with self._connect() as con:
            row=con.execute('INSERT INTO domain_reminders(domain,title,due_at) VALUES(?,?,?)',(self.context.domain,title,due_at))
            return row.lastrowid

    def overview(self):
        self.context.require(self.context.domain+'.records.read')
        with self._connect() as con:
            allowed=allowed_kinds(self.context.domain)
            clause=' AND kind IN ('+','.join('?' for _ in allowed)+')' if allowed is not None else ''
            params=(self.context.domain,*sorted(allowed)) if allowed is not None else (self.context.domain,)
            records=[dict(r) for r in con.execute('SELECT * FROM domain_records WHERE domain=?'+clause+' ORDER BY id DESC LIMIT 100',params)]
            reminders=[dict(r) for r in con.execute('SELECT * FROM domain_reminders WHERE domain=? ORDER BY id DESC LIMIT 100',(self.context.domain,))]
        for record in records:record['data']=json.loads(record.pop('data_json'))
        return {'records':records,'reminders':reminders}

    def cancel_reminder(self,reminder_id):
        self.context.require(self.context.domain+'.reminders.write')
        with self._connect() as con:
            changed=con.execute("UPDATE domain_reminders SET status='CANCELLED' WHERE id=? AND domain=? AND status='SCHEDULED'",(reminder_id,self.context.domain)).rowcount
        if not changed:raise ValueError('Scheduled reminder not found in this domain.')


def deliver_due_reminders(store, controls):
    """Atomically record each due reminder once in the existing dashboard inbox."""
    initialize(store)
    from control.notifications import initialize as initialize_notifications
    initialize_notifications(store)
    allowed=[d for d in controls.registry.domains if controls.allowed(d)]
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        rows=con.execute("SELECT * FROM domain_reminders WHERE status='SCHEDULED' AND due_at<=?",(time.time(),)).fetchall()
        for row in rows:
            if row['domain'] not in allowed:continue
            con.execute('INSERT INTO notifications(title,body,severity,domain,presented) VALUES(?,?,?,?,0)',
                        (row['domain'].title()+' reminder',row['title'],'INFO',row['domain']))
            con.execute("UPDATE domain_reminders SET status='DELIVERED' WHERE id=?",(row['id'],))
    return sum(row['domain'] in allowed for row in rows)
