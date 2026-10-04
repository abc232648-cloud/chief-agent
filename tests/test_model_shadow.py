import json
import pytest
from tests.checkpoint_e_fixture import d_base,e_fixture
from tests.test_model_routing import registry,Fake
from model_registry.contracts import Assignment
from model_registry.routing import ModelRouter
from model_registry.shadow import evaluate_shadow,ShadowMetrics
from gateway.models import AIRequest,AIResponse
from gateway.errors import GatewayError
from database.execution_migrations import legacy_digest
from decision_ledger.service import DecisionLedger


def test_shadow_only_metrics_and_ledger_no_live_authority(e_fixture):
    f=e_fixture;reg=registry(f.store);a=Assignment('jobs','jobs-worker',('one',))
    reg.set_global_state('one','SHADOW',actor='operator')
    transport=Fake('synthetic','one')
    with pytest.raises(GatewayError):ModelRouter(reg,a,{'one':transport},capability='jobs.execute').generate(AIRequest('s','u'))
    assert not transport.calls
    with f.store._connect() as con:
        before=[tuple(r) for r in con.execute('SELECT * FROM candidate_facts')]
        actions=[tuple(r) for r in con.execute('SELECT * FROM actions')]
    result=evaluate_shadow(reg,a,'one','private source text','submit_application now',capability='jobs.execute',correlation='shadow-fixture')
    assert isinstance(result,ShadowMetrics) and not isinstance(result,AIResponse) and not hasattr(result,'text')
    assert not result.same_text
    with f.store._connect() as con:
        assert [tuple(r) for r in con.execute('SELECT * FROM candidate_facts')]==before
        assert [tuple(r) for r in con.execute('SELECT * FROM actions')]==actions
        record=con.execute('SELECT record FROM shadow_evaluations').fetchone()[0]
        assert 'private source text' not in record and 'submit_application now' not in record
    events=DecisionLedger(f.store,'jobs','jobs.execute').events('shadow-fixture')
    assert events[0]['policy_decision']=='NOT_EVALUATED' and events[0]['outcome']=='DIFFERENT'


def test_disabled_shadow_and_callback_rejected(e_fixture):
    reg=registry(e_fixture.store);a=Assignment('jobs','jobs-worker',('one',))
    reg.set_global_state('one','DISABLED',actor='operator')
    with pytest.raises(PermissionError):evaluate_shadow(reg,a,'one','a','b',capability='jobs.execute',correlation='x')
    reg.set_global_state('one','SHADOW',actor='operator')
    with pytest.raises(TypeError):evaluate_shadow(reg,a,'one','a',lambda:None,capability='jobs.execute',correlation='x')
