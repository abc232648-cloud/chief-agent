import hashlib
import pytest
from identity import passwords
from identity.contracts import AuthenticationRequired
from tests.checkpoint_f_fixture import e_base,f_fixture,PASSWORD


def legacy(password):
    salt='11'*16
    return salt+':'+hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,maxmem=64*1024*1024).hex()


def test_versioned_hash_and_bounded_parameters():
    encoded=passwords.password_hash(PASSWORD)
    assert passwords.check_password(PASSWORD,encoded)
    assert not passwords.needs_upgrade(encoded)
    assert not passwords.check_password('Wrong-fixture-password',encoded)
    assert not passwords.check_password(PASSWORD,encoded.replace('m=65536','m=999999999'))
    assert not passwords.check_password(PASSWORD,encoded.replace('t=3','t=999999999'))


@pytest.mark.parametrize('stored',['','unknown:format','chief-v2:$broken',None,'x'*1025])
def test_invalid_stored_formats_fail_closed(stored):
    assert not passwords.check_password(PASSWORD,stored)


def test_legacy_upgrade_only_after_success(f_fixture):
    f=f_fixture;old=legacy(PASSWORD)
    with f.store._connect() as con:con.execute('UPDATE human_identities SET password_hash=? WHERE id=?',(old,f.owner.id))
    with pytest.raises(AuthenticationRequired):f.identity.login('owner','Wrong-fixture-password')
    with f.store._connect() as con:assert con.execute('SELECT password_hash FROM human_identities WHERE id=?',(f.owner.id,)).fetchone()[0]==old
    raw,_=f.identity.login('owner',PASSWORD)
    f.identity.authenticate(raw)
    with f.store._connect() as con:
        new=con.execute('SELECT password_hash FROM human_identities WHERE id=?',(f.owner.id,)).fetchone()[0]
        assert passwords.check_password(PASSWORD,new) and not passwords.needs_upgrade(new)
        assert con.execute("SELECT COUNT(*) FROM human_security_events WHERE operation='PASSWORD_HASH_UPGRADED'").fetchone()[0]==1


def test_kdf_concurrency_has_a_bound():
    assert passwords._SLOTS.acquire(timeout=0)
    assert passwords._SLOTS.acquire(timeout=0)
    try:
        with pytest.raises(PermissionError,match='busy'):passwords.password_hash(PASSWORD)
    finally:
        passwords._SLOTS.release();passwords._SLOTS.release()


def test_unknown_names_share_bounded_lockout(f_fixture):
    f=f_fixture
    for n in range(7):
        with pytest.raises(AuthenticationRequired):f.identity.login('unknown-'+str(n),PASSWORD)
    with f.store._connect() as con:assert con.execute('SELECT COUNT(*) FROM human_login_limits').fetchone()[0]==1
    f.identity.login('owner',PASSWORD)
