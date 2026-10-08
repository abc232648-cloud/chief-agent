import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from installation import trust_policy as p
from tests.test_release_trust import signed

AUDIT={'operator':'synthetic-operator','reason':'Independent synthetic key review','private_storage_confirmed':True}


def root(tmp_path):
    r=tmp_path/'policy';r.mkdir(mode=0o700)
    p.initialize(r,**AUDIT);return r


def enroll(r,envelope,args,revision=1):
    public=args['trusted_keys'][envelope['key_id']]
    p.change_key(r,operation='ENROLL',public_key=public,expected_revision=revision,**AUDIT)
    return public


def review(r,envelope,args,revision):
    kw={k:v for k,v in args.items() if k not in {'trusted_keys','revoked_keys','minimum_sequence'}}
    return p.review_release(r,json.dumps(envelope).encode(),expected_revision=revision,**kw,**AUDIT)


def test_enrollment_review_floor_and_revocation_survive_reopen(tmp_path):
    r=root(tmp_path);e,a=signed();pub=enroll(r,e,a)
    result=review(r,e,a,2)
    assert result['activation']=='BLOCKED' and result['status']=='RELEASE_REVIEW_RECORDED_NOT_ACTIVATED'
    state=p.inspect(r,private_storage_confirmed=True);assert state['minimum_sequence']==3 and state['revision']==3
    p.change_key(r,operation='REVOKE',public_key=pub,expected_revision=3,**AUDIT)
    with pytest.raises(ValueError,match='not trusted'):review(r,e,a,4)
    with pytest.raises(ValueError):p.change_key(r,operation='ENROLL',public_key=pub,expected_revision=4,**AUDIT)
    assert p.inspect(r,private_storage_confirmed=True)['minimum_sequence']==3


def test_missing_or_interrupted_policy_is_not_initialized(tmp_path):
    r=tmp_path/'policy';r.mkdir(mode=0o700)
    with pytest.raises(FileNotFoundError):p.inspect(r,private_storage_confirmed=True)
    (r/p.NAME).touch(mode=0o600)
    with pytest.raises(sqlite3.DatabaseError):p.inspect(r,private_storage_confirmed=True)
    with pytest.raises(FileExistsError):p.initialize(r,**AUDIT)


def test_two_administrators_cannot_apply_stale_policy(tmp_path):
    r=root(tmp_path);e,a=signed();pub=a['trusted_keys'][e['key_id']]
    def act(_):
        try:return p.change_key(r,operation='ENROLL',public_key=pub,expected_revision=1,**AUDIT)['status']
        except ValueError:return 'STALE'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(act,range(2)))
    assert sorted(results)==['POLICY_RECORDED','STALE']


def test_corrupted_history_refuses_read_or_enrollment(tmp_path):
    r=root(tmp_path)
    with sqlite3.connect(r/p.NAME) as con:con.execute("UPDATE policy_events SET document='{}'")
    with pytest.raises(ValueError):p.inspect(r,private_storage_confirmed=True)


def test_transaction_interruption_leaves_previous_policy(tmp_path,monkeypatch):
    r=root(tmp_path);e,a=signed();before=p.inspect(r,private_storage_confirmed=True)
    original=p._append
    def fail(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('Synthetic interruption')
    monkeypatch.setattr(p,'_append',fail)
    with pytest.raises(RuntimeError):enroll(r,e,a)
    assert p.inspect(r,private_storage_confirmed=True)==before


def test_key_rotation_retains_floor_and_blocks_sequence_rebinding(tmp_path):
    r=root(tmp_path);e,a=signed();pub=enroll(r,e,a);review(r,e,a,2)
    e2,a2=signed();enroll(r,e2,a2,3)
    with pytest.raises(ValueError,match='already bound'):review(r,e2,a2,4)
    old,aold=signed(sequence=2);enroll(r,old,aold,4)
    with pytest.raises(ValueError,match='floor'):review(r,old,aold,5)
    assert p.inspect(r,private_storage_confirmed=True)['minimum_sequence']==3


def test_expired_or_wrong_artifact_does_not_change_policy(tmp_path):
    r=root(tmp_path);e,a=signed();enroll(r,e,a);before=p.inspect(r,private_storage_confirmed=True)
    with pytest.raises(ValueError):review(r,e,{**a,'now':'2027-01-01T00:00:00Z'},2)
    with pytest.raises(ValueError):review(r,e,{**a,'expected_archive':'0'*64},2)
    assert p.inspect(r,private_storage_confirmed=True)==before


@pytest.mark.skipif(os.name=='nt',reason='POSIX privacy/symlink fixture; Windows ACL qualification remains separate')
def test_shared_policy_root_and_link_rejected(tmp_path):
    r=tmp_path/'policy';r.mkdir(mode=0o755)
    with pytest.raises(ValueError,match='private'):p.initialize(r,**AUDIT)
    r.chmod(0o700);p.initialize(r,**AUDIT)
    link=tmp_path/'linked';link.symlink_to(r,target_is_directory=True)
    with pytest.raises(ValueError,match='links'):p.inspect(link,private_storage_confirmed=True)


def test_cli_requires_explicit_operator_and_private_storage(tmp_path,capsys):
    from application.release_trust import main
    r=tmp_path/'policy';r.mkdir(mode=0o700)
    assert main(['initialize','--root',str(r)])==2
    assert not (r/p.NAME).exists()
    assert main(['initialize','--root',str(r),'--operator','synthetic','--reason','Local review','--confirm-private-storage'])==0
    assert main(['inspect','--root',str(r),'--confirm-private-storage'])==0
    assert 'BLOCKED' in capsys.readouterr().out
