"""Local public-key trust administration; never installs or activates a release."""
import argparse
import json
from pathlib import Path
from installation import trust_policy as policy
from operations.time_integrity import utc_now, utc_text


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['initialize','inspect','enroll','revoke','review'])
    parser.add_argument('--root',required=True)
    parser.add_argument('--confirm-private-storage',action='store_true')
    parser.add_argument('--operator');parser.add_argument('--reason')
    parser.add_argument('--expected-revision',type=int)
    parser.add_argument('--public-key',help='Independently reviewed raw 32-byte public key file; never a private key.')
    parser.add_argument('--signed-envelope')
    for name in ['archive','source','dependencies','runtime-inventory']:
        parser.add_argument('--'+name+'-sha256')
    args=parser.parse_args(argv)
    storage={'private_storage_confirmed':args.confirm_private_storage}
    audit={'operator':args.operator,'reason':args.reason}
    try:
        if args.operation=='initialize':result=policy.initialize(args.root,**storage,**audit)
        elif args.operation=='inspect':result=policy.inspect(args.root,**storage)
        elif args.operation in {'enroll','revoke'}:
            with Path(args.public_key).open('rb') as f:key=f.read(33)
            result=policy.change_key(args.root,operation=args.operation.upper(),public_key=key,
                expected_revision=args.expected_revision,**storage,**audit)
        else:
            with Path(args.signed_envelope).open('rb') as f:document=f.read(16385)
            result=policy.review_release(args.root,document,expected_revision=args.expected_revision,
                now=utc_text(utc_now()),expected_archive=args.archive_sha256,expected_source=args.source_sha256,
                expected_dependencies=args.dependencies_sha256,expected_runtime_inventory=args.runtime_inventory_sha256,
                **storage,**audit)
    except Exception:
        print(json.dumps({'status':'TRUST_OPERATION_REJECTED','activation':'BLOCKED',
                          'reason':'Review policy integrity, private storage, current revision, independent key provenance and signed identities.'}))
        return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
