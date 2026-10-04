"""Job-owned vocabulary; inherited PolicyGate.execute remains enforcement."""
from dataclasses import asdict
from policy.rules import FORBIDDEN_ACTIONS, LOW_RISK_ACTIONS, HIGH_IMPACT_ACTIONS
from policy.contracts import ActionRisk as Risk, Outcome, PolicyPack, PolicyRule, Precedence, PolicyRequest
from policy.engine import PolicyEngine
from policy.compatibility import PolicyComparison
from worker.action_gate import PolicyGate
from security.permissions import current_context


def job_pack():
    rules = [PolicyRule(a, Outcome.BLOCK, 'Forbidden action', True) for a in sorted(FORBIDDEN_ACTIONS)]
    rules += [PolicyRule(a, Outcome.ALLOW, 'Low-risk action') for a in sorted(LOW_RISK_ACTIONS)]
    rules += [PolicyRule(a, Outcome.ASK, 'High-impact action requires user approval') for a in sorted(HIGH_IMPACT_ACTIONS)]
    return PolicyPack('jobs.legacy', '1.0.0', Precedence.CHIEF, tuple(rules))


def action_risk(action):
    if action in {'pay_money', 'financial_commitment'}:
        return Risk.PHYSICAL_FINANCIAL
    if action in HIGH_IMPACT_ACTIONS or action in FORBIDDEN_ACTIONS:
        return Risk.HIGH_IMPACT
    if action in {'read_job_listing', 'discover_jobs', 'discover_sources', 'verify_source'}:
        return Risk.READ
    if action in {'save_application_draft', 'log_application'}:
        return Risk.RECORD
    if action in {'draft_cv', 'draft_cover_letter', 'match_candidate', 'screen_scam'}:
        return Risk.ADVISE
    return Risk.LOW_RISK_AUTOMATION if action in LOW_RISK_ACTIONS else Risk.HIGH_IMPACT


class JobComparisonGate(PolicyGate):
    def __init__(self, store=None, *, engine=None, sink=None):
        self.engine = engine if engine is not None else PolicyEngine((job_pack(),))
        self.store, self.sink, self.last_comparison = store, sink, None
        self.audit_error = None
        self.observer = None
        self.observer_error = None

    def decide(self, request):
        # Exact legacy decision/context checks, and inherited execute/approval behavior.
        effective = super().decide(request)
        try:
            candidate = self.engine.evaluate(PolicyRequest(request.action, action_risk(request.action)))
            decision = candidate.decision.value
            context = current_context()
            if context is not None:
                try:
                    context.require('jobs.execute')
                except PermissionError:
                    decision = 'BLOCK'
                if context.domain != 'jobs' or 'jobs.execute' not in context.capabilities:
                    decision = 'BLOCK'
            comparison = PolicyComparison(request.action, effective.decision.value, decision,
                                          'MATCH' if effective.decision.value == decision else 'MISMATCH',
                                          candidate.matched_packs)
        except Exception as exc:
            comparison = PolicyComparison(request.action, effective.decision.value, None, 'COMPARISON_ERROR', (), type(exc).__name__)
        self.last_comparison = comparison
        self.audit_error = None
        try:
            if self.sink is not None:
                self.sink(comparison)
            elif self.store is not None:
                import json
                from security_hardening import redact
                with self.store._connect() as con:
                    con.execute('INSERT INTO audit_log(event_time,category,action,status,data_json) VALUES(?,?,?,?,?)',
                                (comparison.received_at,'policy_comparison','Compared Job policy decision',
                                 comparison.status,json.dumps(redact(asdict(comparison)))))
        except Exception as exc:
            # Audit failure is observable in-memory; never changes the legacy outcome.
            self.audit_error = type(exc).__name__
        if self.observer is not None:
            try:
                self.observer(request, effective)
            except Exception as exc:
                self.observer_error = type(exc).__name__
        return effective
