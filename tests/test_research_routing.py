from pathlib import Path
import json

import pytest

from application.research_router import load_research_policy, route_research
from runtime_qualification.catalog import load_candidates
from runtime_qualification.contracts import Evidence, GateStatus
from runtime_qualification.evaluator import mandatory_gates
from runtime_qualification.policy import load_policy, plan_runtime

ROOT = Path(__file__).resolve().parents[1]


def runtime_inputs(*, qualify_research=False):
    candidates = load_candidates((ROOT / 'config/runtime_candidates.json').read_text(encoding='utf-8'))
    runtime_policy = load_policy((ROOT / 'config/runtime_policy.json').read_text(encoding='utf-8'), candidates)
    evidence = {}
    if qualify_research:
        research = next(item for item in candidates if item.id == runtime_policy.research_candidate_id)
        evidence[research.id] = tuple(
            Evidence(research.id, gate, GateStatus.PROVEN, 'fixture', 'synthetic-test', 'fixture', True)
            for gate in mandatory_gates(runtime_policy.research_role)
        )
    return runtime_policy, plan_runtime(candidates, evidence, runtime_policy)


def policy():
    return load_research_policy(ROOT / 'config/research_policy.json')


def test_internal_data_stays_inside_chief_and_never_starts_research_runtime():
    runtime_policy, plan = runtime_inputs(qualify_research=False)
    route = route_research('INTERNAL', plan, runtime_policy, policy(), caller='any-future-agent')
    assert route.status == 'READY'
    assert route.route == 'CHIEF_INTERNAL'
    assert route.runtime_candidate_id is None
    assert route.lifecycle == 'INTERNAL'
    assert route.search_provider is None
    assert route.action_authority == 'NONE'


def test_external_research_fails_closed_until_research_runtime_is_qualified():
    runtime_policy, plan = runtime_inputs(qualify_research=False)
    route = route_research('EXTERNAL', plan, runtime_policy, policy(), caller='job-like-caller')
    assert route.status == 'UNAVAILABLE'
    assert route.route == 'RESEARCH_RUNTIME'
    assert route.runtime_candidate_id is None
    assert route.lifecycle == 'ON_DEMAND'
    assert route.search_provider is None
    assert route.action_authority == 'NONE'


def test_external_research_automatically_routes_to_on_demand_no_authority_runtime_when_qualified():
    runtime_policy, plan = runtime_inputs(qualify_research=True)
    route = route_research('EXTERNAL', plan, runtime_policy, policy(), caller='future-agent-not-yet-invented')
    assert route.status == 'READY'
    assert route.route == 'RESEARCH_RUNTIME'
    assert route.runtime_candidate_id == runtime_policy.research_candidate_id
    assert route.lifecycle == 'ON_DEMAND'
    assert route.action_authority == 'NONE'
    assert route.search_provider == 'ddgs'


def test_caller_name_never_changes_external_research_authority_or_runtime_selection():
    runtime_policy, plan = runtime_inputs(qualify_research=True)
    routes = {
        route_research('EXTERNAL', plan, runtime_policy, policy(), caller=caller)
        for caller in ('existing-domain', 'future-domain', None)
    }
    assert len(routes) == 1


def test_initial_search_policy_enables_only_key_free_path_and_reserves_later_candidates():
    configured = policy()
    assert configured.interface == 'PROVIDER_NEUTRAL'
    assert configured.initial_search_provider == 'ddgs'
    assert configured.enabled_search_providers == ('ddgs',)
    assert configured.reserved_search_providers == ('brave', 'searxng', 'tavily')
    assert configured.automatic_provider_fallback is False
    assert configured.action_authority == 'NONE'


def test_research_policy_rejects_action_authority_fallback_overlap_and_unknown_scope():
    source = json.loads((ROOT / 'config/research_policy.json').read_text(encoding='utf-8'))
    with pytest.raises(ValueError, match='no action authority'):
        load_research_policy({**source, 'action_authority': 'CHIEF_GATED'})
    with pytest.raises(ValueError, match='fallback'):
        load_research_policy({**source, 'automatic_provider_fallback': True})
    with pytest.raises(ValueError, match='disjoint'):
        load_research_policy({**source, 'reserved_search_providers': ['ddgs', 'searxng']})
    runtime_policy, plan = runtime_inputs(qualify_research=True)
    with pytest.raises(ValueError, match='INTERNAL or EXTERNAL'):
        route_research('UNKNOWN', plan, runtime_policy, policy())


def test_research_policy_rejects_duplicate_json_keys():
    raw = (ROOT / 'config/research_policy.json').read_text(encoding='utf-8')
    raw = raw.replace(
        '"action_authority": "NONE"',
        '"action_authority": "NONE",\n  "action_authority": "CHIEF_GATED"',
        1,
    )
    with pytest.raises(ValueError, match='Duplicate research policy key'):
        load_research_policy(raw)
