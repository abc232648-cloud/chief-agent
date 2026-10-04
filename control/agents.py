import json
import time


SCHEMA = '''
CREATE TABLE IF NOT EXISTS agent_controls(domain TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,autostart INTEGER NOT NULL DEFAULT 1,running INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS agent_runs(id INTEGER PRIMARY KEY,domain TEXT NOT NULL,task TEXT NOT NULL,started REAL NOT NULL,finished REAL,outcome TEXT NOT NULL DEFAULT 'RUNNING');
CREATE TABLE IF NOT EXISTS control_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fact_history(id INTEGER PRIMARY KEY,fact_id INTEGER NOT NULL,text TEXT NOT NULL,status TEXT NOT NULL,changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
'''


class AgentControls:
    def __init__(self,store,registry,*,components=None):
        self.store=store;self.registry=registry
        self.components=components
        with store._connect() as con:
            if getattr(store,'operational',False):
                for domain in self.registry.domains:
                    if not con.execute('SELECT 1 FROM agent_controls WHERE domain=?',(domain,)).fetchone():raise ValueError('Operational domain controls require explicit preparation.')
                return
            con.executescript(SCHEMA)
            for domain in self.registry.domains:
                con.execute('INSERT OR IGNORE INTO agent_controls(domain) VALUES(?)',(domain,))

    def allowed(self,domain):
        with self.store._connect() as con:
            row=con.execute('SELECT enabled,running FROM agent_controls WHERE domain=?',(domain,)).fetchone()
            return bool(row and row['enabled'] and row['running'] and self._components_allowed(domain,con))

    def _components_allowed(self,domain,con):
        from database.component_state import ComponentState
        from capabilities.contracts import Mode
        _,agent=self.registry.resolve(domain)
        if self.components is not None:
            return self.components.allowed(agent.id,con=con)
        # Existing scheduler/reminder callers also honor persisted agent/capability
        # restrictions without importing the application composition into Core.
        rows=ComponentState(self.store).rows(con)
        relevant={('component',agent.id)}|{('capability',cap) for cap in agent.capabilities}
        return all(r['mode']==Mode.ENABLED.value for r in rows if (r['kind'],r['id']) in relevant)

    def component_allowed(self,agent_id,capability):
        agent=self.registry.agents.get(agent_id)
        if agent is None or capability not in agent.capabilities:return False
        with self.store._connect() as con:
            return self._components_allowed(agent.domain,con)

    def change(self,domain,payload):
        self.registry.resolve(domain)
        if not payload or set(payload)-{'enabled','autostart','running'} or any(type(v) is not bool for v in payload.values()):
            raise ValueError('Supply enabled, autostart or running as true/false.')
        with self.store._connect() as con:
            row=dict(con.execute('SELECT * FROM agent_controls WHERE domain=?',(domain,)).fetchone())
            row.update(payload)
            if not row['enabled']:row['running']=False
            con.execute('UPDATE agent_controls SET enabled=?,autostart=?,running=? WHERE domain=?',(row['enabled'],row['autostart'],row['running'],domain))
        from database.store_extensions import add_audit
        add_audit(self.store,'agents','Changed agent controls',actor='user',data={'agent':domain,**payload})
        return {'status':'UPDATED','message':'New work follows this setting. A running action finishes its current guarded operation.'}

    def startup(self):
        with self.store._connect() as con:
            con.execute('UPDATE agent_controls SET running=CASE WHEN enabled=1 AND autostart=1 THEN 1 ELSE 0 END')
            previous=con.execute("SELECT value FROM control_state WHERE key='heartbeat'").fetchone()
            stopped=float(previous[0]) if previous else 0
            con.execute("UPDATE agent_runs SET finished=MAX(started,?),outcome='INTERRUPTED' WHERE finished IS NULL",(stopped,))
            con.execute("INSERT OR REPLACE INTO control_state VALUES('worker_started',?)",(str(time.time()),))
        self.heartbeat()

    def heartbeat(self):
        with self.store._connect() as con:con.execute("INSERT OR REPLACE INTO control_state VALUES('heartbeat',?)",(str(time.time()),))

    def begin(self,domain,task):
        self.heartbeat()
        with self.store._connect() as con:
            return con.execute('INSERT INTO agent_runs(domain,task,started) VALUES(?,?,?)',(domain,task,time.time())).lastrowid

    def finish(self,run_id,outcome):
        with self.store._connect() as con:con.execute('UPDATE agent_runs SET finished=?,outcome=? WHERE id=?',(time.time(),str(outcome),run_id))
        self.heartbeat()

    def overview(self):
        now=time.time()
        with self.store._connect() as con:
            settings={r['domain']:dict(r) for r in con.execute('SELECT * FROM agent_controls')}
            state={r['key']:r['value'] for r in con.execute('SELECT * FROM control_state')}
            runs=[dict(r) for r in con.execute('SELECT * FROM agent_runs ORDER BY id DESC LIMIT 200')]
            totals={r['domain']:r['seconds'] for r in con.execute('SELECT domain,SUM(MAX(0,finished-started)) seconds FROM agent_runs WHERE finished IS NOT NULL GROUP BY domain')}
        heartbeat=float(state.get('heartbeat',0));healthy=now-heartbeat<30
        result=[]
        for definition in self.registry.describe():
            domain=definition['id'];row=settings[domain];history=[r for r in runs if r['domain']==domain];active=next((r for r in history if r['finished'] is None),None)
            status='DISABLED' if not row['enabled'] else 'PAUSED' if not row['running'] else 'WORKING' if active else 'IDLE'
            if row['enabled'] and row['running'] and not healthy:status='WORKER_NOT_CONNECTED'
            result.append({**definition,**row,'status':status,'last_activity':(history[0]['finished'] or history[0]['started']) if history else None,
                'current_task':active['task'] if active else None,'working_seconds':round((totals.get(domain) or 0)+(now-active['started'] if active and healthy else 0)),
                'recent_activity':history[:10]})
        return {'agents':result,'worker_connected':healthy,'heartbeat_at':heartbeat or None,'service_started_at':float(state.get('worker_started',0)) or None}

    def claim_command(self):
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            candidates=con.execute("SELECT c.*,COALESCE(d.domain,'jobs') AS control_domain FROM commands c LEFT JOIN domain_requests d ON d.command_id=c.id JOIN agent_controls a ON a.domain=COALESCE(d.domain,'jobs') WHERE c.status='QUEUED' AND a.enabled=1 AND a.running=1 ORDER BY c.id").fetchall()
            row=next((dict(r) for r in candidates if self._components_allowed(r['control_domain'],con)),None)
            if row:row.pop('control_domain')
            if row:con.execute("UPDATE commands SET status='PROCESSING' WHERE id=?",(row['id'],))
        return dict(row) if row else None
