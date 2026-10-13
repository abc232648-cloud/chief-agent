"""Record a local operator observation. Never installs or activates an update."""
import argparse
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--event-id', required=True)
    parser.add_argument('--outcome', required=True, choices=['STAGED', 'DEPLOYED', 'ROLLED_BACK', 'FAILED'])
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--evidence-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        from deployment.launch import configure
        from update_center.history import record
        from update_center.storage import ExistingUpdateStore
        instance = configure(args.config)
        # Existing DB only; no Store initialization, schema changes or data restore.
        receipt = record(ExistingUpdateStore(instance.database), dict(event_id=args.event_id, outcome=args.outcome,
                         source_sha256=args.source_sha256, evidence_sha256=args.evidence_sha256))
        print(json.dumps({'status': 'OPERATOR_OBSERVATION_RECORDED', 'event': receipt,
                          'installation_changed': False}))
        return 0
    except Exception:
        print(json.dumps({'status': 'HISTORY_NOT_RECORDED', 'installation_changed': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
