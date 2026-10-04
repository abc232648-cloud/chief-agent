"""Application composition CLI for staging-only dependency-aware update plans."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from compatibility.manifest import load_manifest
from update_center.planner import plan_update
from .composition import default_catalogs


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('current')
    parser.add_argument('candidate')
    args=parser.parse_args(argv)
    current=load_manifest(Path(args.current).read_text(encoding='utf-8'))
    candidate=load_manifest(Path(args.candidate).read_text(encoding='utf-8'))
    plan=plan_update(current,candidate,default_catalogs().capabilities)
    print(json.dumps(asdict(plan),indent=2,sort_keys=True))
    return 2 if plan.blockers else 0


if __name__=='__main__':
    raise SystemExit(main())
