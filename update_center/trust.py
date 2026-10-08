"""Detached release verification against independently provisioned public keys.

No trust-on-first-use, embedded-key enrollment, key generation or activation.
The caller must protect the local key/revocation policy and sequence floor.
Passing a signature check authenticates the signed claims, not their safety.
"""
import base64
import binascii
import hashlib
import json
import re

from compatibility.manifest import _unique_object
from operations.backup import _sha
from operations.time_integrity import aware_utc

DOMAIN=b'chief-release-v1\0'
FIELDS={'release_id','sequence','source_tree_sha256','archive_sha256','dependencies_sha256','runtime_inventory_sha256','issued_at','expires_at'}


def signing_bytes(payload):
    return DOMAIN+json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def verify_signed_release(document,*,trusted_keys,revoked_keys,minimum_sequence,now,
                          expected_archive,expected_source,expected_dependencies,expected_runtime_inventory):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(document,bytes) or len(document)>16384:raise ValueError('Signed release envelope exceeds limit.')
    if type(minimum_sequence) is not int or minimum_sequence<0:raise ValueError('Explicit trusted sequence floor required.')
    if not isinstance(trusted_keys,dict) or not isinstance(revoked_keys,(set,frozenset)):
        raise ValueError('Independent key and revocation policy required.')
    envelope=json.loads(document,object_pairs_hook=_unique_object)
    if not isinstance(envelope,dict) or set(envelope)!={'format_version','key_id','payload','signature'} or type(envelope['format_version']) is not int or envelope['format_version']!=1:
        raise ValueError('Unsupported signed release envelope.')
    key_id=envelope['key_id'];_sha(key_id)
    if key_id in revoked_keys or key_id not in trusted_keys:raise ValueError('Release signer is not trusted.')
    key=trusted_keys[key_id]
    if not isinstance(key,bytes) or len(key)!=32 or hashlib.sha256(key).hexdigest()!=key_id:
        raise ValueError('Trusted key fingerprint differs.')
    payload=envelope['payload']
    if not isinstance(payload,dict) or set(payload)!=FIELDS:raise ValueError('Invalid signed release claims.')
    if not isinstance(payload['release_id'],str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}',payload['release_id']):
        raise ValueError('Invalid release identity.')
    if type(payload['sequence']) is not int or not minimum_sequence<=payload['sequence']<=2147483647:
        raise ValueError('Release is below the trusted sequence floor.')
    for key,expected in (('archive_sha256',expected_archive),('source_tree_sha256',expected_source),
                         ('dependencies_sha256',expected_dependencies),('runtime_inventory_sha256',expected_runtime_inventory)):
        _sha(expected);_sha(payload[key])
        if payload[key]!=expected:raise ValueError('Signed artifact identity differs.')
    issued=aware_utc(payload['issued_at']);expires=aware_utc(payload['expires_at']);current=aware_utc(now)
    if not issued<=current<expires or issued>=expires:
        raise ValueError('Release validity window is not current.')
    try:
        signature=base64.b64decode(envelope['signature'],validate=True)
        if len(signature)!=64:raise ValueError('Invalid signature length.')
        Ed25519PublicKey.from_public_bytes(trusted_keys[key_id]).verify(signature,signing_bytes(payload))
    except (InvalidSignature,ValueError,TypeError,binascii.Error) as exc:
        raise ValueError('Release signature verification failed.') from None
    return {'status':'SIGNATURE_VERIFIED_NOT_ACTIVATED','key_id':key_id,'release_id':payload['release_id'],
            'sequence':payload['sequence'],'source_tree_sha256':expected_source,'archive_sha256':expected_archive,
            'signed_envelope_sha256':hashlib.sha256(document).hexdigest(),'activation':'BLOCKED',
            'local_policy_persistence':'CALLER_REQUIRED'}
