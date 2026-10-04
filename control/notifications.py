import json
from datetime import datetime,timezone


def initialize(store):
    if getattr(store,'operational',False):return
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        columns={r[1] for r in con.execute('PRAGMA table_info(notifications)')}
        for name,definition in {'domain':"TEXT NOT NULL DEFAULT 'system'",'presented':'INTEGER NOT NULL DEFAULT 1','related_page':"TEXT NOT NULL DEFAULT ''"}.items():
            if name not in columns:con.execute(f'ALTER TABLE notifications ADD COLUMN {name} {definition}')
        con.execute('CREATE TABLE IF NOT EXISTS notification_preferences(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL)')


def preferences(store,payload=None):
    initialize(store)
    with store._connect() as con:
        row=con.execute('SELECT data FROM notification_preferences WHERE id=1').fetchone()
        data=json.loads(row[0]) if row else {'delivery':'all','sort':'newest'}
        if payload is not None:
            if set(payload)-{'delivery','sort'} or payload.get('delivery',data['delivery']) not in ('all','critical','quiet') or payload.get('sort',data['sort']) not in ('newest','oldest'):
                raise ValueError('Invalid notification preference.')
            data.update(payload)
            con.execute('INSERT OR REPLACE INTO notification_preferences VALUES(1,?)',(json.dumps(data),))
    return data


def list_notifications(store,query):
    initialize(store)
    sql='SELECT * FROM notifications WHERE 1=1';args=[]
    for key,column in [('agent','domain'),('severity','severity')]:
        if query.get(key):sql+=' AND '+column+'=?';args.append(query[key])
    if query.get('read') in ('0','1'):sql+=' AND read=?';args.append(int(query['read']))
    if query.get('q'):sql+=' AND (title LIKE ? OR body LIKE ?)';args.extend(['%'+query['q']+'%']*2)
    for key,op in [('start','>='),('end','<=')]:
        if query.get(key):
            stamp=datetime.fromisoformat(query[key])
            if stamp.tzinfo:stamp=stamp.astimezone(timezone.utc).replace(tzinfo=None)
            sql+=' AND datetime(created_at)'+op+'datetime(?)';args.append(stamp.isoformat(sep=' '))
    sql+=' ORDER BY id '+('ASC' if query.get('sort')=='oldest' else 'DESC')+' LIMIT 200'
    with store._connect() as con:return [dict(r) for r in con.execute(sql,args)]


def update(store,target,payload):
    initialize(store)
    field=payload.get('field','read')
    if field not in ('read','presented') or type(payload.get('value')) is not bool:raise ValueError('Invalid notification state.')
    with store._connect() as con:
        if target=='all':con.execute('UPDATE notifications SET '+field+'=?',(int(payload['value']),))
        else:
            changed=con.execute('UPDATE notifications SET '+field+'=? WHERE id=?',(int(payload['value']),int(target))).rowcount
            if not changed:raise ValueError('Notification does not exist.')
    return {'status':'UPDATED'}
