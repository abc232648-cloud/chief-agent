"""Cross-domain reminder schedules; atomic local delivery, no external actions."""
import json
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PREFIX='schedule.v1.'


def list_schedules(store):
    with store._connect() as con:
        rows=con.execute('SELECT value FROM control_state WHERE key LIKE ? ORDER BY key LIMIT 501',(PREFIX+'%',)).fetchall()
    if len(rows)>500:raise ValueError('Schedule capacity exceeded; review required.')
    return [json.loads(row[0]) for row in rows]


def create(store,registry,actor,body,*,now=None):
    now=time.time() if now is None else now
    if set(body)!={'domain','action','title','first_run','timezone','interval_minutes'}:
        raise ValueError('Supply the supported schedule fields.')
    if not isinstance(body['domain'],str) or body['domain'] not in registry.domains or body['action']!='in_app_reminder':
        raise ValueError('This action is not supported for scheduling. Camera and external workflow actions are unavailable.')
    title=body['title']
    if not isinstance(title,str) or not title.strip() or len(title)>240:raise ValueError('Reminder needs 1–240 characters.')
    interval=body['interval_minutes']
    if type(interval) is not int or not (interval==0 or 5<=interval<=525600):raise ValueError('Use 0 for once or 5–525600 minutes between runs.')
    try:
        zone=ZoneInfo(body['timezone']);due=datetime.fromisoformat(body['first_run'].replace('Z','+00:00'))
        if due.tzinfo is None:raise ValueError()
        stamp=due.timestamp()
        if not now<stamp<=now+366*86400:raise ValueError()
    except (ValueError,TypeError,AttributeError,ZoneInfoNotFoundError):raise ValueError('Choose a timezone and an explicit future time within the next year.') from None
    identity=uuid.uuid4().hex
    row={'id':identity,'domain':body['domain'],'action':body['action'],'title':title.strip(),
         'timezone':str(zone),'next_due':stamp,'interval_minutes':interval,'enabled':True,
         'status':'SCHEDULED','actor':actor,'revision':1,'last_run':None,
         'timing':'Elapsed-minute interval; overdue runs are coalesced into one reminder.'}
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        count=con.execute('SELECT count(*) FROM control_state WHERE key LIKE ?',(PREFIX+'%',)).fetchone()[0]
        if count>=500:raise ValueError('Maximum 500 schedules.')
        con.execute('INSERT INTO control_state(key,value) VALUES(?,?)',(PREFIX+identity,json.dumps(row)))
    return row


def change(store,identity,body):
    if set(body)!={'enabled','revision'} or type(body['enabled']) is not bool or type(body['revision']) is not int:
        raise ValueError('Supply enabled and the displayed revision.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');found=con.execute('SELECT value FROM control_state WHERE key=?',(PREFIX+identity,)).fetchone()
        if not found:raise ValueError('Schedule not found.')
        row=json.loads(found[0])
        if row['revision']!=body['revision']:raise ValueError('Schedule changed; refresh before editing.')
        if row['status'] in {'COMPLETED','AUTHORITY_REVOKED','UNSUPPORTED'}:
            raise ValueError('Create a new schedule; this one is closed.')
        row.update(enabled=body['enabled'],status='SCHEDULED' if body['enabled'] else 'PAUSED',revision=row['revision']+1)
        con.execute('UPDATE control_state SET value=? WHERE key=?',(json.dumps(row),PREFIX+identity))
    return row


def deliver(store,controls,*,now=None):
    now=time.time() if now is None else now
    from control.notifications import initialize
    initialize(store)  # Existing isolated-development preparation; no operational DDL.
    allowed={domain for domain in controls.registry.domains if controls.allowed(domain)}
    count=0
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        for entry in con.execute('SELECT key,value FROM control_state WHERE key LIKE ?',(PREFIX+'%',)).fetchall():
            row=json.loads(entry['value'])
            if not row['enabled'] or row['next_due']>now or row['domain'] not in allowed:continue
            actor=con.execute('SELECT enabled,role,domains FROM human_identities WHERE id=?',(row['actor'],)).fetchone()
            if not actor or not actor['enabled'] or actor['role'] not in {'Owner','Administrator'} or '*' not in json.loads(actor['domains']):
                row.update(enabled=False,status='AUTHORITY_REVOKED')
            elif row['action']=='in_app_reminder':
                con.execute('INSERT INTO notifications(title,body,severity,domain,presented) VALUES(?,?,?,?,0)',('Scheduled reminder',row['title'],'INFO',row['domain']))
                row['last_run']=now;count+=1
                if row['interval_minutes']:
                    # Preserve the original cadence but skip missed slots.
                    step=row['interval_minutes']*60
                    row['next_due']+= (int((now-row['next_due'])//step)+1)*step
                else:row.update(enabled=False,status='COMPLETED')
            else:row.update(enabled=False,status='UNSUPPORTED')
            row['revision']+=1
            con.execute('UPDATE control_state SET value=? WHERE key=?',(json.dumps(row),entry['key']))
    return count
