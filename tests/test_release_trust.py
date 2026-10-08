import base64
import hashlib
import json
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from update_center.trust import signing_bytes,verify_signed_release


def signed(**changes):
    key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes_raw();identity=hashlib.sha256(public).hexdigest()
    payload={'release_id':'synthetic-1','sequence':3,'source_tree_sha256':'1'*64,'archive_sha256':'2'*64,
        'dependencies_sha256':'3'*64,'runtime_inventory_sha256':'4'*64,
        'issued_at':'2026-10-01T00:00:00Z','expires_at':'2026-11-01T00:00:00Z',**changes}
    envelope={'format_version':1,'key_id':identity,'payload':payload,'signature':base64.b64encode(key.sign(signing_bytes(payload))).decode()}
    args={'trusted_keys':{identity:public},'revoked_keys':set(),'minimum_sequence':2,'now':'2026-10-07T00:00:00Z',
        'expected_archive':'2'*64,'expected_source':'1'*64,'expected_dependencies':'3'*64,'expected_runtime_inventory':'4'*64}
    return envelope,args


def test_independent_key_signature_and_pins_do_not_grant_activation():
    envelope,args=signed();result=verify_signed_release(json.dumps(envelope).encode(),**args)
    assert result['status']=='SIGNATURE_VERIFIED_NOT_ACTIVATED' and result['activation']=='BLOCKED'
    assert result['local_policy_persistence']=='CALLER_REQUIRED'


@pytest.mark.parametrize('change',[{'trusted_keys':{}},{'minimum_sequence':4},{'minimum_sequence':True},
    {'now':'2026-09-01T00:00:00Z'},{'now':'2026-11-01T00:00:00Z'},{'now':'2026-10-07T00:00:00'},
    {'expected_archive':'0'*64},{'expected_source':'0'*64},{'expected_dependencies':'0'*64},{'expected_runtime_inventory':'0'*64}])
def test_unknown_signers_downgrades_time_and_artifact_substitution_rejected(change):
    envelope,args=signed()
    with pytest.raises(ValueError):verify_signed_release(json.dumps(envelope).encode(),**{**args,**change})


def test_revocation_overrides_valid_signature():
    envelope,args=signed();args['revoked_keys']={envelope['key_id']}
    with pytest.raises(ValueError,match='not trusted'):verify_signed_release(json.dumps(envelope).encode(),**args)


def test_modified_signed_claim_and_embedded_key_are_rejected():
    envelope,args=signed();envelope['payload']['release_id']='tampered'
    with pytest.raises(ValueError,match='signature'):verify_signed_release(json.dumps(envelope).encode(),**args)
    envelope,args=signed();envelope['public_key']='never-enroll-from-package'
    with pytest.raises(ValueError,match='envelope'):verify_signed_release(json.dumps(envelope).encode(),**args)


def test_duplicate_json_fields_rejected():
    envelope,args=signed();raw=json.dumps(envelope).replace('"format_version": 1','"format_version": 1, "format_version": 1')
    with pytest.raises(ValueError,match='Duplicate'):verify_signed_release(raw.encode(),**args)
