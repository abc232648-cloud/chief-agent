from pathlib import Path
import json

import pytest

from runtime_qualification.catalog import load_candidates
from runtime_qualification.contracts import Evidence, Gate, GateStatus
from runtime_qualification.evaluator import comparable_workloads, eligible_candidates, evaluate_candidate, mandatory_gates
from runtime_qualification.policy import load_policy, plan_runtime

ROOT = Path(__file__).resolve().parents[1]


def candidates():
    return load_candidates((ROOT / 'config/runtime_candidates.json').read_text(encoding='utf-8'))


def policy():
    return load_policy((ROOT / 'config/runtime_policy.json').read_text(encoding='utf-8'), candidates())


def evidence(candidate, role, status=GateStatus.PROVEN, *, fresh=True):
    return tuple(
        Evidence(candidate.id, gate, status, 'fixture', 'synthetic-test', 'fixture', fresh)
        for gate in mandatory_gates(role)
    )


def test_catalog_and_policy_pin_distinct_execution_and_research_roles_without_claiming_qualification():
    execution, research = candidates()
    assert [(item.product, item.version) for item in (execution, research)] == [
        ('OpenClaw', '2026.10.1'), ('Hermes Agent', '0.21.3')
    ]
    assert any('isolated named gateway profiles' in item for item in execution.documented_capabilities)
    assert any('independent profiles' in item for item in research.documented_capabilities)
    configured = policy()
    assert configured.execution_candidate_id == execution.id
    assert configured.execution_role == 'GENERAL_EXECUTION'
    assert configured.execution_lifecycle == 'SERVICE'
    assert configured.execution_action_authority == 'CHIEF_GATED'
    assert configured.research_candidate_id == research.id
    assert configured.research_role == 'RESEARCH_ONLY'
    assert configured.research_lifecycle == 'ON_DEMAND'
    assert configured.research_action_authority == 'NONE'
    assert configured.auto_route_external_research is True


def test_documentation_is_not_proof_for_either_role():
    execution, research = candidates()
    execution_result = evaluate_candidate(
        execution, evidence(execution, 'GENERAL_EXECUTION', GateStatus.DOCUMENTED),
        role='GENERAL_EXECUTION',
    )
    research_result = evaluate_candidate(
        research, evidence(research, 'RESEARCH_ONLY', GateStatus.DOCUMENTED),
        role='RESEARCH_ONLY',
    )
    assert not execution_result.qualified
    assert not research_result.qualified
    assert all(blocker.endswith(':DOCUMENTED') for blocker in execution_result.blockers + research_result.blockers)


def test_missing_stale_or_failed_execution_gate_blocks_execution_runtime():
    execution = candidates()[0]
    assert not evaluate_candidate(execution, (), role='GENERAL_EXECUTION').qualified
    rows = list(evidence(execution, 'GENERAL_EXECUTION'))
    rows[0] = Evidence(execution.id, rows[0].gate, GateStatus.FAILED, 'fixture', 'synthetic', 'failed', True)
    assert not evaluate_candidate(execution, rows, role='GENERAL_EXECUTION').qualified
    rows = list(evidence(execution, 'GENERAL_EXECUTION'))
    rows[0] = Evidence(execution.id, rows[0].gate, GateStatus.PROVEN, 'fixture', 'synthetic', 'stale', False)
    assert not evaluate_candidate(execution, rows, role='GENERAL_EXECUTION').qualified


def test_research_runtime_requires_research_integrity_in_addition_to_base_gates():
    research = candidates()[1]
    rows = [
        Evidence(research.id, gate, GateStatus.PROVEN, 'fixture', 'synthetic', 'fixture', True)
        for gate in mandatory_gates('GENERAL_EXECUTION')
    ]
    result = evaluate_candidate(research, rows, role='RESEARCH_ONLY')
    assert not result.qualified
    assert result.blockers == ('RESEARCH_INTEGRITY:UNTESTED',)
    assert Gate.RESEARCH_INTEGRITY in mandatory_gates('RESEARCH_ONLY')


def test_execution_and_research_qualification_are_independent():
    execution, research = candidates()
    configured = policy()

    execution_only = plan_runtime(
        (execution, research),
        {execution.id: evidence(execution, configured.execution_role)},
        configured,
    )
    assert execution_only.status == 'EXECUTION_READY'
    assert execution_only.execution_qualified is True
    assert execution_only.research_qualified is False
    assert execution_only.external_research_auto_routed is False

    research_only = plan_runtime(
        (execution, research),
        {research.id: evidence(research, configured.research_role)},
        configured,
    )
    assert research_only.status == 'RESEARCH_READY'
    assert research_only.execution_qualified is False
    assert research_only.research_qualified is True
    assert research_only.selected_candidate_id is None
    assert research_only.external_research_auto_routed is True


def test_both_role_contracts_proven_enable_execution_and_external_research_route():
    execution, research = candidates()
    configured = policy()
    mapping = {
        execution.id: evidence(execution, configured.execution_role),
        research.id: evidence(research, configured.research_role),
    }
    plan = plan_runtime((execution, research), mapping, configured)
    assert plan.status == 'EXECUTION_AND_RESEARCH_READY'
    assert plan.selected_candidate_id == execution.id
    assert plan.execution_qualified is True
    assert plan.research_qualified is True
    assert plan.external_research_auto_routed is True
    eligible, results = eligible_candidates(
        (execution, research), mapping,
        {execution.id: configured.execution_role, research.id: configured.research_role},
    )
    assert eligible == (execution.id, research.id)
    assert all(item.qualified for item in results)


def test_policy_rejects_research_action_authority_wrong_lifecycle_and_disabled_auto_route():
    execution, research = candidates()
    base = json.loads((ROOT / 'config/runtime_policy.json').read_text(encoding='utf-8'))
    with pytest.raises(ValueError, match='no action authority'):
        load_policy({**base, 'research_action_authority': 'CHIEF_GATED'}, (execution, research))
    with pytest.raises(ValueError, match='ON_DEMAND'):
        load_policy({**base, 'research_lifecycle': 'SERVICE'}, (execution, research))
    with pytest.raises(ValueError, match='CHIEF_GATED'):
        load_policy({**base, 'execution_action_authority': 'NONE'}, (execution, research))
    with pytest.raises(ValueError, match='automatically'):
        load_policy({**base, 'auto_route_external_research': False}, (execution, research))


def test_policy_rejects_unknown_or_same_candidate_and_duplicate_keys():
    execution, research = candidates()
    base = json.loads((ROOT / 'config/runtime_policy.json').read_text(encoding='utf-8'))
    with pytest.raises(ValueError, match='pinned catalog'):
        load_policy({**base, 'execution_candidate_id': 'unknown-runtime'}, (execution, research))
    with pytest.raises(ValueError, match='different pinned candidate'):
        load_policy({**base, 'research_candidate_id': execution.id}, (execution, research))
    source = (ROOT / 'config/runtime_policy.json').read_text(encoding='utf-8')
    source = source.replace(
        '"research_action_authority": "NONE",',
        '"research_action_authority": "NONE",\n  "research_action_authority": "CHIEF_GATED",',
        1,
    )
    with pytest.raises(ValueError, match='Duplicate runtime policy key'):
        load_policy(source, (execution, research))


def test_evidence_cannot_cross_candidates_or_include_role_foreign_gate():
    execution, research = candidates()
    with pytest.raises(ValueError):
        evaluate_candidate(execution, evidence(research, 'RESEARCH_ONLY'), role='GENERAL_EXECUTION')
    row = Evidence(execution.id, Gate.RESEARCH_INTEGRITY, GateStatus.PROVEN, 'fixture', 'synthetic', 'wrong-role', True)
    with pytest.raises(ValueError, match='not part of this runtime role'):
        evaluate_candidate(execution, (row,), role='GENERAL_EXECUTION')


def test_workloads_cover_execution_authority_and_research_no_action_boundaries():
    execution = comparable_workloads('GENERAL_EXECUTION')
    research = comparable_workloads('RESEARCH_ONLY')
    assert 'chief_runtime_permission_conflict_chief_wins' in execution
    assert 'known_good_version_rollback' in execution
    assert 'research_prompt_injection_cannot_authorize_action' in research
    assert 'research_runtime_has_no_action_authority' in research
    assert 'research_source_provenance_preserved' in research
    assert len(comparable_workloads()) == len(set(comparable_workloads()))


def test_catalog_rejects_unapproved_or_insecure_provenance_urls():
    source = (ROOT / 'config/runtime_candidates.json').read_text(encoding='utf-8')
    approved = 'https://docs.openclaw.ai/gateway/security'
    assert approved in source
    with pytest.raises(ValueError):
        load_candidates(source.replace(approved, 'http://docs.openclaw.ai/gateway/security', 1))
    with pytest.raises(ValueError):
        load_candidates(source.replace(approved, 'https://example.com/security', 1))


def test_cli_reports_no_role_ready_without_execution_evidence(capsys):
    from runtime_qualification.__main__ import main

    assert main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'NO_RUNTIME_READY'
    assert payload['execution']['role'] == 'GENERAL_EXECUTION'
    assert payload['execution']['qualified'] is False
    assert payload['execution']['action_authority'] == 'CHIEF_GATED'
    assert payload['research']['role'] == 'RESEARCH_ONLY'
    assert payload['research']['lifecycle'] == 'ON_DEMAND'
    assert payload['research']['action_authority'] == 'NONE'
    assert payload['research']['qualified'] is False
    assert payload['research']['active_route'] is False
