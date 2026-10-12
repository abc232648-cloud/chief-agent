"""Explicit role-scoped runtime selection policy layered on fail-closed evidence."""
from dataclasses import dataclass
import json

from .evaluator import evaluate_candidate


@dataclass(frozen=True)
class RuntimePolicy:
    execution_candidate_id: str
    execution_role: str
    execution_lifecycle: str
    execution_action_authority: str
    research_candidate_id: str
    research_role: str
    research_lifecycle: str
    research_action_authority: str
    auto_route_external_research: bool

    @property
    def primary_candidate_id(self):
        """Compatibility alias used by installation-readiness profile reservation."""
        return self.execution_candidate_id

    @property
    def supplemental_candidate_id(self):
        """Compatibility alias; research is not a fallback or general supplement."""
        return self.research_candidate_id


@dataclass(frozen=True)
class RuntimePlan:
    status: str
    execution_candidate_id: str
    execution_qualified: bool
    research_candidate_id: str
    research_qualified: bool
    execution_blockers: tuple[str, ...]
    research_blockers: tuple[str, ...]
    external_research_auto_routed: bool

    @property
    def selected_candidate_id(self):
        return self.execution_candidate_id if self.execution_qualified else None


_EXPECTED = {
    'execution_candidate_id',
    'execution_role',
    'execution_lifecycle',
    'execution_action_authority',
    'research_candidate_id',
    'research_role',
    'research_lifecycle',
    'research_action_authority',
    'auto_route_external_research',
}


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
    if not isinstance(data, dict) or set(data) != _EXPECTED:
        raise ValueError('Runtime policy has an invalid shape.')
    ids = {candidate.id for candidate in candidates}
    execution = data['execution_candidate_id']
    research = data['research_candidate_id']
    if execution not in ids:
        raise ValueError('Execution runtime candidate is not in the pinned catalog.')
    if research not in ids or research == execution:
        raise ValueError('Research runtime candidate must be a different pinned candidate.')
    if data['execution_role'] != 'GENERAL_EXECUTION':
        raise ValueError('Execution runtime role must be GENERAL_EXECUTION.')
    if data['execution_lifecycle'] != 'SERVICE':
        raise ValueError('Execution runtime lifecycle must be SERVICE.')
    if data['execution_action_authority'] != 'CHIEF_GATED':
        raise ValueError('Execution runtime may act only through CHIEF_GATED authority.')
    if data['research_role'] != 'RESEARCH_ONLY':
        raise ValueError('Research runtime role must be RESEARCH_ONLY.')
    if data['research_lifecycle'] != 'ON_DEMAND':
        raise ValueError('Research runtime lifecycle must be ON_DEMAND.')
    if data['research_action_authority'] != 'NONE':
        raise ValueError('Research runtime must have no action authority.')
    if data['auto_route_external_research'] is not True:
        raise ValueError('External research must route automatically to the research runtime.')
    return RuntimePolicy(**data)


def plan_runtime(candidates, evidence_by_candidate, policy):
    candidates = tuple(candidates)
    by_id = {candidate.id: candidate for candidate in candidates}
    if policy.execution_candidate_id not in by_id or policy.research_candidate_id not in by_id:
        raise ValueError('Runtime policy refers to a candidate outside this catalog.')
    execution = evaluate_candidate(
        by_id[policy.execution_candidate_id],
        evidence_by_candidate.get(policy.execution_candidate_id, ()),
        role=policy.execution_role,
    )
    research = evaluate_candidate(
        by_id[policy.research_candidate_id],
        evidence_by_candidate.get(policy.research_candidate_id, ()),
        role=policy.research_role,
    )
    if execution.qualified and research.qualified:
        status = 'EXECUTION_AND_RESEARCH_READY'
    elif execution.qualified:
        status = 'EXECUTION_READY'
    elif research.qualified:
        status = 'RESEARCH_READY'
    else:
        status = 'NO_RUNTIME_READY'
    return RuntimePlan(
        status=status,
        execution_candidate_id=policy.execution_candidate_id,
        execution_qualified=execution.qualified,
        research_candidate_id=policy.research_candidate_id,
        research_qualified=research.qualified,
        execution_blockers=execution.blockers,
        research_blockers=research.blockers,
        external_research_auto_routed=policy.auto_route_external_research and research.qualified,
    )
