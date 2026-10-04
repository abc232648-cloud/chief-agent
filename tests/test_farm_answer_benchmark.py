"""Versioned local benchmark. Passing mocks is NOT live provider acceptance."""
import json
from pathlib import Path
import uuid
import logging
import pytest

from domains.farming import assistant, live_ai, financial_permissions, brief
from identity.service import IdentityService
from operations.diagnostics import configure, agent_trace
from operations.correlation import scope
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_live_ai import connected

DATA = json.loads((Path(__file__).parent/'fixtures/farm_answer_benchmark_v1.json').read_text())


@pytest.mark.parametrize('case', DATA['cases'], ids=lambda case: case['id'])
def test_builtin_benchmark_no_fabricated_authority_or_side_effects(dashboard, case):
    d=dashboard
    with d.store._connect() as con:
        before=[tuple(r) for r in con.execute('SELECT * FROM domain_records')]
    answer=assistant.ask(d.store,d.credentials['principal'],{'question':case['question']})
    for expected in case['contains']:assert expected in answer['answer']
    assert answer['action_authority']=='NONE' and answer['external_requests']==0
    assert answer['status']=='BUILT_IN_GUIDANCE' and not answer['live_ai']
    assert len(answer['trace_id'])==32
    with d.store._connect() as con:
        assert [tuple(r) for r in con.execute('SELECT * FROM domain_records')]==before


def test_finance_revocation_preserves_operational_guidance(dashboard):
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    service.create_user(owner,'benchmark-manager',PASSWORD,'Manager',('farming',))
    _,manager=service.login('benchmark-manager',PASSWORD)
    financial_permissions.configure(d.store,owner,{'event_id':str(uuid.uuid4()),'expected_revision':None,
        'human_id':manager.id,'permissions':{**financial_permissions.DEFAULTS,'access':False}})
    answer=assistant.ask(d.store,manager,{'question':'How do I report feed?'})
    assert 'kilograms' in answer['answer'] and 'bookkeeping_balances' not in answer['context']
    assert assistant.overview(d.store,manager)['status']=='LIVE_AI_PENDING'


def test_live_prompt_uses_schedule_context_without_invented_deadlines(connected):
    # A saved empty schedule is still not configured; the provider instruction
    # must rely on the actual context, not a fixed contradictory status claim.
    d=connected.d
    assistant.ask(d.store,d.credentials['principal'],{'question':'What time should I report?'})
    call=connected.calls[-1]
    assert 'Daily reporting deadlines are not configured.' not in call.system
    assert 'if deadlines are absent, do not invent them' in call.system
    assert json.loads(call.user)['authorized_context']['daily_report_schedule']=='NOT_CONFIGURED'
    assert 'do not prescribe drugs or doses' in call.system
    assert 'Do not claim to have saved records or performed actions' in call.system


def test_trace_reuses_correlation_and_does_not_store_payloads(dashboard,tmp_path):
    d=dashboard;logger,handler=configure(tmp_path,'dashboard')
    try:
        with scope(request_id='e'*32):
            result=assistant.ask(d.store,d.credentials['principal'],{'question':'How do I record feed? PRIVATE_QUESTION_MARKER'})
        assert result['trace_id']=='e'*32
        handler.flush();text=(tmp_path/'diagnostics/dashboard.log').read_text()
        assert 'FARM_GUIDANCE_BUILTIN correlation='+'e'*32 in text and 'elapsed_ms=' in text
        assert 'PRIVATE_QUESTION_MARKER' not in text and result['answer'] not in text
        logger.warning('FARM_GUIDANCE_LIVE correlation=%s elapsed_ms=1 model=private-key','e'*32)
        handler.flush();assert 'private-key' not in (tmp_path/'diagnostics/dashboard.log').read_text()
        with pytest.raises(ValueError):agent_trace('e'*32,'LIVE',1,'private-key')
    finally:
        logger.removeHandler(handler);handler.close()
