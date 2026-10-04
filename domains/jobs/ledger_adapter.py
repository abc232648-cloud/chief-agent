"""Reference-only observation of Job's existing execution/history boundaries."""
from contextvars import ContextVar
import json
import uuid
from decision_ledger.service import DecisionLedger
from domains.jobs.evidence_adapter import job_scope, JobEvidence
from domains.jobs.policy_adapter import action_risk
from worker.command_processor import CommandProcessor
from worker.reliable_submission import ReliableFinalSubmissionExecutor


class JobLedger:
    def __init__(self, store):
        self.store = store
        self.ledger = DecisionLedger(store, 'jobs', 'jobs.execute', reference_validator=self.valid_reference)
        self.session = ContextVar('job_ledger_session', default=None)
        self.last_error = None

    def valid_reference(self, ref):
        kind, identity = ref['kind'], ref['id']
        with self.store._connect() as con:
            if kind == 'command':
                if not con.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
                    return False
                if not con.execute("SELECT 1 FROM sqlite_master WHERE name='domain_requests'").fetchone():
                    return True
                row = con.execute('SELECT domain FROM domain_requests WHERE command_id=?', (identity,)).fetchone()
                return row is None or row[0] == 'jobs'
            if kind == 'approval':
                row = con.execute('SELECT payload_json FROM actions WHERE id=?', (identity,)).fetchone()
                if not row:
                    return False
                data = json.loads(row[0] or '{}')
                return 'command_id' in data and self.valid_reference({'kind':'command','id':str(data['command_id'])})
            if kind == 'audit':
                row = con.execute('SELECT data_json FROM audit_log WHERE id=?', (identity,)).fetchone()
                if not row:
                    return False
                data = json.loads(row[0] or '{}')
                return any(key in data and self.valid_reference({'kind':target,'id':str(data[key])}) for key,target in [('command_id','command'),('action_id','approval')])
            tables = {'application':'applications','snapshot':'application_snapshots','application_event':'application_events','candidate_fact':'candidate_facts'}
            return kind in tables and con.execute('SELECT 1 FROM '+tables[kind]+' WHERE id=?', (identity,)).fetchone() is not None

    def emit(self, *, action, phase, outcome, policy='NOT_EVALUATED', refs=(), approval=None, evidence_ids=()):
        session = self.session.get()
        if session is None:
            return
        try:
            with job_scope():
                if not self.ledger.evidence.ready():
                    return
                # Missing/ambiguous legacy ownership is omitted, never guessed.
                refs = [r for r in [*session['refs'], *refs] if self.valid_reference(r)]
                self.ledger.append(event_key=uuid.uuid4().hex, correlation_id=session['correlation'],
                    action=action, phase=phase, outcome=outcome, rationale='JOB_LEGACY_OBSERVATION',
                    risk=action_risk(action), policy_decision=policy, approval=approval,
                    references=refs, evidence_ids=evidence_ids, policy_ref='jobs.legacy:1.0.0' if policy!='NOT_EVALUATED' else None)
        except Exception as exc:
            self.last_error = type(exc).__name__
            # Never throw after an external effect or turn observation failure into replay.
            try:
                from database.store_extensions import add_audit
                add_audit(self.store, 'decision_ledger', 'Ledger observation failed', status='ERROR', data={'error_type':self.last_error})
            except Exception:
                pass

    def observe_policy(self, request, decision):
        self.emit(action=request.action, phase='POLICY', outcome='EVALUATED', policy=decision.decision.value)

    def correlations(self, result):
        refs, evidence_ids = [], set()
        def add(kind, identity):
            ref = {'domain':'jobs','kind':kind,'id':str(identity)}
            if self.valid_reference(ref) and ref not in refs:
                refs.append(ref)
        def visit(value):
            if isinstance(value, dict):
                if value.get('application_id'):
                    add('application', value['application_id'])
                if value.get('fact_id'):
                    add('candidate_fact', value['fact_id'])
                    evidence_ids.update(JobEvidence(self.store).references(value['fact_id']))
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)
        visit(result)
        for identity in result.get('approvals', []):
            add('approval', identity)
        session = self.session.get()
        with self.store._connect() as con:
            for row in con.execute('SELECT id,data_json FROM audit_log WHERE id>?', (session['audit_start'],)):
                data = json.loads(row[1] or '{}')
                if any(str(data.get('command_id' if r['kind']=='command' else 'action_id')) == r['id'] for r in session['refs']):
                    add('audit', row[0])
            for ref in list(refs):
                if ref['kind'] == 'application':
                    app=con.execute('SELECT draft_json FROM applications WHERE id=?',(ref['id'],)).fetchone()
                    if app:
                        visit(json.loads(app[0] or '{}'))
                    for snapshot in con.execute('SELECT form_fields_json FROM application_snapshots WHERE application_id=?',(ref['id'],)):
                        visit(json.loads(snapshot[0] or '[]'))
                    for kind, table in [('snapshot','application_snapshots'),('application_event','application_events')]:
                        for row in con.execute('SELECT id FROM '+table+' WHERE application_id=?', (ref['id'],)):
                            add(kind, row[0])
        return refs, tuple(sorted(evidence_ids))


class JobCommandProcessor(CommandProcessor):
    def __init__(self, store, gateway, **kwargs):
        self.ledger_observer = JobLedger(store)
        super().__init__(store, gateway, candidate_fact_provider=JobEvidence(store).confirmed, **kwargs)
        self.gate.observer = self.ledger_observer.observe_policy

    def _observed(self, kind, identity, invoke):
        observer = self.ledger_observer
        with job_scope():
            with self.store._connect() as con:
                audit_start = con.execute('SELECT COALESCE(MAX(id),0) FROM audit_log').fetchone()[0]
            ref = {'domain':'jobs','kind':kind,'id':str(identity)}
            if kind == 'command' and not observer.valid_reference(ref):
                raise PermissionError('Command is not owned by Job.')
            session = dict(correlation=uuid.uuid4().hex, refs=[ref], audit_start=audit_start)
            marker = observer.session.set(session)
            observer.last_correlation = session['correlation']
            try:
                approval = None
                action = 'process_'+kind
                if kind == 'approval':
                    row = self.store.get_action(identity)
                    if row:
                        action = row['action']
                        payload = json.loads(row.get('payload_json') or '{}')
                        if 'command_id' in payload and not observer.valid_reference({'kind':'command','id':str(payload['command_id'])}):
                            raise PermissionError('Approval belongs to another domain.')
                    if row and row.get('status') in {'APPROVED','EXECUTING'}:
                        approval = 'APPROVED'
                observer.emit(action=action, phase='INTENT', outcome='NOT_ATTEMPTED', approval=approval)
                from operations.correlation import scope
                with scope(ledger_correlation_id=session['correlation'], **{'command_id' if kind=='command' else 'action_id':int(identity)}):
                    result = invoke()
                try:
                    refs, evidence_ids = observer.correlations(result)
                    if kind == 'command':
                        with self.store._connect() as con:
                            outcome=con.execute('SELECT status FROM commands WHERE id=?',(identity,)).fetchone()[0]
                    else:
                        outcome=str(result.get('status','UNKNOWN'))
                    observer.emit(action=action, phase='OUTCOME', outcome=outcome, refs=refs,
                                  evidence_ids=evidence_ids, approval='PENDING' if result.get('approvals') else approval)
                except Exception as exc:
                    observer.last_error = type(exc).__name__
                return result
            except Exception:
                observer.emit(action='process_'+kind, phase='OUTCOME', outcome='ERROR')
                raise
            finally:
                observer.session.reset(marker)

    def process_command(self, command_id, instruction):
        return self._observed('command', command_id, lambda: super(JobCommandProcessor,self).process_command(command_id,instruction))

    def process_approved_action(self, action_id, *, claimed=False):
        return self._observed('approval', action_id, lambda: super(JobCommandProcessor,self).process_approved_action(action_id,claimed=claimed))

    def _execute_low_risk(self, action, payload):
        self.ledger_observer.emit(action=action,phase='INTENT',outcome='NOT_ATTEMPTED')
        try:
            result=super()._execute_low_risk(action,payload)
            self.ledger_observer.emit(action=action,phase='OUTCOME',outcome=str(result.get('status','UNKNOWN')))
            return result
        except Exception:
            self.ledger_observer.emit(action=action,phase='OUTCOME',outcome='ERROR')
            raise


class JobSubmissionExecutor(ReliableFinalSubmissionExecutor):
    """The existing explicit dashboard retry uses the same evidence boundary."""
    def __init__(self, store, **kwargs):
        from .policy_adapter import JobComparisonGate
        gate = JobComparisonGate(store)
        self.observer = JobLedger(store)
        gate.observer = self.observer.observe_policy
        super().__init__(store, policy_gate=gate, candidate_fact_provider=JobEvidence(store).confirmed, **kwargs)

    def submit(self, application_id, url, submit_selector, *, approved=False, expected_form_hash=None):
        with job_scope():
            marker=self.observer.session.set({'correlation':uuid.uuid4().hex,'refs':[{'domain':'jobs','kind':'application','id':str(application_id)}]})
            try:
                self.observer.emit(action='submit_application',phase='INTENT',outcome='NOT_ATTEMPTED',approval='APPROVED' if approved else None)
                result=super().submit(application_id,url,submit_selector,approved=approved,expected_form_hash=expected_form_hash)
                self.observer.emit(action='submit_application',phase='OUTCOME',outcome=str(result.get('status','UNKNOWN')))
                return result
            except Exception:
                self.observer.emit(action='submit_application',phase='OUTCOME',outcome='ERROR')
                raise
            finally:
                self.observer.session.reset(marker)
