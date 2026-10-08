"""Operator CLI: verified inert source preparation only."""
import argparse
import json
import zipfile
from pathlib import Path

from installation.preparation import prepare_source
from update_center.staging import MAX_ARCHIVE


def main(argv=None):
    parser=argparse.ArgumentParser(description='Prepare verified Chief source without running or activating it.')
    parser.add_argument('archive')
    parser.add_argument('--archive-sha256',required=True)
    parser.add_argument('--source-sha256',required=True)
    parser.add_argument('--installation-root',required=True)
    parser.add_argument('--confirm-private-storage',action='store_true')
    args=parser.parse_args(argv)
    try:
        with Path(args.archive).open('rb') as stream:data=stream.read(MAX_ARCHIVE+1)
        receipt=prepare_source(data,args.installation_root,expected_archive=args.archive_sha256,
            expected_source=args.source_sha256,private_storage_confirmed=args.confirm_private_storage)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,zipfile.BadZipFile):
        # No filesystem paths, credentials or archive payloads in diagnostics.
        print(json.dumps({'status':'PREPARATION_FAILED','reason':'Check package pins, supported manifest and private storage. No activation occurred.'}))
        return 2
    print(json.dumps(receipt));return 0


if __name__=='__main__':raise SystemExit(main())
