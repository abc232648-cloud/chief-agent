from dataclasses import asdict
import json
from types import MappingProxyType
import uuid
import hashlib
from evidence.service import EvidenceService,canonical
from evidence.contracts import token
from decision_ledger.service import DecisionLedger
from policy.contracts import ActionRisk,PolicyRequest
from operations.time_integrity import utc_now,utc_text
from security.permissions import current_context
from database.execution_migrations import schema_ready
from .contracts import Definition


class RunbookEngine:
    """Only domain-bound low-risk/local handlers execute in E. No auto recovery."""
    def __init__(self,store,domain,capability,catalog,policy,bindings,*,prerequisite=None,approval=None):
        self.store,self.domain,self.capability=store,token(domain),token(capability)
        self.catalog,self.policy=catalog,policy
        bindings=tuple(bindings)
        if len({b.action for b in bindings})!=len(bindings):raise ValueError('Duplicate domain binding.')
        self.bindings=MappingProxyType({b.action:b for b in bindings})
        self.prerequisite=prerequisite or (lambda name,run:False)
        self.approval=approval or (lambda ref,run,step,digest:False)
        self.evidence=EvidenceService(store,domain,capability)
        self.ledger=DecisionLedger(store,domain,capability,reference_validator=self._reference)

    def _guard(self):
        self.evidence.guard()

    def _reference(self,ref):
        if ref['kind']=='sop_approval':
            with self.store._connect() as con:return con.execute('SELECT 1 FROM sop_events WHERE domain=? AND id=? AND approval_ref IS NOT NULL',(self.domain,ref['id'])).fetchone() is not None
        if ref['kind']!='sop_run':return False
        with self.store._connect() as con:return con.execute('SELECT 1 FROM sop_runs WHERE domain=? AND id=?',(self.domain,ref['id'])).fetchone() is not None

    def register(self,definition):
        self._guard()
        if not isinstance(definition,Definition) or definition.domain!=self.domain:raise PermissionError('Definition domain mismatch.')
        for step in definition.steps:
            binding=self.bindings.get(step.action)
            if binding is None:raise ValueError('Unknown domain action binding.')
            capability=self.catalog.capabilities.get(binding.capability)
            if capability is None or capability.owner not in {self.domain,'chief'}:raise PermissionError('Action references another domain capability.')
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if not schema_ready(con):raise RuntimeError('Explicit E migration required.')
            old=con.execute('SELECT digest FROM sop_definitions WHERE domain=? AND id=? AND version=?',(self.domain,definition.id,definition.version)).fetchone()
            if old:
                if old[0]!=definition.digest:raise ValueError('Published version is immutable.')
                return definition.digest
            con.execute('INSERT INTO sop_definitions VALUES(?,?,?,?,?,?)',(self.domain,definition.id,definition.version,definition.digest,canonical(asdict(definition)),utc_text(utc_now())))
        return definition.digest

    def _definition(self,con,identity,version):
        row=con.execute('SELECT record,digest FROM sop_definitions WHERE domain=? AND id=? AND version=?',(self.domain,identity,version)).fetchone()
        if not row:raise LookupError('Runbook version unavailable in this domain.')
        definition=Definition.from_record(json.loads(row[0]))
        if definition.digest!=row[1]:raise ValueError('Definition integrity failure.')
        return definition

    def start(self,identity,version,*,evidence_ids=()):
        self._guard()
        for evidence_id in evidence_ids:self.evidence.get(evidence_id)
        run_id=uuid.uuid4().hex
        with self.store._connect() as con:
            definition=self._definition(con,identity,version)
            contracts={s.action:self.bindings[s.action].contract() for s in definition.steps}
            record={'evidence_ids':list(evidence_ids),'bindings':contracts,'created_at':utc_text(utc_now())}
            con.execute('INSERT INTO sop_runs VALUES(?,?,?,?,?,?,?,?,?)',(self.domain,run_id,identity,version,definition.digest,canonical(record),'READY',definition.start,0))
            self._event(con,run_id,definition.start,'READY','CREATED')
        return run_id

    def get(self,run_id):
        self._guard()
        with self.store._connect() as con:
            row=con.execute('SELECT * FROM sop_runs WHERE domain=? AND id=?',(self.domain,run_id)).fetchone()
            if row is None:raise LookupError('Run unavailable in this domain.')
            result=dict(row);result['record']=json.loads(result['record']);return result

    def _event(self,con,run_id,step,state,reason,approval_ref=None):
        con.execute('INSERT INTO sop_events(domain,run_id,step,state,reason,approval_ref,received_at) VALUES(?,?,?,?,?,?,?)',(self.domain,run_id,step,state,token(reason),approval_ref,utc_text(utc_now())))

    def _change(self,run,state,reason,*,step=None,approval_ref=None):
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            changed=con.execute('UPDATE sop_runs SET state=?,step=?,revision=revision+1 WHERE domain=? AND id=? AND revision=?',
                                (state,step if step is not None else run['step'],self.domain,run['id'],run['revision'])).rowcount
            if changed!=1:raise RuntimeError('Run changed concurrently; inspect current state.')
            self._event(con,run['id'],run['step'],state,reason,approval_ref)
        return self.get(run['id'])

    def _audit(self,run,step,binding,phase,outcome,decision,approval_ref=None):
        refs=[{'domain':self.domain,'kind':'sop_run','id':run['id']}]
        if approval_ref:
            with self.store._connect() as con:
                row=con.execute('SELECT id FROM sop_events WHERE domain=? AND run_id=? AND approval_ref=? ORDER BY id DESC LIMIT 1',(self.domain,run['id'],approval_ref)).fetchone()
                if row:refs.append({'domain':self.domain,'kind':'sop_approval','id':str(row[0])})
        # Approval references are domain-owned opaque codes, not new approval records.
        self.ledger.append(event_key=run['id']+':'+str(run['revision'])+':'+phase,correlation_id=run['id'],
            action=binding.action,phase=phase,outcome=outcome,rationale='SOP_'+step.id,risk=binding.risk,
            policy_decision=decision,approval='APPROVED' if approval_ref else ('PENDING' if decision=='ASK' else None),
            evidence_ids=run['record']['evidence_ids'],references=refs,
            policy_ref='policy-set:'+hashlib.sha256(canonical([(p.id,p.version) for p in self.policy.packs]).encode()).hexdigest())

    def advance(self,run_id,*,approval_ref=None):
        self._guard();run=self.get(run_id)
        if run['state'] not in {'READY','WAITING_APPROVAL'}:return run
        with self.store._connect() as con:definition=self._definition(con,run['definition_id'],run['version'])
        if definition.digest!=run['digest']:raise ValueError('Pinned run definition changed.')
        step=next(s for s in definition.steps if s.id==run['step'])
        binding=self.bindings.get(step.action)
        if binding is None or binding.contract()!=run['record']['bindings'][step.action]:return self._change(run,'REVIEW','BINDING_VERSION_CHANGED')
        capability=self.catalog.capabilities.get(binding.capability)
        if capability is None or capability.owner not in {self.domain,'chief'}:return self._change(run,'BLOCKED','CAPABILITY_UNAVAILABLE')
        try:current_context().require(binding.capability)
        except PermissionError:return self._change(run,'BLOCKED','CAPABILITY_DENIED')
        if binding.external or binding.risk not in {ActionRisk.READ,ActionRisk.RECORD,ActionRisk.ADVISE}:
            return self._change(run,'REVIEW','EXTERNAL_OR_HIGH_IMPACT_NOT_SUPPORTED')
        if not all(self.prerequisite(name,run_id) is True for name in step.prerequisites):return self._change(run,'BLOCKED','PREREQUISITE_FAILED')
        evidence_ids=run['record']['evidence_ids']
        qualities=[self.evidence.quality(e) for e in evidence_ids]
        if any(q.authority_restriction!='UNCHANGED' for q in qualities) or (step.evidence_required and (not qualities or any(q.sufficiency!='SUFFICIENT' for q in qualities))):
            return self._change(run,'REVIEW','EVIDENCE_INSUFFICIENT')
        result=self.policy.evaluate(PolicyRequest(binding.action,binding.risk))
        decision=result.decision.value
        if decision=='BLOCK':
            self._audit(run,step,binding,'POLICY','BLOCKED',decision)
            return self._change(run,'BLOCKED','POLICY_BLOCK')
        needs_approval=decision=='ASK' or step.approval_required
        approved=False
        if approval_ref is not None:
            token(approval_ref)
            approved=self.approval(approval_ref,run_id,step.id,definition.digest) is True
        if needs_approval and not approved:
            self._audit(run,step,binding,'POLICY','WAITING_APPROVAL','ASK')
            return self._change(run,'WAITING_APPROVAL','APPROVAL_REQUIRED')
        run=self._change(run,'RUNNING','CLAIMED',approval_ref=approval_ref if approved else None)
        try:
            self._audit(run,step,binding,'INTENT','NOT_ATTEMPTED',decision,approval_ref if approved else None)
        except Exception:
            return self._change(run,'REVIEW','LEDGER_INTENT_FAILED')
        try:
            # The handler receives references only, not authority from a definition.
            outcome=binding.handler(tuple(evidence_ids))
            token(outcome)
            branches=dict(step.branches)
            if outcome not in branches:raise ValueError('Undeclared outcome.')
        except Exception:
            self._audit(run,step,binding,'OUTCOME','FAILED',decision,approval_ref if approved else None)
            return self._change(run,'READY' if step.failure_next else 'FAILED','HANDLER_FAILED',step=step.failure_next)
        self._audit(run,step,binding,'OUTCOME',outcome,decision,approval_ref if approved else None)
        target=branches[outcome]
        return self._change(run,'READY' if target else 'COMPLETED','STEP_COMPLETED',step=target)

    def recover(self,run_id):
        """Explicit inspection marks uncertainty; never calls a handler or retries."""
        run=self.get(run_id)
        if run['state']=='RUNNING':return self._change(run,'REVIEW','INTERRUPTED_ACTION_AMBIGUOUS')
        return run
