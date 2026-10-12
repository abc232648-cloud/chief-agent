from pathlib import Path
import json

import pytest

from runtime_qualification.catalog import load_candidates
from runtime_qualification.contracts import Evidence, GateStatus
from runtime_qualification.evaluator import MANDATORY_GATES, comparable_workloads, eligible_candidates, evaluate_candidate
from runtime_qualification.policy import RuntimePolicy, load_policy, plan_runtime

ROOT = Path(__file__).resolve().parents[1]


def candidates():
    return load_candidates((ROOT / 'config/runtime_candidates.json').read_text(encoding='utf-8'))


def policy():
    return load_policy((ROOT / 'config/runtime_policy.json').read_text(encoding='utf-8'), candidates())


def evidence(candidate, status=GateStatus.PROVEN, *, fresh=True):
    return tuple(Evidence(candidate.id, gate, status, 'fixture', 'synthetic-test', 'fixture', fresh) for gate in MANDATORY_GATES)


def test_catalog_pins_primary_and_supplemental_candidates_without_claiming_qualification():
    items = candidates()
    assert [(item.product, item.version) for item in items] == [('OpenClaw', '2026.10.1'), ('Hermes Agent', '0.21.3')]
    assert all(item.release_url.startswith('https://') for item in items)
    assert any('isolated named gateway profiles' in capability for capability in items[0].documented_capabilities)
    assert any('independent profiles' in capability for capability in items[1].documented_capabilities)
    configured = policy()
    assert configured.primary_candidate_id == items[0].id
    assert configured.supplemental_candidate_id == items[1].id
    assert configured.supplemental_mode == 'GAP_ONLY'
    assert configured.supplemental_workloads == ()


def test_documentation_is_not_proof_of_hard_gate():
    candidate = candidates()[0]
    result = evaluate_candidate(candidate, evidence(candidate, GateStatus.DOCUMENTED))
    assert result.qualified is False
    assert all(blocker.endswith(':DOCUMENTED') for blocker in result.blockers)


def test_missing_stale_or_failed_gate_blocks_candidate():
    candidate = candidates()[0]
    assert not evaluate_candidate(candidate, ()).qualified
    rows = list(evidence(candidate))
    rows[0] = Evidence(candidate.id, rows[0].gate, GateStatus.FAILED, 'fixture', 'synthetic', 'failed', True)
    assert not evaluate_candidate(candidate, rows).qualified
    rows = list(evidence(candidate))
    rows[0] = Evidence(candidate.id, rows[0].gate, GateStatus.PROVEN, 'fixture', 'synthetic', 'stale', False)
    assert not evaluate_candidate(candidate, rows).qualified


def test_all_hard_gates_proven_is_eligible_but_selection_still_follows_policy():
    primary, supplemental = candidates()
    mapping = {primary.id: evidence(primary), supplemental.id: evidence(supplemental)}
    eligible, results = eligible_candidates((primary, supplemental), mapping)
    assert eligible == (primary.id, supplemental.id)
    assert all(result.qualified for result in results)
    plan = plan_runtime((primary, supplemental), mapping, policy())
    assert plan.status == 'PRIMARY_READY'
    assert plan.selected_candidate_id == primary.id
    assert plan.supplemental_qualified is True
    assert plan.authorized_supplemental_workloads == ()


def test_supplemental_candidate_never_replaces_unqualified_primary():
    primary, supplemental = candidates()
    mapping = {supplemental.id: evidence(supplemental)}
    plan = plan_runtime((primary, supplemental), mapping, policy())
    assert plan.status == 'NO_RUNTIME_SELECTED'
    assert plan.selected_candidate_id is None
    assert plan.supplemental_qualified is True
    assert plan.authorized_supplemental_workloads == ()
    assert plan.primary_blockers


def test_explicit_gap_is_authorized_only_when_both_candidates_are_qualified():
    primary, supplemental = candidates()
    configured = RuntimePolicy(primary.id, supplemental.id, 'GAP_ONLY', ('interrupted_coding_recovery',))
    both = {primary.id: evidence(primary), supplemental.id: evidence(supplemental)}
    plan = plan_runtime((primary, supplemental), both, configured)
    assert plan.selected_candidate_id == primary.id
    assert plan.authorized_supplemental_workloads == ('interrupted_coding_recovery',)
    primary_only = plan_runtime((primary, supplemental), {primary.id: evidence(primary)}, configured)
    assert primary_only.selected_candidate_id == primary.id
    assert primary_only.supplemental_qualified is False
    assert primary_only.authorized_supplemental_workloads == ()


def test_policy_rejects_general_fallback_unknown_candidates_and_duplicate_workloads():
    primary, supplemental = candidates()
    base = {
        'primary_candidate_id': primary.id,
        'supplemental_candidate_id': supplemental.id,
        'supplemental_mode': 'GAP_ONLY',
        'supplemental_workloads': [],
    }
    with pytest.raises(ValueError, match='GAP_ONLY'):
        load_policy({**base, 'supplemental_mode': 'FALLBACK'}, (primary, supplemental))
    with pytest.raises(ValueError, match='pinned catalog'):
        load_policy({**base, 'primary_candidate_id': 'unknown-runtime'}, (primary, supplemental))
    with pytest.raises(ValueError, match='different pinned candidate'):
        load_policy({**base, 'supplemental_candidate_id': primary.id}, (primary, supplemental))
    with pytest.raises(ValueError, match='unique'):
        load_policy({**base, 'supplemental_workloads': ['gap-a', 'gap-a']}, (primary, supplemental))


def test_policy_json_rejects_duplicate_keys():
    primary, supplemental = candidates()
    source = (
        '{"primary_candidate_id":"' + primary.id + '",'
        '"supplemental_candidate_id":"' + supplemental.id + '",'
        '"supplemental_mode":"GAP_ONLY",'
        '"supplemental_mode":"FALLBACK",'
        '"supplemental_workloads":[]}'
    )
    with pytest.raises(ValueError, match='Duplicate runtime policy key'):
        load_policy(source, (primary, supplemental))


def test_evidence_cannot_be_reused_for_another_candidate_or_gate_duplicated():
    primary, supplemental = candidates()
    with pytest.raises(ValueError):
        evaluate_candidate(primary, evidence(supplemental))
    rows = list(evidence(primary))
    rows.append(rows[0])
    with pytest.raises(ValueError, match='one current summary'):
        evaluate_candidate(primary, rows)


def test_comparable_workloads_cover_security_recovery_and_device_scope():
    workloads = comparable_workloads()
    assert 'chief_runtime_permission_conflict_chief_wins' in workloads
    assert 'known_good_version_rollback' in workloads
    assert 'interrupted_coding_recovery' in workloads
    assert 'device_node_surface_if_required' in workloads
    assert len(workloads) == len(set(workloads))


def test_catalog_rejects_unapproved_or_insecure_provenance_urls():
    source = (ROOT / 'config/runtime_candidates.json').read_text(encoding='utf-8')
    approved = 'https://docs.openclaw.ai/gateway/security'
    assert approved in source
    with pytest.raises(ValueError):
        load_candidates(source.replace(approved, 'http://docs.openclaw.ai/gateway/security', 1))
    with pytest.raises(ValueError):
        load_candidates(source.replace(approved, 'https://example.com/security', 1))


def test_cli_reports_no_selection_without_execution_evidence(capsys):
    from runtime_qualification.__main__ import main

    assert main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'NO_RUNTIME_SELECTED'
    assert payload['selected'] is None
    assert payload['eligible'] == []
    assert payload['primary_candidate_id'] == 'openclaw-2026.10.1'
    assert payload['supplemental_candidate_id'] == 'hermes-agent-0.21.3'
    assert payload['supplemental_mode'] == 'GAP_ONLY'
    assert payload['supplemental_workloads'] == []
    assert payload['authorized_supplemental_workloads'] == []
    assert {item['candidate_id'] for item in payload['qualification']} == {'openclaw-2026.10.1', 'hermes-agent-0.21.3'}
