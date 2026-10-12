"""Inspect pinned role-scoped runtimes. No runtime is installed or started here."""
from dataclasses import asdict
import json
from pathlib import Path

from .catalog import load_candidates
from .evaluator import evaluate_candidate
from .policy import load_policy, plan_runtime


def main(argv=None):
    root=Path(__file__).resolve().parents[1]
    candidates=load_candidates((root/'config/runtime_candidates.json').read_text(encoding='utf-8'))
    policy=load_policy((root/'config/runtime_policy.json').read_text(encoding='utf-8'),candidates)
    by_id={candidate.id:candidate for candidate in candidates}
    results=(
        evaluate_candidate(by_id[policy.execution_candidate_id],(),role=policy.execution_role),
        evaluate_candidate(by_id[policy.research_candidate_id],(),role=policy.research_role),
    )
    plan=plan_runtime(candidates,{},policy)
    payload={
        'status':plan.status,
        'execution':{
            'candidate_id':policy.execution_candidate_id,
            'role':policy.execution_role,
            'lifecycle':policy.execution_lifecycle,
            'action_authority':policy.execution_action_authority,
            'qualified':plan.execution_qualified,
            'blockers':plan.execution_blockers,
        },
        'research':{
            'candidate_id':policy.research_candidate_id,
            'role':policy.research_role,
            'lifecycle':policy.research_lifecycle,
            'action_authority':policy.research_action_authority,
            'qualified':plan.research_qualified,
            'auto_route_external_research':policy.auto_route_external_research,
            'active_route':plan.external_research_auto_routed,
            'blockers':plan.research_blockers,
        },
        'candidates':[asdict(x) for x in candidates],
        'qualification':[asdict(x) for x in results],
        'note':'Documentation is provenance only. Each role needs fresh PROVEN evidence for every gate assigned to that role.',
    }
    print(json.dumps(payload,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
