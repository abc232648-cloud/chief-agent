"""Download and prepare selected pinned Python dependencies without activation."""
import argparse
import json
from pathlib import Path
from compatibility.manifest import _unique_object
from installation.automatic import prepare_selected
from installation.profiles import PROFILES
from update_center.staging import MAX_ARCHIVE


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source-archive','archive-sha256','source-sha256','runtime-profile','installation-root'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--component',action='append',choices=PROFILES,default=[])
    parser.add_argument('--confirm-private-storage',action='store_true')
    parser.add_argument('--confirm-runtime-and-dependencies-reviewed',action='store_true')
    args=parser.parse_args(argv)
    try:
        with Path(args.source_archive).open('rb') as f:source=f.read(MAX_ARCHIVE+1)
        with Path(args.runtime_profile).open('rb') as f:raw=f.read(16385)
        if len(raw)>16384:raise ValueError('Runtime profile exceeds limit.')
        runtime=json.loads(raw,object_pairs_hook=_unique_object)
        result=prepare_selected(source,args.installation_root,components=args.component,expected_archive=args.archive_sha256,
            expected_source=args.source_sha256,expected_runtime=runtime,private_storage_confirmed=args.confirm_private_storage,
            runtime_and_dependencies_reviewed=args.confirm_runtime_and_dependencies_reviewed)
    except Exception:
        print(json.dumps({'status':'DEPENDENCY_SETUP_REJECTED','activation':'BLOCKED',
            'reason':'Check selected profiles, source identity, qualified runtime and private destination; no service was activated.'}));return 2
    print(json.dumps(result));return 0 if result['status']=='SELECTED_PYTHON_DEPENDENCIES_PREPARED_NOT_ACTIVATED' else 2


if __name__=='__main__':raise SystemExit(main())
