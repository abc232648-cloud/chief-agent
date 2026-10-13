"""Interactive local Chief update review; no passwords or session tokens in args.

Uses an existing prepared database only. Selection requires stopped managed
services and a fresh B.5 backup/quarantined restore. Never restarts services.
"""
import argparse
import getpass
import json

from application.composition import default_catalogs
from update_center.local_inputs import load_policy, read_bounded
from compatibility.manifest import load_manifest
from database.store import Store
from identity.service import IdentityService
from application.update_coordinator import ManualUpdater
from update_center.managed import ManagedInstallation
from update_center.release_trust import MAX_ENVELOPE
from update_center.staging import MAX_ARCHIVE
from update_center.operator_policy import OperatorUpdatePolicy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['validate', 'apply', 'recover'])
    parser.add_argument('--installation', required=True)
    parser.add_argument('--current-manifest', required=True)
    parser.add_argument('--trust-policy', required=True)
    parser.add_argument('--update-policy', required=True)
    parser.add_argument('--test-config', help='Optional pinned browser runtime and built companion-client paths.')
    parser.add_argument('--private-work-root', required=True)
    parser.add_argument('--candidate')
    parser.add_argument('--run-id')
    parser.add_argument('--archive')
    parser.add_argument('--envelope')
    parser.add_argument('--confirm-private-storage', action='store_true')
    args = parser.parse_args(argv)
    authentication = raw = None
    try:
        if not args.confirm_private_storage:
            raise ValueError('Private operator storage confirmation is required.')
        if args.operation != 'validate' and (args.archive or args.envelope):
            raise ValueError('Import packages only during validation.')
        if args.operation == 'apply' and not args.run_id:
            raise ValueError('Apply requires a server-owned validation reference.')
        if bool(args.archive) != bool(args.envelope):
            raise ValueError('Archive and signed envelope are required together.')
        installation = ManagedInstallation(args.installation)
        current = load_manifest(read_bounded(args.current_manifest, MAX_ENVELOPE).decode('utf-8'))
        # The CLI requires explicit human review; it does not convert planning or
        # model recommendations into an ALLOW policy or grant any agent powers.
        policy = OperatorUpdatePolicy(args.update_policy)
        updater = ManualUpdater(installation, current, default_catalogs().capabilities, policy,
            trust_policy=args.trust_policy, evidence_parent=args.private_work_root,
            backup_parent=args.private_work_root, restore_parent=args.private_work_root, test_config=args.test_config)
        authentication = IdentityService(Store(updater.instance.database, initialize=False,
                                               expected_schema=current.schema_sha256))
        username = input('Chief Owner or Administrator username: ')
        password = getpass.getpass('Password (private; never saved in update output): ')
        try:
            raw, principal = authentication.login(username, password)
        finally:
            password = None
        authentication.authorize(principal, 'installation.manage', sensitive=True)
        candidate = args.candidate
        if args.operation == 'validate':
            if input('Type VALIDATE to execute the reviewed signed candidate tests: ').strip() != 'VALIDATE':
                raise PermissionError('Validation was not confirmed.')
            if args.archive:
                keys, floor = load_policy(args.trust_policy)
                staged = installation.stage(read_bounded(args.archive, MAX_ARCHIVE),
                    read_bounded(args.envelope, MAX_ENVELOPE), keys, minimum_sequence=floor)
                if candidate and candidate != staged.manifest.source_tree_sha256:
                    raise ValueError('Explicit candidate differs from imported release.')
                candidate = staged.manifest.source_tree_sha256
            result = updater.validate(principal, candidate, confirm_reviewed_code=True)
        elif args.operation == 'apply':
            print('This selects Chief source only. Managed services must already be stopped. No schema migration or automatic restart occurs.')
            print('Candidate source: ' + str(candidate))
            if input('Type APPLY to review and select this validated source: ').strip() != 'APPLY':
                raise PermissionError('Source selection was not confirmed.')
            decision = updater.approve(principal, candidate, args.run_id)
            result = updater.select(principal, candidate, decision['id'], private_storage_confirmed=True)
        else:
            print('Recovery selects only the pinned previous source from an interrupted switch. Operational data is not restored.')
            if input('Type RECOVER to select the previous source: ').strip() != 'RECOVER':
                raise PermissionError('Recovery was not confirmed.')
            result = updater.recover_previous(principal, candidate, confirm_recovery=True, private_storage_confirmed=True)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] != 'FAILED' and result['status'] not in {'TIMED_OUT', 'INVALID_REPORT'} else 2
    except Exception:
        print(json.dumps({'status':'UPDATE_NOT_COMPLETED', 'message':'An identity, trust, compatibility, acceptance or maintenance requirement was not met. Existing operational data was not restored.'}))
        return 2
    finally:
        if authentication is not None and raw is not None:
            try:
                authentication.revoke_token(raw)
            except Exception:
                print(json.dumps({'status':'SESSION_CLEANUP_FAILED', 'message':'The local sign-in could not be revoked. Review local identity storage before another update attempt.'}))
                return 2


if __name__ == '__main__':
    raise SystemExit(main())
