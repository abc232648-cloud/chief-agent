"""Shared dispatch for trusted, registered domains; legacy commands remain jobs."""
import json
from database.store_extensions import add_audit
from domains.storage import DomainStorage, initialize
from policy.rules import FORBIDDEN_ACTIONS, HIGH_IMPACT_ACTIONS
from security.permissions import worker_context


class DomainRuntime:
    def __init__(self, store, gateway=None, *, registry, components=None):
        self.store, self.gateway = store, gateway
        self.registry = registry
        self.components = components
        self.component_allowed = components.allowed if components is not None else None
        self.processors = {}
        initialize(store)
        from control.agents import AgentControls
        self.controls=AgentControls(store,self.registry,components=components)
        self.component_allowed=self.controls.component_allowed

    def action(self, domain_id, action_name):
        domain, agent = self.registry.resolve(domain_id)
        if action_name in FORBIDDEN_ACTIONS or action_name in HIGH_IMPACT_ACTIONS:
            raise PermissionError('This local domain route cannot perform forbidden or approval-required actions.')
        action = next((a for a in domain.actions if a.name == action_name), None)
        if action is None:
            raise ValueError('Unknown action for this domain.')
        with worker_context(agent) as context:
            context.require(action.capability)
        return action, agent

    def queue(self, domain_id, action_name, payload):
        from control.agents import AgentControls
        self.controls=AgentControls(self.store,self.registry,components=self.components)
        action, _ = self.action(domain_id, action_name)
        if not isinstance(payload, dict) or set(payload) != {f['name'] for f in action.fields}:
            raise ValueError('Supply exactly the fields declared for this action.')
        encoded = json.dumps(payload, allow_nan=False)
        if len(encoded) > 8000:
            raise ValueError('Domain request is too large.')
        with self.store._connect() as con:
            cid = con.execute('INSERT INTO commands(instruction) VALUES(?)',
                              (f'{domain_id}: {action.label}',)).lastrowid
            con.execute('INSERT INTO domain_requests VALUES(?,?,?,?)', (cid, domain_id, action_name, encoded))
        return {'status': 'QUEUED', 'command_id': cid, 'message': 'Waiting for the shared worker.'}

    def _jobs(self):
        domain, agent = self.registry.resolve('jobs')
        if 'jobs' not in self.processors:
            self.processors['jobs'] = domain.processor_factory(self.store, self.gateway)
        return self.processors['jobs'], agent

    def process_approved_action(self, *args, **kwargs):
        if not self.controls.allowed('jobs'):
            if kwargs.get('claimed'):
                with self.store._connect() as con:con.execute("UPDATE actions SET status='APPROVED' WHERE id=? AND status='EXECUTING'",(args[0],))
            return {'status':'PAUSED'}
        processor, agent = self._jobs()
        run_id=self.controls.begin('jobs','Approved action #'+str(args[0]))
        try:
            with worker_context(agent, self.controls.allowed, self.component_allowed) as context:
                context.require('jobs.execute')
                result=processor.process_approved_action(*args, **kwargs)
            self.controls.finish(run_id,result.get('status','RESULT_NOT_RECORDED'))
            return result
        except Exception:
            self.controls.finish(run_id,'FAILED')
            with self.store._connect() as con:
                con.execute("UPDATE actions SET status='REVIEW' WHERE id=? AND status='EXECUTING'",(args[0],))
            raise

    def process_command(self, command_id, instruction):
        with self.store._connect() as con:
            row=con.execute('SELECT domain FROM domain_requests WHERE command_id=?',(command_id,)).fetchone()
        domain=row['domain'] if row else 'jobs'
        if not self.controls.allowed(domain):
            self.store.update_command(command_id,'QUEUED','Waiting for agent to resume.')
            return {'status':'PAUSED'}
        run_id=self.controls.begin(domain,instruction)
        try:
            result=self._process_command(command_id,instruction)
            with self.store._connect() as con:status=con.execute('SELECT status FROM commands WHERE id=?',(command_id,)).fetchone()[0]
            self.controls.finish(run_id,status)
            return result
        except Exception:
            self.controls.finish(run_id,'FAILED')
            raise

    def _process_command(self, command_id, instruction):
        with self.store._connect() as con:
            request = con.execute('SELECT * FROM domain_requests WHERE command_id=?', (command_id,)).fetchone()
        if request is None:
            processor, agent = self._jobs()
            with worker_context(agent, self.controls.allowed, self.component_allowed) as context:
                context.require('jobs.execute')
                return processor.process_command(command_id, instruction)
        self.store.set_worker('RUNNING', 'Processing '+request['domain']+' action')
        try:
            action, agent = self.action(request['domain'], request['action'])
            with worker_context(agent, self.controls.allowed, self.component_allowed) as context, self.store._connect() as con:
                con.execute('BEGIN IMMEDIATE')
                result = action.execute(DomainStorage(self.store, context, con), json.loads(request['payload_json']))
                if result.get('status') != 'COMPLETED':
                    raise ValueError('Domain action did not complete.')
                con.execute("UPDATE commands SET status='COMPLETED',result=?,processed_at=CURRENT_TIMESTAMP WHERE id=?",
                            (json.dumps(result), command_id))
        except Exception as exc:
            result = {'status': 'FAILED', 'message': str(exc)}
            self.store.update_command(command_id, 'FAILED', json.dumps(result))
        add_audit(self.store, 'domain', 'Processed domain action', status=result['status'],
                  data={'command_id': command_id, 'domain': request['domain'], 'action': request['action']})
        self.store.set_worker('IDLE', 'Domain action '+result['status'].lower())
        return result

    def overview(self, domain_id):
        _, agent = self.registry.resolve(domain_id)
        with worker_context(agent, component_allowed=self.component_allowed) as context:
            result = DomainStorage(self.store, context).overview() if domain_id+'.records.read' in context.capabilities else {'records': [], 'reminders': []}
        with self.store._connect() as con:
            result['commands'] = [dict(r) for r in con.execute('SELECT c.* FROM commands c JOIN domain_requests d ON d.command_id=c.id WHERE d.domain=? ORDER BY c.id DESC LIMIT 20', (domain_id,))]
        return result
