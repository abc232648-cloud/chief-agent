"""Local operator source staging only. Does not install, migrate or start services."""
import argparse
import json
from pathlib import Path
from update_center.staging import MAX_ARCHIVE,stage_package


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive');parser.add_argument('--archive-sha256',required=True)
    parser.add_argument('--source-sha256',required=True);parser.add_argument('--staging-root',required=True)
    parser.add_argument('--confirm-private-storage',action='store_true')
    args=parser.parse_args(argv)
    try:
        with Path(args.archive).open('rb') as stream:data=stream.read(MAX_ARCHIVE+1)
        receipt=stage_package(data,args.staging_root,expected_archive=args.archive_sha256,
                              expected_source=args.source_sha256,private_storage_confirmed=args.confirm_private_storage)
    except Exception:
        # Do not print hostile ZIP names, paths, private content or exception text.
        print(json.dumps({'status':'STAGING_FAILED','installed':False,
                          'message':'Package checks or private staging storage failed. Existing installation unchanged.'}))
        return 2
    print(json.dumps(receipt,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
