import sqlite3
import pytest
from tests.checkpoint_f_fixture import e_base,f_fixture
import database.identity_migrations as migration
from database.execution_migrations import schema_ready as e_ready
from database.store import Store

def test_populated_e_preserved(e_base):
    f=e_base;assert migration.migrate_isolated(f.store,**f.args)=='APPLIED'
    assert migration.migrate_isolated(f.store,**f.args)=='ALREADY_APPLIED'
    with f.store._connect() as con:
        assert migration.legacy_digest(con)==f.before and e_ready(con)
        for table in migration.TABLES[1:]:assert con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
    with pytest.raises(PermissionError):Store(f.args['restore_directory']/'state.sqlite3')

@pytest.mark.parametrize('point',[0,1,4,8,13])
def test_failure_rolls_back_then_retry(e_base,monkeypatch,point):
    ddl=migration.DDL;monkeypatch.setattr(migration,'DDL',ddl[:point]+('INVALID SQL',)+ddl[point:])
    with pytest.raises(sqlite3.Error):migration.migrate_isolated(e_base.store,**e_base.args)
    monkeypatch.setattr(migration,'DDL',ddl)
    with e_base.store._connect() as con:assert migration.legacy_digest(con)==e_base.before and not migration.schema_ready(con)
    assert migration.migrate_isolated(e_base.store,**e_base.args)=='APPLIED'

def test_interruption_preserves_e(e_base,monkeypatch):
    ddl=migration.DDL
    class Interrupted:
        def __iter__(self):yield from ddl[:4];raise KeyboardInterrupt()
    monkeypatch.setattr(migration,'DDL',Interrupted())
    with pytest.raises(KeyboardInterrupt):migration.migrate_isolated(e_base.store,**e_base.args)
    monkeypatch.setattr(migration,'DDL',ddl)
    with e_base.store._connect() as con:assert migration.legacy_digest(con)==e_base.before and not migration.schema_ready(con)

@pytest.mark.parametrize('damage',['backup','source','schema','marker','receipt','changed','target'])
def test_gate_rejects_unsafe_fixture(e_base,damage):
    f=e_base;args=dict(f.args);store=f.store
    if damage=='backup':(args['backup_directory']/'state.sqlite3').write_bytes(b'broken')
    elif damage in {'source','schema'}:args['expected_'+damage]='0'*64
    elif damage=='marker':(f.root/'.chief-isolated-development.json').write_text('{}')
    elif damage=='receipt':(args['restore_directory']/'restore-receipt.json').write_text('{}')
    elif damage=='changed':f.store.add_candidate_fact({'text':'changed'})
    else:
        class Target:path=args['restore_directory']/'state.sqlite3'
        store=Target()
    with pytest.raises((ValueError,PermissionError,sqlite3.Error)):migration.migrate_isolated(store,**args)
    with f.store._connect() as con:assert not migration.schema_ready(con)
