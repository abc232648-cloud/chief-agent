from dataclasses import replace
import json
from pathlib import Path

import pytest

from agents.registry import AgentRegistry
from application.capability_catalog import main
from application.composition import compose_catalogs, default_catalogs, default_registry
from capabilities.contracts import Consumer, Node
from capabilities.registry import CapabilityRegistry
from capabilities.regression import regression_plan
from domains.contracts import AgentDefinition, DomainDefinition
from domains.farming import definition as farming
from domains.jobs import definition as jobs
from test_capability_registry import capability

ROOT = Path(__file__).resolve().parents[1]


def test_two_catalogs_have_separate_types_and_domain_payload_is_unchanged():
    catalogs = default_catalogs()
    expected = AgentRegistry()
    expected.register(*jobs())
    expected.register(*farming())
    assert type(catalogs.agents) is AgentRegistry
    assert type(catalogs.capabilities) is CapabilityRegistry
    assert not hasattr(catalogs.agents, 'graph')
    assert default_registry().describe() == expected.describe() == catalogs.agents.describe()
    assert catalogs.agents.agents == expected.agents
    expected_permissions = {p for a in expected.agents.values() for p in a.capabilities}
    assert {c.id for c in catalogs.capabilities.capabilities.values() if c.owner != 'chief'} == expected_permissions | {'farming.assistant.ask'}
    assert 'farming.assistant.ask' not in expected_permissions
    assert catalogs.capabilities.capabilities['farming.assistant.ask'].permissions == ()


def test_shared_changes_select_both_domains_and_keep_blocked_browser_tests_required():
    registry = default_catalogs().capabilities
    plan = regression_plan(registry, [Node('capability', 'chief.domain_storage')], candidate='fixture')
    assert plan.required_consumers == ('chief.dashboard', 'chief.scheduler', 'farming-recorder', 'jobs-worker')
    assert 'tests/test_access_browser.py' in plan.required_tests
    assert 'tests/test_domains_browser.py' in plan.required_tests
    assert 'tests/test_v20_fact_governance.py' in plan.required_tests
    assert 'tests/test_v23_final_submission.py' in plan.required_tests
    plan.validate_test_files(ROOT)
    for node in registry.graph.nodes:
        regression_plan(registry, [node], candidate='fixture').validate_test_files(ROOT)


def test_job_private_change_does_not_select_farm_consumers():
    registry = default_catalogs().capabilities
    plan = regression_plan(registry, [Node('component', 'jobs.worker')], candidate='fixture')
    assert plan.required_consumers == ('jobs-worker',)
    assert 'tests/test_domains_browser.py' not in plan.required_tests


def test_generic_composition_accepts_third_domain_without_builtin_domains():
    domain = DomainDefinition('sample', 'Sample', 'Fixture')
    agent = AgentDefinition('reader', 'sample', frozenset({'sample.read'}))
    catalogs = compose_catalogs([(domain, agent, (capability(),), ())])
    assert list(catalogs.agents.domains) == ['sample']
    assert list(catalogs.capabilities.capabilities) == ['sample.read']


def test_missing_extra_or_wrong_owner_domain_declaration_is_rejected():
    domain = DomainDefinition('sample', 'Sample', 'Fixture')
    agent = AgentDefinition('reader', 'sample', frozenset({'sample.read'}))
    for declarations in ((), (capability(), capability(id='sample.extra')), (capability(owner='chief'),)):
        with pytest.raises(ValueError, match='exactly describe'):
            compose_catalogs([(domain, agent, declarations, ())])


def test_read_only_catalog_and_regression_cli(capsys):
    assert main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload['capabilities']) == 16
    assert 'farming.assistant.ask' in {c['id'] for c in payload['capabilities']}
    assert {'chief.model_registry','chief.runbooks','chief.update_planning','chief.runtime_qualification'} <= {c['id'] for c in payload['capabilities']}
    assert {'chief.evidence','chief.data_quality','chief.decision_ledger'} <= {c['id'] for c in payload['capabilities']}
    assert main(['--changed', 'capability:chief.domain_dispatch', '--candidate', 'fixture']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['required_consumers'] == ['chief.dashboard', 'farming-recorder', 'jobs-worker']
    with pytest.raises(SystemExit) as exc:
        main(['--changed', 'capability:missing', '--candidate', 'fixture'])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        main(['--changed', 'capability:jobs.execute'])


def test_docker_payload_contains_every_top_level_application_import():
    docker = (ROOT/'gateway/Dockerfile').read_text()
    assert 'COPY application ./application' in docker
    assert 'COPY capabilities ./capabilities' in docker
    assert 'COPY agents ./agents' in docker
    assert 'COPY domains ./domains' in docker


def test_catalog_metadata_cannot_grant_cross_domain_execution():
    from security.permissions import worker_context
    catalogs = default_catalogs()
    _, farm_agent = catalogs.agents.resolve('farming')
    detached = catalogs.capabilities.describe()
    detached['capabilities'][0]['permissions'].append('jobs.execute')
    with worker_context(farm_agent) as context:
        with pytest.raises(PermissionError):
            context.require('jobs.execute')
