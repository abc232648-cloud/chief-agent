"""Verify a Chief release against provisioned local publisher policy; stage inertly.

This operator command never adds trust keys, installs, changes services or migrates
data. The policy is provisioned separately; do not trust a key from the release.
"""
import argparse
import base64
import json
from pathlib import Path

from compatibility.manifest import _unique_object
from operations.backup import _plain
from update_center.release_trust import (
    REPOSITORY, MAX_ENVELOPE, PublisherKey, verify_release, verify_artifact,
)
from update_center.staging import MAX_ARCHIVE, stage_package


def read_bounded(filename, limit):
    path = Path(filename)
    if not path.is_absolute():
        raise ValueError('Absolute operator input required.')
    _plain(path, file=True)
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('Operator input exceeds limit.')
    return value


def load_policy(filename):
    value = json.loads(read_bounded(filename, 16384), object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {'version', 'repository', 'minimum_sequence', 'keys'}:
        raise ValueError('Invalid publisher policy.')
    if type(value['version']) is not int or value['version'] != 1 or value['repository'] != REPOSITORY:
        raise ValueError('Invalid publisher policy.')
    if type(value['minimum_sequence']) is not int or value['minimum_sequence'] < 0:
        raise ValueError('Invalid publisher policy.')
    if not isinstance(value['keys'], list) or not 1 <= len(value['keys']) <= 16:
        raise ValueError('Invalid publisher policy.')
    keys = []
    for item in value['keys']:
        if not isinstance(item, dict) or set(item) != {'key_id', 'public_key', 'valid_from', 'valid_until', 'revoked'}:
            raise ValueError('Invalid publisher key.')
        if type(item['revoked']) is not bool:
            raise ValueError('Invalid publisher key.')
        key = base64.b64decode(item['public_key'], validate=True)
        if len(key) != 32:
            raise ValueError('Invalid publisher key.')
        keys.append(PublisherKey(item['key_id'], key, item['valid_from'], item['valid_until'], item['revoked']))
    return tuple(keys), value['minimum_sequence']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--envelope', required=True)
    parser.add_argument('--trust-policy', required=True)
    parser.add_argument('--archive')
    parser.add_argument('--staging-root')
    parser.add_argument('--confirm-private-storage', action='store_true')
    parser.add_argument('--config', help='Existing prepared instance for durable release receipts; required for staging.')
    parser.add_argument('--current-manifest', help='Operator-supplied current release manifest for read-only compatibility planning.')
    args = parser.parse_args(argv)
    try:
        keys, floor = load_policy(args.trust_policy)
        envelope = read_bounded(args.envelope, MAX_ENVELOPE)
        if args.staging_root and (not args.archive or not args.confirm_private_storage or not args.config or not args.current_manifest):
            raise ValueError('Explicit private archive staging required.')
        data = read_bounded(args.archive, MAX_ARCHIVE) if args.archive else None
        release = (verify_artifact(data, envelope, keys, minimum_sequence=floor) if data is not None
                   else verify_release(envelope, keys, minimum_sequence=floor))
        if args.config:
            from deployment.launch import configure
            from update_center.storage import ExistingUpdateStore
            from update_center.release_history import verify_and_record
            instance = configure(args.config)
            release = verify_and_record(ExistingUpdateStore(instance.database), envelope, keys, minimum_sequence=floor)
        result = {'status': 'ARTIFACT_VERIFIED' if data is not None else 'PUBLISHER_VERIFIED',
                  'release_id': release.manifest.release_id, 'source_sha256': release.manifest.source_tree_sha256,
                  'archive_sha256': release.archive_sha256, 'envelope_sha256': release.envelope_sha256,
                  'sequence': release.sequence, 'installed': False, 'compatibility': 'NOT_EVALUATED',
                  'activation': 'NOT_AUTHORIZED'}
        result['replay_protection'] = 'PERSISTED_SEQUENCE' if args.config else 'LOCAL_POLICY_ONLY'
        if args.current_manifest:
            from compatibility.manifest import load_manifest
            from application.composition import default_catalogs
            from update_center.compatibility_check import assess
            current = load_manifest(read_bounded(args.current_manifest, MAX_ENVELOPE).decode('utf-8'))
            assessment = assess(envelope, keys, current, default_catalogs().capabilities, minimum_sequence=floor)
            result['compatibility'] = assessment['status']
            result['assessment'] = assessment
            if args.staging_root and assessment['blockers']:
                raise ValueError('Compatibility planning blocked this candidate.')
        if args.staging_root:
            receipt = stage_package(data, args.staging_root, expected_archive=release.archive_sha256,
                                    expected_source=release.manifest.source_tree_sha256,
                                    private_storage_confirmed=True)
            result.update(status='STAGED_NOT_INSTALLED', stage_id=receipt['stage_id'])
    except Exception:
        print(json.dumps({'status': 'RELEASE_REJECTED', 'installed': False,
                          'message': 'Publisher policy, release identity or private staging checks failed.'}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
