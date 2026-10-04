"""Read-only catalog/impact inspection: python -m application.capability_catalog."""
import argparse
from dataclasses import asdict
import json

from capabilities.contracts import Node
from capabilities.regression import regression_plan
from .composition import default_catalogs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--changed', action='append', default=[], metavar='KIND:ID')
    parser.add_argument('--candidate', help='Identity of the source being tested, preferably its SHA-256.')
    args = parser.parse_args(argv)
    registry = default_catalogs().capabilities
    try:
        if args.changed:
            changes = []
            for value in args.changed:
                kind, separator, identity = value.partition(':')
                if not separator:
                    raise ValueError('Use capability:ID or component:ID.')
                changes.append(Node(kind, identity))
            result = asdict(regression_plan(registry, changes, candidate=args.candidate))
        else:
            result = registry.describe()
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
