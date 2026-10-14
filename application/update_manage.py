"""Interactive local Chief update review with optional verified acquisition and Linux restart.

Passwords/session tokens never appear in arguments. Downloaded releases remain
in private staging until publisher verification succeeds. Dependency-changing
candidates receive an isolated release runtime before tests. Automatic service
control is limited to fixed Linux systemd-user units and rolls back the exact
completed source selection if fresh READY state cannot be reached.
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
    parser.add_argument('--archive-url')
    parser.add_argument('--envelope-url')
    parser.add_argument('--allowed-host', action='append', default=[])
    parser.add_argument('--restart-linux-services', action='store_true')
    parser.add_argument('--confirm-private-storage', action='store_true')
    args = parser.parse_args(argv)
    authentication = raw = None
    cleanup_failed = False
    exit_code = 2
    try:
        if not args.confirm_private_storage:
            raise ValueError('Private operator storage confirmation is required.')
        if args.operation != 'validate' and (args.archive or args.envelope or args.archive_url or args.envelope_url or args.allowed_host):
            raise ValueError('Release import or acquisition is valid only during validation.')
        if args.operation == 'validate' and args.restart_linux_services:
            raise ValueError('Validation never restarts managed services.')
        if args.operation == 'apply' and not args.run_id:
            raise ValueError('Apply requires a server-owned validation reference.')
        if bool(args.archive) != bool(args.envelope):
            raise ValueError('Archive and signed envelope are required together.')
        if bool(args.archive_url) != bool(args.envelope_url):
            raise ValueError('Archive and envelope URLs are required together.')
        if args.archive and args.archive_url:
            raise ValueError('Choose local release files or approved HTTPS acquisition, not both.')
        if args.archive_url and not args.allowed_host:
            raise ValueError('HTTPS release acquisition requires at least one explicit allowed host.')
        installation = ManagedInstallation(args.installation)
        current = load_manifest(read_bounded(args.current_manifest, MAX_ENVELOPE).decode('utf-8'))
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
            print('Validation may download a reviewed release and prepare an isolated dependency runtime before executing candidate tests.')
            if input('Type VALIDATE to execute the reviewed signed candidate tests: ').strip() != 'VALIDATE':
                raise PermissionError('Validation was not confirmed.')
            archive_path, envelope_path = args.archive, args.envelope
            if args.archive_url:
                from update_center.acquisition import acquire_release
                acquired = acquire_release(args.archive_url, args.envelope_url, args.private_work_root,
                    allowed_hosts=args.allowed_host, private_storage_confirmed=True)
                archive_path, envelope_path = acquired['archive'], acquired['envelope']
            if archive_path:
                keys, floor = load_policy(args.trust_policy)
                staged = installation.stage(read_bounded(archive_path, MAX_ARCHIVE),
                    read_bounded(envelope_path, MAX_ENVELOPE), keys, minimum_sequence=floor)
                if candidate and candidate != staged.manifest.source_tree_sha256:
                    raise ValueError('Explicit candidate differs from imported release.')
                candidate = staged.manifest.source_tree_sha256
            if not candidate:
                raise ValueError('Validation requires a staged candidate or signed release package.')
            result = updater.validate(principal, candidate, confirm_reviewed_code=True)
        elif args.operation == 'apply':
            if args.restart_linux_services:
                print('This will stop the fixed Chief Linux user services, select the validated source, and require fresh READY state after restart.')
            else:
                print('This selects Chief source only. Managed services must already be stopped; no automatic restart occurs.')
            print('Candidate source: ' + str(candidate))
            if input('Type APPLY to review and select this validated source: ').strip() != 'APPLY':
                raise PermissionError('Source selection was not confirmed.')
            decision = updater.approve(principal, candidate, args.run_id)
            if not args.restart_linux_services:
                result = updater.select(principal, candidate, decision['id'], private_storage_confirmed=True)
            else:
                from deployment.linux_service_control import stop_services, start_services
                stop_services()
                try:
                    result = updater.select(principal, candidate, decision['id'], private_storage_confirmed=True)
                except BaseException:
                    try:
                        start_services(updater.instance)
                    except Exception:
                        pass
                    raise
                try:
                    start_services(updater.instance)
                    result = {**result, 'status': 'SOURCE_SELECTED_SERVICES_READY',
                              'services_started': True, 'deployment_certified': False}
                except Exception:
                    stop_services()
                    rollback = updater.rollback_after_activation_failure(
                        principal, candidate, private_storage_confirmed=True)
                    start_services(updater.instance)
                    result = {**rollback, 'status': 'ACTIVATION_ROLLED_BACK',
                              'services_started': True, 'deployment_certified': False}
        else:
            if args.restart_linux_services:
                print('Recovery will stop the fixed Chief Linux user services, select the exact previous source, and restart it.')
            else:
                print('Recovery selects only the pinned previous source from an interrupted switch. Operational data is not restored.')
            if input('Type RECOVER to select the previous source: ').strip() != 'RECOVER':
                raise PermissionError('Recovery was not confirmed.')
            if args.restart_linux_services:
                from deployment.linux_service_control import stop_services, start_services
                stop_services()
            result = updater.recover_previous(principal, candidate, confirm_recovery=True, private_storage_confirmed=True)
            if args.restart_linux_services:
                start_services(updater.instance)
                result = {**result, 'status': 'PREVIOUS_SOURCE_SELECTED_SERVICES_READY',
                          'services_started': True, 'deployment_certified': False}
        print(json.dumps(result, sort_keys=True))
        failed = result['status'] in {'FAILED', 'TIMED_OUT', 'INVALID_REPORT', 'ACTIVATION_ROLLED_BACK'}
        exit_code = 2 if failed else 0
    except Exception:
        print(json.dumps({'status':'UPDATE_NOT_COMPLETED', 'message':'An identity, trust, compatibility, acceptance, dependency, service or maintenance requirement was not met. Existing operational data was not restored.'}))
        exit_code = 2
    finally:
        if authentication is not None and raw is not None:
            try:
                authentication.revoke_token(raw)
            except Exception:
                cleanup_failed = True
                print(json.dumps({'status':'SESSION_CLEANUP_FAILED', 'message':'The local sign-in could not be revoked. Review local identity storage before another update attempt.'}))
    if cleanup_failed:
        return 2
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
