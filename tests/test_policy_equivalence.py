from contextlib import nullcontext
import pytest
from domains.jobs.policy_adapter import JobComparisonGate,job_pack
from policy.rules import FORBIDDEN_ACTIONS,LOW_RISK_ACTIONS,HIGH_IMPACT_ACTIONS
from policy.contracts import PolicyPack,PolicyRule,Precedence,Outcome
from policy.engine import PolicyEngine
from domains.contracts import AgentDefinition
from security.permissions import worker_context
from worker.action_gate import PolicyGate,ActionRequest

ACTIONS=sorted(FORBIDDEN_ACTIONS|LOW_RISK_ACTIONS|HIGH_IMPACT_ACTIONS|{'synthetic_unknown'})


def context(kind):
    if kind=='none':return nullcontext()
    domain='farming' if kind=='wrong_domain' else 'jobs'
    grants=frozenset() if kind=='missing_grant' else frozenset({'jobs.execute'})
    return worker_context(AgentDefinition('synthetic',domain,grants),lambda domain:kind!='paused')


@pytest.mark.parametrize('action',ACTIONS)
@pytest.mark.parametrize('kind',['none','valid','paused','missing_grant','wrong_domain'])
@pytest.mark.parametrize('approved',[False,True])
def test_complete_job_equivalence(action,kind,approved):
    gate=JobComparisonGate();legacy=PolicyGate();request=ActionRequest(action,{'synthetic':True})
    def execute(gate):
        calls=[]
        try:
            result=gate.execute(request,lambda payload:calls.append(payload) or 'executed',approved=approved)
            return ('result',result,len(calls))
        except Exception as exc:
            return ('exception',type(exc).__name__,len(calls))
    with context(kind):
        assert gate.decide(request)==legacy.decide(request)
        assert gate.last_comparison.status=='MATCH'
        assert execute(gate)==execute(legacy)
        assert gate.last_comparison.status=='MATCH'
    assert JobComparisonGate.execute is PolicyGate.execute


def test_deliberate_mismatch_cannot_change_actual_enforcement():
    bad=PolicyPack('bad','1.0.0',Precedence.LAW_REGULATION,(PolicyRule('pay_money',Outcome.ALLOW,'synthetic mismatch'),))
    gate=JobComparisonGate(engine=PolicyEngine((bad,)))
    called=[]
    with pytest.raises(Exception):gate.execute(ActionRequest('pay_money',{}),lambda p:called.append(p),approved=True)
    assert gate.last_comparison.status=='MISMATCH' and gate.last_comparison.legacy=='BLOCK'
    assert not called


def test_comparison_and_audit_errors_never_replace_legacy_result():
    class Broken:
        def evaluate(self,request):raise RuntimeError('synthetic comparison failure')
    def sink(record):raise OSError('synthetic audit failure')
    gate=JobComparisonGate(engine=Broken(),sink=sink)
    called=[]
    assert gate.execute(ActionRequest('read_job_listing',{}),lambda p:called.append(p) or 7)==7
    assert len(called)==1 and gate.last_comparison.status=='COMPARISON_ERROR'
    assert gate.audit_error=='OSError'


def test_job_pack_contains_every_legacy_action_once():
    assert {r.action for r in job_pack().rules}==FORBIDDEN_ACTIONS|LOW_RISK_ACTIONS|HIGH_IMPACT_ACTIONS
