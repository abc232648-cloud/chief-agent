"""Fail-closed qualification matrix for role-scoped agent runtimes."""
from .contracts import Evidence, Gate, GateStatus, Qualification


BASE_GATES = (
    Gate.AUTHORITY_SECURITY,
    Gate.ISOLATION,
    Gate.RELIABILITY,
    Gate.WINDOWS_COMPATIBILITY,
    Gate.LINUX_FOLIO_COMPATIBILITY,
    Gate.OPERATIONS,
    Gate.PRIVACY,
)
ROLE_GATES = {
    'GENERAL_EXECUTION': BASE_GATES,
    'RESEARCH_ONLY': BASE_GATES + (Gate.RESEARCH_INTEGRITY,),
}


def mandatory_gates(role):
    try:
        return ROLE_GATES[role]
    except KeyError as exc:
        raise ValueError('Unknown runtime role.') from exc


def comparable_workloads(role=None):
    execution = (
        'malicious_page_cannot_authorize_action',
        'read_only_browser_extraction',
        'provider_failure_safe_degradation',
        'scheduled_task_restart_state_preservation',
        'prohibited_filesystem_and_command_attempts',
        'chief_runtime_permission_conflict_chief_wins',
        'known_good_version_rollback',
        'interrupted_execution_recovery',
        'device_node_surface_if_required',
    )
    research = (
        'external_research_routes_to_research_runtime',
        'internal_data_does_not_start_research_runtime',
        'research_prompt_injection_cannot_authorize_action',
        'research_runtime_has_no_action_authority',
        'research_source_provenance_preserved',
        'research_profile_cannot_read_unrelated_chief_secrets',
        'research_provider_failure_safe_degradation',
        'isolated_parallel_research',
    )
    if role is None:
        return execution + research
    if role == 'GENERAL_EXECUTION':
        return execution
    if role == 'RESEARCH_ONLY':
        return research
    raise ValueError('Unknown runtime role.')


def evaluate_candidate(candidate, evidence, *, role='GENERAL_EXECUTION'):
    gates = mandatory_gates(role)
    evidence = tuple(evidence)
    seen = set(); statuses = {}; blockers = []
    for item in evidence:
        if not isinstance(item, Evidence) or item.candidate_id != candidate.id:
            raise ValueError('Evidence belongs to a different or invalid candidate.')
        if item.gate not in gates:
            raise ValueError('Evidence gate is not part of this runtime role.')
        if item.gate in seen:
            raise ValueError('Each mandatory gate accepts one current summary evidence record.')
        seen.add(item.gate)
        status = item.status if item.fresh else GateStatus.UNTESTED
        statuses[item.gate] = status
    for gate in gates:
        status = statuses.get(gate, GateStatus.UNTESTED)
        if status != GateStatus.PROVEN:
            blockers.append(gate.value + ':' + status.value)
    matrix = tuple((gate.value, statuses.get(gate, GateStatus.UNTESTED).value) for gate in gates)
    return Qualification(candidate.id, role, matrix, not blockers, tuple(blockers))


def eligible_candidates(candidates, evidence_by_candidate, role_by_candidate):
    results = tuple(
        evaluate_candidate(c, evidence_by_candidate.get(c.id, ()), role=role_by_candidate[c.id])
        for c in candidates
    )
    return tuple(r.candidate_id for r in results if r.qualified), results
