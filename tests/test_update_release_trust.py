import base64
from dataclasses import asdict, replace
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tests.test_update_center import candidate
from tests.test_update_staging import package
from update_center.release_trust import (
    PublisherKey, ReleaseRejected, SIGNING_CONTEXT, canonical, verify_release, verify_artifact,
)

NOW = '2026-10-13T12:00:00Z'


@pytest.fixture
def release():
    private = Ed25519PrivateKey.generate()
    key = PublisherKey('publisher-1', private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
                       '2026-10-01T00:00:00Z', '2027-01-01T00:00:00Z')
    data, pins = package()
    manifest = asdict(replace(candidate(), source_tree_sha256=pins['expected_source']))
    manifest['capability_versions'] = [dict(id=k, version=v) for k, v in manifest['capability_versions']]
    payload = dict(format_version=1, product='chief', repository='abc232648-cloud/chief-agent',
                   channel='stable', sequence=2, commit='a'*40,
                   archive_sha256=pins['expected_archive'], archive_size=len(data), manifest=manifest,
                   targets=[dict(os=name, architecture='x86_64', python_minimum='3.12.0', python_before='3.15.0')
                            for name in ('windows', 'linux')],
                   issued_at='2026-10-13T11:00:00Z', expires_at='2026-10-14T11:00:00Z')
    def sign(value):
        signature = private.sign(SIGNING_CONTEXT + canonical(value))
        return canonical(dict(key_id=key.key_id, payload=value, signature=base64.b64encode(signature).decode()))
    return key, payload, sign, data


def test_signed_identity_and_complete_artifact_are_separate_checks(release):
    key, payload, sign, data = release
    envelope = sign(payload)
    result = verify_artifact(data, envelope, [key], minimum_sequence=1, now=NOW)
    assert result.sequence == 2 and result.commit == 'a'*40
    assert result.manifest.source_tree_sha256 == payload['manifest']['source_tree_sha256']
    assert result == verify_release(envelope, [key], minimum_sequence=2, now=NOW)
    with pytest.raises(ReleaseRejected):
        verify_artifact(data[:-1], envelope, [key], minimum_sequence=1, now=NOW)
    changed = data[:-1] + bytes([data[-1] ^ 1])
    with pytest.raises(ReleaseRejected):
        verify_artifact(changed, envelope, [key], minimum_sequence=1, now=NOW)


@pytest.mark.parametrize('field,value', [
    ('product', 'staff'), ('product', 'farming'), ('repository', 'other/chief-agent'),
    ('channel', 'preview'), ('format_version', True), ('sequence', True), ('sequence', 0),
    ('commit', 'main'), ('commit', 'a'*39), ('archive_size', True), ('archive_size', 0),
    ('archive_size', 65*1024*1024), ('archive_sha256', 'not-a-hash'),
    ('issued_at', '2026-10-13T12:00:01Z'), ('issued_at', '2026-10-13T11:00:00'),
    ('expires_at', NOW), ('expires_at', '2027-12-01T00:00:00Z'),
    ('download_url', 'http://127.0.0.1/private'),
    ('targets', []), ('targets', [{'os': 'android'}]),
])
def test_even_signed_invalid_or_out_of_scope_metadata_is_rejected(release, field, value):
    key, payload, sign, _ = release
    with pytest.raises(ReleaseRejected, match='could not be verified'):
        verify_release(sign({**payload, field: value}), [key], minimum_sequence=1, now=NOW)


def test_signature_tampering_unknown_revoked_and_expired_keys(release):
    key, payload, sign, _ = release
    envelope = sign(payload)
    for keys in ([], [replace(key, revoked=True)], [replace(key, valid_until=NOW)], [key, key],
                 [replace(key, public_key=b'x'*32)], [replace(key, valid_from='2026-10-14T00:00:00Z')]):
        with pytest.raises(ReleaseRejected):
            verify_release(envelope, keys, minimum_sequence=1, now=NOW)
    raw = json.loads(envelope)
    raw['payload']['sequence'] = 10
    with pytest.raises(ReleaseRejected):
        verify_release(canonical(raw), [key], minimum_sequence=1, now=NOW)
    with pytest.raises(ReleaseRejected):
        verify_release(envelope, [key], minimum_sequence=3, now=NOW)


@pytest.mark.parametrize('raw', [b'{}', b'[]', b'null', b'\xff', b'x'*(256*1024+1)],
                         ids=['empty-object', 'array', 'null', 'invalid-utf8', 'oversized'])
def test_malformed_inputs_return_closed_error(raw):
    with pytest.raises(ReleaseRejected, match='could not be verified'):
        verify_release(raw, [], minimum_sequence=0, now=NOW)


def test_duplicate_fields_private_state_and_newer_creation_are_rejected(release):
    key, payload, sign, _ = release
    envelope = sign(payload)
    duplicate = b'{"key_id":"attacker",' + envelope[1:]
    with pytest.raises(ReleaseRejected):
        verify_release(duplicate, [key], minimum_sequence=1, now=NOW)
    for changes in ({'private_state_excluded': False}, {'created_at': '2026-10-13T11:30:00Z'}):
        value = {**payload, 'manifest': {**payload['manifest'], **changes}}
        with pytest.raises(ReleaseRejected):
            verify_release(sign(value), [key], minimum_sequence=1, now=NOW)


def test_artifact_check_rechecks_metadata_expiry(release):
    key, payload, sign, data = release
    with pytest.raises(ReleaseRejected):
        verify_artifact(data, sign(payload), [key], minimum_sequence=1, now=payload['expires_at'])
