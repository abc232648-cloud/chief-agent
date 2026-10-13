"""Chief-only signed release identities. Verification grants no execution authority.

Trusted keys and the replay floor come from local installation policy, never
from a downloaded envelope. Signing belongs to the publisher, not this client.
"""
import base64
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from compatibility.contracts import ReleaseManifest
from compatibility.manifest import _unique_object, load_manifest
from operations.time_integrity import aware_utc, utc_now, utc_text
from update_center.staging import MAX_ARCHIVE, inspect_package

REPOSITORY = 'abc232648-cloud/chief-agent'
MAX_ENVELOPE = 256 * 1024
SIGNING_CONTEXT = b'chief-release-envelope-v1\x00'


class ReleaseRejected(ValueError):
    """Closed error; do not echo hostile metadata or crypto exception details."""


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


@dataclass(frozen=True)
class PublisherKey:
    key_id: str
    public_key: bytes
    valid_from: str
    valid_until: str
    revoked: bool = False


@dataclass(frozen=True)
class ReleaseTarget:
    os: str
    architecture: str
    python_minimum: str
    python_before: str


@dataclass(frozen=True)
class VerifiedRelease:
    envelope_sha256: str
    key_id: str
    sequence: int
    commit: str
    archive_sha256: str
    archive_size: int
    manifest: ReleaseManifest
    verified_at: str
    expires_at: str
    targets: tuple[ReleaseTarget, ...]


def python_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'3\.(?:0|[1-9][0-9]?)\.(?:0|[1-9][0-9]{0,2})', value):
        raise ValueError('Invalid supported Python version.')
    return tuple(int(part) for part in value.split('.'))


def _targets(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 8:
        raise ValueError()
    targets, pairs = [], set()
    for item in values:
        if not isinstance(item, dict) or set(item) != {'os', 'architecture', 'python_minimum', 'python_before'}:
            raise ValueError()
        target = ReleaseTarget(**item)
        pair = (target.os, target.architecture)
        if target.os not in {'windows', 'linux'} or target.architecture not in {'x86_64', 'aarch64'} or pair in pairs:
            raise ValueError()
        if not python_version(target.python_minimum) < python_version(target.python_before):
            raise ValueError()
        pairs.add(pair)
        targets.append(target)
    return tuple(targets)


def verify_release(data, trusted_keys, *, minimum_sequence, now=None):
    """Verify a bounded envelope against already provisioned publisher keys.

The replay floor is the highest accepted sequence, persisted by the coordinator.
A sequence equal to that floor may be reverified, but must not be reactivated.
The caller must bind a sequence to its exact envelope digest when recording it.
"""
    try:
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ENVELOPE:
            raise ValueError()
        if type(minimum_sequence) is not int or minimum_sequence < 0:
            raise ValueError()
        envelope = json.loads(data, object_pairs_hook=_unique_object)
        if not isinstance(envelope, dict) or set(envelope) != {'key_id', 'payload', 'signature'}:
            raise ValueError()
        key_id = envelope['key_id']
        if not isinstance(key_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', key_id):
            raise ValueError()
        keys = tuple(trusted_keys)
        if not all(isinstance(k, PublisherKey) for k in keys):
            raise ValueError()
        if len({k.key_id for k in keys}) != len(keys):
            raise ValueError()
        key = next(k for k in keys if k.key_id == key_id)
        current = aware_utc(now if now is not None else utc_now())
        if key.revoked is not False or not aware_utc(key.valid_from) <= current < aware_utc(key.valid_until):
            raise ValueError()
        signature = base64.b64decode(envelope['signature'], validate=True)
        if len(signature) != 64:
            raise ValueError()
        payload = envelope['payload']
        Ed25519PublicKey.from_public_bytes(key.public_key).verify(
            signature, SIGNING_CONTEXT + canonical(payload))
        fields = {'format_version', 'product', 'repository', 'channel', 'sequence',
                  'commit', 'archive_sha256', 'archive_size', 'manifest', 'issued_at', 'expires_at', 'targets'}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError()
        if type(payload['format_version']) is not int or payload['format_version'] != 1:
            raise ValueError()
        if (payload['product'] != 'chief' or payload['repository'] != REPOSITORY
                or payload['channel'] != 'stable'):
            raise ValueError()
        sequence = payload['sequence']
        if type(sequence) is not int or sequence < max(1, minimum_sequence):
            raise ValueError()
        if not isinstance(payload['commit'], str) or not re.fullmatch('[0-9a-f]{40}', payload['commit']):
            raise ValueError()
        if not isinstance(payload['archive_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', payload['archive_sha256']):
            raise ValueError()
        size = payload['archive_size']
        if type(size) is not int or not 0 < size <= MAX_ARCHIVE:
            raise ValueError()
        issued, expires = aware_utc(payload['issued_at']), aware_utc(payload['expires_at'])
        if not issued <= current < expires or not timedelta(0) < expires-issued <= timedelta(days=31):
            raise ValueError()
        if not aware_utc(key.valid_from) <= issued < aware_utc(key.valid_until):
            raise ValueError()
        manifest = load_manifest(payload['manifest'])
        targets = _targets(payload['targets'])
        if not manifest.private_state_excluded or aware_utc(manifest.created_at) > issued:
            raise ValueError()
        return VerifiedRelease(hashlib.sha256(canonical(envelope)).hexdigest(), key_id,
                               sequence, payload['commit'], payload['archive_sha256'], size,
                               manifest, utc_text(current), utc_text(expires), targets)
    except (ValueError, TypeError, KeyError, StopIteration, InvalidSignature, UnsupportedAlgorithm, OverflowError, RecursionError):
        raise ReleaseRejected('Chief release identity could not be verified.') from None


def verify_artifact(data, envelope, trusted_keys, *, minimum_sequence, now=None):
    """Reverify trust/freshness and all ZIP bytes before passing to inert staging."""
    release = verify_release(envelope, trusted_keys, minimum_sequence=minimum_sequence, now=now)
    if not isinstance(data, bytes) or len(data) != release.archive_size:
        raise ReleaseRejected('Chief release artifact identity differs.')
    try:
        inspect_package(data, expected_archive=release.archive_sha256,
                        expected_source=release.manifest.source_tree_sha256)
    except Exception:
        raise ReleaseRejected('Chief release artifact identity differs.') from None
    return release
