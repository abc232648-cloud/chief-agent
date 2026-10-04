from pathlib import Path

import pytest

from runtime_qualification.catalog import load_candidates
from runtime_qualification.contracts import Evidence, Gate, GateStatus
from runtime_qualification.evaluator import MANDATORY_GATES, comparable_workloads, eligible_candidates, evaluate_candidate

ROOT=Path(__file__).resolve().parents[1]


def candidates():return load_candidates((ROOT/'config/runtime_candidates.json').read_text())


def evidence(candidate,status=GateStatus.PROVEN,*,fresh=True):
    return tuple(Evidence(candidate.id,g,status,'fixture','synthetic-test','fixture',fresh) for g in MANDATORY_GATES)


def test_catalog_pins_openclaw_and_hermes_without_claiming_qualification():
    items=candidates()
    assert [(x.product,x.version) for x in items]==[('OpenClaw','2026.9.5'),('Hermes Agent','0.21.3')]
    assert all(x.release_url.startswith('https://') for x in items)
    assert any('isolated named gateway profiles' in c for c in items[0].documented_capabilities)
    assert any('independent profiles' in c for c in items[1].documented_capabilities)


def test_documentation_is_not_proof_of_hard_gate():
    c=candidates()[0]
    result=evaluate_candidate(c,evidence(c,GateStatus.DOCUMENTED))
    assert result.qualified is False
    assert all(x.endswith(':DOCUMENTED') for x in result.blockers)


def test_missing_stale_or_failed_gate_blocks_candidate():
    c=candidates()[0]
    assert not evaluate_candidate(c,()).qualified
    rows=list(evidence(c));rows[0]=Evidence(c.id,rows[0].gate,GateStatus.FAILED,'fixture','synthetic','failed',True)
    assert not evaluate_candidate(c,rows).qualified
    rows=list(evidence(c));rows[0]=Evidence(c.id,rows[0].gate,GateStatus.PROVEN,'fixture','synthetic','stale',False)
    assert not evaluate_candidate(c,rows).qualified


def test_all_hard_gates_proven_is_eligible_but_does_not_auto_select():
    a,b=candidates();mapping={a.id:evidence(a),b.id:evidence(b)}
    eligible,results=eligible_candidates((a,b),mapping)
    assert eligible==(a.id,b.id)
    assert all(r.qualified for r in results)
    # Framework returns eligible candidates; product selection remains an explicit reviewed decision.


def test_evidence_cannot_be_reused_for_another_candidate_or_gate_duplicated():
    a,b=candidates()
    with pytest.raises(ValueError):evaluate_candidate(a,evidence(b))
    rows=list(evidence(a));rows.append(rows[0])
    with pytest.raises(ValueError,match='one current summary'):evaluate_candidate(a,rows)


def test_comparable_workloads_cover_security_recovery_and_device_scope():
    workloads=comparable_workloads()
    assert 'chief_runtime_permission_conflict_chief_wins' in workloads
    assert 'known_good_version_rollback' in workloads
    assert 'device_node_surface_if_required' in workloads
    assert len(workloads)==len(set(workloads))


def test_catalog_rejects_unapproved_or_insecure_provenance_urls():
    raw=(ROOT/'config/runtime_candidates.json').read_text().replace('https://docs.openclaw.ai/network','http://docs.openclaw.ai/network')
    with pytest.raises(ValueError):load_candidates(raw)
    raw=(ROOT/'config/runtime_candidates.json').read_text().replace('https://docs.openclaw.ai/network','https://example.com/network')
    with pytest.raises(ValueError):load_candidates(raw)

def test_cli_reports_no_selection_without_execution_evidence(capsys):
    from runtime_qualification.__main__ import main
    assert main([])==0
    import json
    payload=json.loads(capsys.readouterr().out)
    assert payload['status']=='NO_RUNTIME_SELECTED'
    assert payload['eligible']==[]
    assert {x['candidate_id'] for x in payload['qualification']}=={'openclaw-2026.9.5','hermes-agent-0.21.3'}
