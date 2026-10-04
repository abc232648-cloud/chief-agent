import sqlite3
from contextlib import closing
import pytest
from tests.checkpoint_e_fixture import d_base,e_fixture
import database.execution_migrations as migration
from database.evidence_migrations import schema_ready as d_ready
from database.migrations import schema_ready as c_ready
from database.store import Store


def test_populated_d_preserved_and_retry(e_fixture):
    f=e_fixture
    with f.store._connect() as con:
        assert migration.legacy_digest(con)==f.before
        assert c_ready(con) and d_ready(con) and migration.schema_ready(con)
        for table in migration.TABLES[1:]:assert con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
    assert migration.migrate_isolated(f.store,**f.args)=='ALREADY_APPLIED'
    with pytest.raises(PermissionError):Store(f.root/'d-restore/state.sqlite3')


@pytest.mark.parametrize('point',[0,1,4,8,12,15])
def test_atomic_failure_retry(d_base,monkeypatch,point):
    ddl=migration.DDL
    monkeypatch.setattr(migration,'DDL',ddl[:point]+('INVALID_SQL',)+ddl[point:])
    with pytest.raises(sqlite3.Error):migration.migrate_isolated(d_base.store,**d_base.args)
    monkeypatch.setattr(migration,'DDL',ddl)
    with d_base.store._connect() as con:
        assert migration.legacy_digest(con)==d_base.before and not migration.schema_ready(con)
    assert migration.migrate_isolated(d_base.store,**d_base.args)=='APPLIED'


def test_interruption_no_partial_schema(d_base,monkeypatch):
    ddl=migration.DDL
    class Interrupted:
        def __iter__(self):
            yield from ddl[:5]
            raise KeyboardInterrupt()
    monkeypatch.setattr(migration,'DDL',Interrupted())
    with pytest.raises(KeyboardInterrupt):migration.migrate_isolated(d_base.store,**d_base.args)
    monkeypatch.setattr(migration,'DDL',ddl)
    with d_base.store._connect() as con:assert migration.legacy_digest(con)==d_base.before and not migration.schema_ready(con)


@pytest.mark.parametrize('damage',['backup','source','schema','receipt','quarantine','changed','marker','target'])
def test_backup_restore_gate(d_base,damage):
    f=d_base;args=dict(f.args);target=f.store
    if damage=='backup':(f.root/'d-backup/state.sqlite3').write_bytes(b'broken')
    elif damage in {'source','schema'}:args['expected_'+damage]='0'*64
    elif damage=='receipt':(f.root/'d-restore/restore-receipt.json').write_text('{}')
    elif damage=='quarantine':
        with closing(sqlite3.connect(f.root/'d-restore/state.sqlite3')) as con:con.execute('PRAGMA application_id=0')
    elif damage=='changed':f.store.add_candidate_fact({'text':'synthetic change'})
    elif damage=='target':
        class Target:path=f.root/'d-restore/state.sqlite3'
        target=Target()
    else:(f.root/'.chief-isolated-development.json').write_text('{}')
    with pytest.raises((ValueError,PermissionError,sqlite3.Error)):migration.migrate_isolated(target,**args)
    with f.store._connect() as con:assert not migration.schema_ready(con)
