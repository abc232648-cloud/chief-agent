import json
from datetime import datetime,timezone

_PUSH_KEY='_job_web_push_subscriptions'
_MAX_PUSH_PER_PRINCIPAL=6
_MAX_PUSH_TOTAL=24


def initialize(store):
    if getattr(store,'operational',False):return
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        columns={r[1] for r in con.execute('PRAGMA table_info(notifications)')}
        for name,definition in {'domain':"TEXT NOT NULL DEFAULT 'system'",'presented':'INTEGER NOT NULL DEFAULT 1','related_page':"TEXT NOT NULL DEFAULT ''"}.items():
            if name not in columns:con.execute(f'ALTER TABLE notifications ADD COLUMN {name} {definition}')
        con.execute('CREATE TABLE IF NOT EXISTS notification_preferences(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL)')


def _data(con):
    row=con.execute('SELECT data FROM notification_preferences WHERE id=1').fetchone()
    data=json.loads(row[0]) if row else {'delivery':'all','sort':'newest'}
    if not isinstance(data,dict):raise ValueError('Invalid notification preference state.')
    data.setdefault('delivery','all');data.setdefault('sort','newest')
    return data


def _save(con,data):
    con.execute('INSERT OR REPLACE INTO notification_preferences VALUES(1,?)',(json.dumps(data,separators=(',',':')),))


def preferences(store,payload=None):
    initialize(store)
    with store._connect() as con:
        if payload is not None:con.execute('BEGIN IMMEDIATE')
        data=_data(con)
        public={'delivery':data.get('delivery','all'),'sort':data.get('sort','newest')}
        if payload is not None:
            if set(payload)-{'delivery','sort'} or payload.get('delivery',public['delivery']) not in ('all','critical','quiet') or payload.get('sort',public['sort']) not in ('newest','oldest'):
                raise ValueError('Invalid notification preference.')
            data.update(payload);_save(con,data)
            public={'delivery':data['delivery'],'sort':data['sort']}
    # Push capability material remains private. Only readiness and current identity's
    # registration count are exposed to the authenticated client.
    from notifications.web_push import public_configuration,subscription_status
    config=public_configuration()
    if config['configured']:
        from identity.context import current_human
        human=current_human()
        public['job_push']=subscription_status(store,human.id) if human is not None else {**config,'subscribed':False,'subscription_count':0}
    return public


def push_subscriptions(store,principal_id=None):
    """Internal push registry. Never expose endpoint/key material through preferences()."""
    initialize(store)
    with store._connect() as con:data=_data(con)
    rows=data.get(_PUSH_KEY,[])
    if not isinstance(rows,list) or not all(isinstance(row,dict) for row in rows):
        raise ValueError('Invalid web push registry state.')
    if principal_id is not None:rows=[row for row in rows if row.get('principal_id')==principal_id]
    return [dict(row) for row in rows]


def upsert_push_subscription(store,record):
    initialize(store)
    principal=record.get('principal_id');endpoint=record.get('endpoint')
    if not isinstance(principal,str) or not principal or not isinstance(endpoint,str) or not endpoint:
        raise ValueError('Push subscription identity is required.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');data=_data(con);rows=data.get(_PUSH_KEY,[])
        if not isinstance(rows,list) or not all(isinstance(row,dict) for row in rows):raise ValueError('Invalid web push registry state.')
        existing=next((row for row in rows if row.get('principal_id')==principal and row.get('endpoint')==endpoint),None)
        if existing:
            record={**record,'created_at':existing.get('created_at',record.get('created_at')),'cursor':max(int(existing.get('cursor',0)),int(record.get('cursor',0)))}
        else:
            count=sum(row.get('principal_id')==principal for row in rows)
            if count>=_MAX_PUSH_PER_PRINCIPAL:raise ValueError('Too many notification devices are registered for this account.')
        # A browser endpoint belongs to one current Chief identity. Account switching transfers it.
        rows=[row for row in rows if row.get('endpoint')!=endpoint]
        if len(rows)>=_MAX_PUSH_TOTAL:raise ValueError('Too many notification devices are registered.')
        rows.append(dict(record));data[_PUSH_KEY]=rows;_save(con,data)
        return sum(row.get('principal_id')==principal for row in rows)


def remove_push_subscription(store,principal_id,endpoint):
    initialize(store)
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');data=_data(con);rows=data.get(_PUSH_KEY,[])
        if not isinstance(rows,list):raise ValueError('Invalid web push registry state.')
        kept=[row for row in rows if not (row.get('principal_id')==principal_id and row.get('endpoint')==endpoint)]
        changed=len(kept)!=len(rows);data[_PUSH_KEY]=kept;_save(con,data)
        return changed


def remove_push_endpoint(store,endpoint):
    """Drop a provider-expired endpoint regardless of the identity that originally registered it."""
    initialize(store)
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');data=_data(con);rows=data.get(_PUSH_KEY,[])
        if not isinstance(rows,list):raise ValueError('Invalid web push registry state.')
        kept=[row for row in rows if row.get('endpoint')!=endpoint]
        changed=len(kept)!=len(rows);data[_PUSH_KEY]=kept;_save(con,data)
        return changed


def advance_push_cursor(store,principal_id,endpoint,notification_id):
    initialize(store)
    target=int(notification_id)
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');data=_data(con);rows=data.get(_PUSH_KEY,[])
        if not isinstance(rows,list):raise ValueError('Invalid web push registry state.')
        changed=False
        for row in rows:
            if row.get('principal_id')==principal_id and row.get('endpoint')==endpoint:
                row['cursor']=max(int(row.get('cursor',0)),target);changed=True;break
        if changed:data[_PUSH_KEY]=rows;_save(con,data)
        return changed


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
            if stamp.tzinfo:stamp.astimezone(timezone.utc).replace(tzinfo=None)
            sql+=' AND datetime(created_at)'+op+'datetime(?)';args.append(stamp.isoformat(sep=' '))
    sql+=' ORDER BY id '+('ASC' if query.get('sort')=='oldest' else 'DESC')+' LIMIT 200'
    with store._connect() as con:return [dict(r) for r in con.execute(sql,args)]


def update(store,target,payload):
    if target in {'push-subscription','push-unsubscribe'}:
        from identity.context import current_human
        human=current_human()
        if human is None:raise PermissionError('Authenticated identity is required for Web Push registration.')
        from notifications.web_push import register_subscription,unregister_subscription
        if target=='push-subscription':return register_subscription(store,human.id,payload)
        if not isinstance(payload,dict) or set(payload)!={'endpoint'}:raise ValueError('A Web Push endpoint is required.')
        return unregister_subscription(store,human.id,payload['endpoint'])
    initialize(store)
    field=payload.get('field','read')
    if field not in ('read','presented') or type(payload.get('value')) is not bool:raise ValueError('Invalid notification state.')
    with store._connect() as con:
        if target=='all':con.execute('UPDATE notifications SET '+field+'=?',(int(payload['value']),))
        else:
            changed=con.execute('UPDATE notifications SET '+field+'=? WHERE id=?',(int(payload['value']),int(target))).rowcount
            if not changed:raise ValueError('Notification does not exist.')
    return {'status':'UPDATED'}
