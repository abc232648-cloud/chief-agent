"""Read-only operational preflight for a manually selected Chief source release."""
import argparse
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--schema', required=True)
    args = parser.parse_args(argv)
    try:
        from deployment.launch import configure
        from deployment.schema import preflight
        from application.composition import default_catalogs
        instance = configure(args.config)
        preflight(instance.database, args.schema)
        default_catalogs()
        result = {'status': 'READY', 'schema_sha256': args.schema, 'external_actions': False}
    except Exception:
        print(json.dumps({'status': 'NOT_READY', 'external_actions': False}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
