"""Offline CLI. Never prints row contents, configuration values or session data."""
import argparse
import json
import sqlite3
from .backup import create_backup, verify_backup, restore_isolated, _load_json


def main(argv=None):
    parser = argparse.ArgumentParser(description='Private backup and quarantined restore; no production promotion.')
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('database')
    backup.add_argument('destination')
    backup.add_argument('--source-root', required=True)
    backup.add_argument('--source-manifest', required=True)
    backup.add_argument('--assets-manifest')
    backup.add_argument('--writers-quiesced', action='store_true')
    backup.add_argument('--private-storage-confirmed', action='store_true')
    backup.add_argument('--timeout-seconds', type=float, default=30)
    for name in ('verify', 'restore'):
        command = commands.add_parser(name)
        command.add_argument('directory')
        if name == 'restore':
            command.add_argument('destination')
            command.add_argument('--private-storage-confirmed', action='store_true')
        command.add_argument('--manifest-sha256', required=True)
        command.add_argument('--expected-source', required=True)
        command.add_argument('--expected-schema', required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        if command == 'backup':
            assets_file = args.pop('assets_manifest')
            args['assets'] = _load_json(assets_file) if assets_file else []
            result = create_backup(**args)
        elif command == 'restore':
            result = restore_isolated(**args)
        else:
            manifest = verify_backup(**args)
            result = {'status': 'VERIFIED', 'backup_id': manifest['backup_id'],
                      'coverage': manifest['coverage'], 'schema_sha256': manifest['database']['schema_sha256']}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        # Exceptions can contain private paths or SQLite values; keep CLI output bounded.
        print(json.dumps({'status': 'FAILED', 'error_type': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
