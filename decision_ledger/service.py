import json
from contextlib import nullcontext
from evidence.contracts import token, probability
from evidence.service import EvidenceService, canonical
from policy.contracts import ActionRisk
from operations.time_integrity import utc_now, utc_text
from database.evidence_migrations import schema_ready


class DecisionLedger:
    def __init__(self, store, domain, capability, *, reference_validator=None):
        self.evidence = EvidenceService(store, domain, capability)
        self.store, self.domain = store, domain
        self.reference_validator = reference_validator

    def append(self, *, event_key, correlation_id, action, phase, outcome, rationale,
               risk, policy_decision, evidence_ids=(), confidence=None, approval=None, references=(), policy_ref=None,
               connection=None):
        self.evidence.guard()
        for value in (event_key, correlation_id, action, outcome, rationale):
            token(value)
        if phase not in {'INTENT', 'POLICY', 'OUTCOME'}:
            raise ValueError('Unknown ledger phase.')
        if policy_decision not in {'ALLOW', 'ASK', 'BLOCK', 'NOT_EVALUATED'}:
            raise ValueError('Unknown policy decision.')
        if approval not in {None, 'PENDING', 'APPROVED', 'REJECTED'}:
            raise ValueError('Unknown approval state.')
        risk = ActionRisk(risk).value
        probability(confidence)
        if policy_ref is not None:
            token(policy_ref)
        for evidence_id in evidence_ids:
            self.evidence.get(evidence_id)
        for ref in references:
            if set(ref) != {'domain','kind','id'} or ref['domain'] != self.domain:
                raise PermissionError('Cross-domain or payload-bearing references are forbidden.')
            token(ref['kind']); token(ref['id'])
            if self.reference_validator is None or not self.reference_validator(ref):
                raise LookupError('Domain reference could not be validated.')
        from security.permissions import current_context
        record = dict(domain=self.domain, capability=self.evidence.capability, actor=token(current_context().agent_id),
                      action=action, phase=phase, outcome=outcome, rationale=rationale,
                      risk=risk, policy_decision=policy_decision, evidence_ids=list(evidence_ids), confidence=confidence,
                      approval=approval, references=list(references), policy_ref=policy_ref, contract_version='1.0.0')
        if connection is not None and not connection.in_transaction:
            raise ValueError('A caller-owned ledger transaction must already be active.')
        with (nullcontext(connection) if connection is not None else self.store._connect()) as con:
            if connection is None:con.execute('BEGIN IMMEDIATE')
            if not schema_ready(con):
                raise RuntimeError('Explicit D migration required.')
            old = con.execute('SELECT id,correlation_id,record FROM decision_ledger WHERE domain=? AND event_key=?', (self.domain, event_key)).fetchone()
            if old:
                previous = json.loads(old[2]); previous.pop('received_at')
                if previous != record or old[1] != correlation_id:
                    raise ValueError('Idempotency key reused with a different event.')
                return old[0]
            record['received_at'] = utc_text(utc_now())
            cur = con.execute('INSERT INTO decision_ledger(domain,event_key,correlation_id,record) VALUES(?,?,?,?)',
                              (self.domain, event_key, correlation_id, canonical(record)))
            return cur.lastrowid

    def events(self, correlation_id):
        self.evidence.guard()
        with self.store._connect() as con:
            return [dict(id=r[0], **json.loads(r[1])) for r in con.execute('SELECT id,record FROM decision_ledger WHERE domain=? AND correlation_id=? ORDER BY id', (self.domain, correlation_id))]
