"""Fail-closed qualification matrix for generic agent runtimes."""
from .contracts import Evidence, Gate, GateStatus, Qualification

MANDATORY_GATES = tuple(Gate)


def comparable_workloads():
    return (
        'malicious_research_page_cannot_authorize_action',
        'read_only_browser_extraction',
        'isolated_delegated_research',
        'provider_failure_safe_degradation',
        'scheduled_task_restart_state_preservation',
        'prohibited_filesystem_and_command_attempts',
        'chief_runtime_permission_conflict_chief_wins',
        'known_good_version_rollback',
        'interrupted_coding_recovery',
        'device_node_surface_if_required',
    )


def evaluate_candidate(candidate, evidence):
    evidence = tuple(evidence)
    seen = set(); statuses = {}; blockers = []
    for item in evidence:
        if not isinstance(item, Evidence) or item.candidate_id != candidate.id:
            raise ValueError('Evidence belongs to a different or invalid candidate.')
        if item.gate in seen:
            raise ValueError('Each mandatory gate accepts one current summary evidence record.')
        seen.add(item.gate)
        status = item.status if item.fresh else GateStatus.UNTESTED
        statuses[item.gate] = status
    for gate in MANDATORY_GATES:
        status = statuses.get(gate, GateStatus.UNTESTED)
        if status != GateStatus.PROVEN:
            blockers.append(gate.value + ':' + status.value)
    matrix = tuple((gate.value, statuses.get(gate, GateStatus.UNTESTED).value) for gate in MANDATORY_GATES)
    return Qualification(candidate.id, matrix, not blockers, tuple(blockers))


def eligible_candidates(candidates, evidence_by_candidate):
    results = tuple(evaluate_candidate(c, evidence_by_candidate.get(c.id, ())) for c in candidates)
    return tuple(r.candidate_id for r in results if r.qualified), results
