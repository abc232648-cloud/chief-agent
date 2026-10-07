"""Record a local operator observation. Never installs or activates an update."""
import argparse
import json
import sqlite3
from contextlib import contextmanager


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
        instance = configure(args.config)
        # Existing DB only; no Store initialization, schema changes or data restore.
        class ExistingStore:
            @contextmanager
            def _connect(self):
                con = sqlite3.connect(instance.database.as_uri() + '?mode=rw', uri=True)
                con.row_factory = sqlite3.Row
                try:
                    with con:
                        yield con
                finally:
                    con.close()
        receipt = record(ExistingStore(), dict(event_id=args.event_id, outcome=args.outcome,
                         source_sha256=args.source_sha256, evidence_sha256=args.evidence_sha256))
        print(json.dumps({'status': 'OPERATOR_OBSERVATION_RECORDED', 'event': receipt,
                          'installation_changed': False}))
        return 0
    except Exception:
        print(json.dumps({'status': 'HISTORY_NOT_RECORDED', 'installation_changed': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
