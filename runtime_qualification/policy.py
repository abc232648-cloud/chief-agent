"""Explicit runtime selection policy layered on fail-closed qualification evidence."""
from dataclasses import dataclass
import json

from .evaluator import eligible_candidates


@dataclass(frozen=True)
class RuntimePolicy:
    primary_candidate_id: str
    supplemental_candidate_id: str
    supplemental_mode: str
    supplemental_workloads: tuple[str, ...]


@dataclass(frozen=True)
class RuntimePlan:
    status: str
    primary_candidate_id: str
    selected_candidate_id: str | None
    primary_blockers: tuple[str, ...]
    supplemental_candidate_id: str
    supplemental_qualified: bool
    supplemental_workloads: tuple[str, ...]
    authorized_supplemental_workloads: tuple[str, ...]


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate runtime policy key.')
        result[key] = value
    return result


def load_policy(data, candidates):
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode('utf-8')
    if isinstance(data, str):
        data = json.loads(data, object_pairs_hook=_no_duplicates)
    expected = {'primary_candidate_id', 'supplemental_candidate_id', 'supplemental_mode', 'supplemental_workloads'}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError('Runtime policy has an invalid shape.')
    ids = {candidate.id for candidate in candidates}
    primary = data['primary_candidate_id']
    supplemental = data['supplemental_candidate_id']
    if not isinstance(primary, str) or primary not in ids:
        raise ValueError('Primary runtime candidate is not in the pinned catalog.')
    if not isinstance(supplemental, str) or supplemental not in ids or supplemental == primary:
        raise ValueError('Supplemental runtime candidate must be a different pinned candidate.')
    if data['supplemental_mode'] != 'GAP_ONLY':
        raise ValueError('Supplemental runtime may only operate in GAP_ONLY mode.')
    workloads = data['supplemental_workloads']
    if not isinstance(workloads, list) or any(not isinstance(item, str) or not item.strip() for item in workloads):
        raise ValueError('Supplemental workloads must be a list of nonempty workload IDs.')
    if len(set(workloads)) != len(workloads):
        raise ValueError('Supplemental workloads must be unique.')
    return RuntimePolicy(primary, supplemental, 'GAP_ONLY', tuple(workloads))


def plan_runtime(candidates, evidence_by_candidate, policy):
    candidates = tuple(candidates)
    by_id = {candidate.id: candidate for candidate in candidates}
    if policy.primary_candidate_id not in by_id or policy.supplemental_candidate_id not in by_id:
        raise ValueError('Runtime policy refers to a candidate outside this catalog.')
    eligible, results = eligible_candidates(candidates, evidence_by_candidate)
    result_by_id = {result.candidate_id: result for result in results}
    primary = result_by_id[policy.primary_candidate_id]
    supplemental = result_by_id[policy.supplemental_candidate_id]
    primary_ready = policy.primary_candidate_id in eligible
    supplemental_ready = policy.supplemental_candidate_id in eligible
    selected = policy.primary_candidate_id if primary_ready else None
    # A supplemental runtime is never a general fallback. It can be used only
    # after the primary is qualified, the supplemental candidate is independently
    # qualified, and an explicit workload gap has been recorded in policy.
    authorized = policy.supplemental_workloads if primary_ready and supplemental_ready else ()
    return RuntimePlan(
        'PRIMARY_READY' if selected else 'NO_RUNTIME_SELECTED',
        policy.primary_candidate_id,
        selected,
        primary.blockers,
        policy.supplemental_candidate_id,
        supplemental_ready,
        policy.supplemental_workloads,
        authorized,
    )
