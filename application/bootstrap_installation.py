"""Local, ordinary-user offline environment preparation. Never activate Chief."""
import argparse
import json
from pathlib import Path
from compatibility.manifest import _unique_object
from installation.bootstrap import prepare_environment
from installation.dependencies import MAX_BUNDLE
from update_center.staging import MAX_ARCHIVE


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source-archive','wheel-archive','runtime-profile','archive-sha256','source-sha256','wheels-sha256','installation-root'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--confirm-private-storage',action='store_true')
    parser.add_argument('--confirm-runtime-and-dependencies-reviewed',action='store_true')
    args=parser.parse_args(argv)
    try:
        with Path(args.source_archive).open('rb') as f:source=f.read(MAX_ARCHIVE+1)
        with Path(args.wheel_archive).open('rb') as f:wheels=f.read(MAX_BUNDLE+1)
        with Path(args.runtime_profile).open('rb') as f:profile=f.read(16385)
        if len(profile)>16384:raise ValueError('Runtime profile exceeds limit.')
        runtime=json.loads(profile,object_pairs_hook=_unique_object)
        receipt=prepare_environment(source,wheels,args.installation_root,expected_archive=args.archive_sha256,
            expected_source=args.source_sha256,expected_wheels=args.wheels_sha256,expected_runtime=runtime,
            private_storage_confirmed=args.confirm_private_storage,
            runtime_and_dependencies_reviewed=args.confirm_runtime_and_dependencies_reviewed)
    except Exception:
        print(json.dumps({'status':'BOOTSTRAP_REJECTED','activation':'BLOCKED','reason':'Review exact pins, runtime, offline dependencies and private storage.'}));return 2
    print(json.dumps(receipt));return 0 if receipt['status']=='ENVIRONMENT_PREPARED_NOT_ACTIVATED' else 2


if __name__=='__main__':raise SystemExit(main())
