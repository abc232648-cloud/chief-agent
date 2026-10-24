"""Tests for execution-runtime integration, process startup, task dispatch, restricted tools, and clean cancellation."""
import pytest
from pathlib import Path

from runtime_qualification.catalog import load_candidates
from runtime_qualification.policy import load_policy
from runtime_qualification.integration import RuntimeIntegrationEngine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def engine():
    candidates = load_candidates((ROOT / 'config/runtime_candidates.json').read_text(encoding='utf-8'))
    policy = load_policy((ROOT / 'config/runtime_policy.json').read_text(encoding='utf-8'), candidates)
    return RuntimeIntegrationEngine(candidates, policy)


def test_runtime_truthful_unqualified_status(engine):
    st = engine.status()
    for cid, info in st['runtimes'].items():
        assert info['qualification_status'] == 'UNQUALIFIED'
    assert st['execution_candidate'] == 'openclaw-2026.10.1'
    assert st['research_candidate'] == 'hermes-agent-0.21.3'


def test_runtime_service_startup(engine):
    res_openclaw = engine.startup('openclaw-2026.10.1')
    assert res_openclaw['status'] == 'RUNNING'
    assert res_openclaw['role'] == 'GENERAL_EXECUTION'
    assert res_openclaw['lifecycle'] == 'SERVICE'

    res_hermes = engine.startup('hermes-agent-0.21.3')
    assert res_hermes['status'] == 'ON_DEMAND_READY'
    assert res_hermes['role'] == 'RESEARCH_ONLY'
    assert res_hermes['lifecycle'] == 'ON_DEMAND'


def test_research_task_dispatch_and_tool_restriction(engine):
    # Research query succeeds on Hermes on-demand
    res = engine.dispatch({
        'task_id': 'task-research-01',
        'type': 'RESEARCH',
        'query': 'Survey optimal brooding temperatures for broiler day-old chicks.'
    })
    assert res.status == 'COMPLETED'
    assert res.role == 'RESEARCH_ONLY'
    assert 'Hermes Agent' in res.output

    # Hard Gate: Any action or tool request to research runtime must be strictly blocked
    with pytest.raises(PermissionError, match='zero action authority'):
        engine.dispatch({
            'task_id': 'task-research-action-fail',
            'type': 'RESEARCH',
            'tools': ['query_weather'],
            'query': 'Check weather and actuate curtains'
        })


def test_execution_task_dispatch_gated_tools_and_cancellation(engine):
    # Execution task with approved gated tool succeeds
    res = engine.dispatch({
        'task_id': 'task-exec-01',
        'type': 'EXECUTION',
        'command': 'Collect daily pen sensor metrics',
        'tools': ['read_pen_sensor_log', 'read_water_flow_meter']
    })
    assert res.status == 'COMPLETED'
    assert res.role == 'GENERAL_EXECUTION'
    assert 'read_pen_sensor_log' in res.tools_invoked

    # Unauthorized tool is rejected
    with pytest.raises(PermissionError, match='not approved under CHIEF_GATED authority'):
        engine.dispatch({
            'task_id': 'task-exec-bad-tool',
            'type': 'EXECUTION',
            'command': 'Reconfigure main firewall',
            'tools': ['modify_iptables_firewall']
        })

    # Cancellation cleans up task
    cancel_res = engine.cancel('non-existent-task')
    assert cancel_res['status'] == 'NOT_FOUND'
