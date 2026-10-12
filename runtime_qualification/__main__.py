"""Inspect pinned runtime candidates and fail-closed selection policy. No runtime is installed here."""
from dataclasses import asdict
import json
from pathlib import Path

from .catalog import load_candidates
from .evaluator import eligible_candidates
from .policy import load_policy, plan_runtime


def main(argv=None):
    root=Path(__file__).resolve().parents[1]
    candidates=load_candidates((root/'config/runtime_candidates.json').read_text(encoding='utf-8'))
    policy=load_policy((root/'config/runtime_policy.json').read_text(encoding='utf-8'),candidates)
    eligible,results=eligible_candidates(candidates,{})
    plan=plan_runtime(candidates,{},policy)
    payload={
        'status':plan.status,
        'selected':plan.selected_candidate_id,
        'primary_candidate_id':plan.primary_candidate_id,
        'supplemental_candidate_id':plan.supplemental_candidate_id,
        'supplemental_mode':policy.supplemental_mode,
        'supplemental_workloads':plan.supplemental_workloads,
        'authorized_supplemental_workloads':plan.authorized_supplemental_workloads,
        'eligible':eligible,
        'candidates':[asdict(x) for x in candidates],
        'qualification':[asdict(x) for x in results],
        'note':'Documentation is provenance only. Fresh per-gate execution evidence is required before selection or supplemental workload authority.',
    }
    print(json.dumps(payload,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
