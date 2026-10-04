import pytest
from identity import recovery
from identity.contracts import AuthenticationRequired
from tests.checkpoint_f_fixture import e_base,f_fixture,PASSWORD,recovery_point


def test_non_admin_recovery_is_denied_before_target_access(tmp_path,monkeypatch):
    monkeypatch.setattr(recovery,'is_os_administrator',lambda:False)
    with pytest.raises(PermissionError,match='administrator'):
        recovery.recover_owner(tmp_path/'missing.db','owner','missing',PASSWORD)
    assert not (tmp_path/'missing.db').exists()


def test_authorized_recovery_revokes_sessions_and_preserves_work(f_fixture,monkeypatch):
    # OS authorization is mocked only in this isolated unit fixture. This is not
    # a claim that an actual administrative recovery drill was executed.
    f=f_fixture;monkeypatch.setattr(recovery,'is_os_administrator',lambda:True)
    f.identity.login('owner',PASSWORD);command=f.store.queue_command('Synthetic never execute')
    point=recovery_point(f.store,f.root)
    with f.store._connect() as con:
        schema=[tuple(r) for r in con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name')]
    result=recovery.recover_owner(f.store.path.resolve(),'owner',f.owner.id,'Replacement-fixture-password')
    assert result['sessions_revoked']==2 and result['schema_migration'] is False
    with pytest.raises(AuthenticationRequired):f.identity.authenticate(f.raw)
    with pytest.raises(AuthenticationRequired):f.identity.login('owner',PASSWORD)
    f.identity.login('owner','Replacement-fixture-password')
    with f.store._connect() as con:
        assert con.execute('SELECT status FROM commands WHERE id=?',(command,)).fetchone()[0]=='QUEUED'
        assert schema==[tuple(r) for r in con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name')]
        event=con.execute("SELECT * FROM human_security_events WHERE operation='OWNER_RECOVERED'").fetchone()
        assert event['human_id']==f.owner.id and event['session_id'] is None
    with pytest.raises(PermissionError):recovery.recover_owner(point['restore_directory']/'state.sqlite3','owner',f.owner.id,PASSWORD)


@pytest.mark.parametrize('failure',['identity','username','role'])
def test_wrong_recovery_target_cannot_change_identity(f_fixture,monkeypatch,failure):
    f=f_fixture;monkeypatch.setattr(recovery,'is_os_administrator',lambda:True)
    identity=f.owner.id;username='owner'
    if failure=='identity':identity='missing'
    elif failure=='username':username='other'
    else:identity=f.identity.create_user(f.owner,'worker',PASSWORD,'Worker',('jobs',));username='worker'
    with pytest.raises(PermissionError):recovery.recover_owner(f.store.path.resolve(),username,identity,'Replacement-fixture-password')
    assert f.identity.authenticate(f.raw).id==f.owner.id
