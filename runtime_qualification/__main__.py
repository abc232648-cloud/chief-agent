"""Inspect the pinned runtime candidate catalog. This performs no installation or qualification work."""
from dataclasses import asdict
import json
from pathlib import Path

from .catalog import load_candidates
from .evaluator import eligible_candidates


def main(argv=None):
    root=Path(__file__).resolve().parents[1]
    candidates=load_candidates((root/'config/runtime_candidates.json').read_text(encoding='utf-8'))
    eligible,results=eligible_candidates(candidates,{})
    payload={
        'status':'NO_RUNTIME_SELECTED',
        'eligible':eligible,
        'candidates':[asdict(x) for x in candidates],
        'qualification':[asdict(x) for x in results],
        'note':'Documentation is provenance only. Fresh per-gate execution evidence is required before selection.',
    }
    print(json.dumps(payload,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
