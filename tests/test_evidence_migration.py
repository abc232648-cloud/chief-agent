import json
import sqlite3
from contextlib import closing
import pytest
from tests.checkpoint_d_fixture import c_fixture, d_fixture
import database.evidence_migrations as migration
from database.migrations import schema_ready as c_ready
from operations.restore_guard import QUARANTINE_APPLICATION_ID
from database.store import Store


def test_populated_preserved_retry_and_c_rollback(d_fixture):
    f=d_fixture
    with f.store._connect() as con:
        assert migration.legacy_digest(con)==f.before
        assert c_ready(con) and migration.schema_ready(con)
        for table in migration.TABLES[1:]:
            assert con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
    assert migration.migrate_isolated(f.store,**f.args)=='ALREADY_APPLIED'
    with pytest.raises(PermissionError):
        Store(f.root/'c-restore/state.sqlite3')


@pytest.mark.parametrize('point',[0,1,4,7,12,20])
def test_interrupted_transaction_leaves_exact_c(c_fixture,monkeypatch,point):
    f=c_fixture
    ddl=migration.DDL
    broken=ddl[:point]+('INVALID SQL FOR FAILURE INJECTION',)+ddl[point:]
    monkeypatch.setattr(migration,'DDL',broken)
    with pytest.raises(sqlite3.Error):
        migration.migrate_isolated(f.store,**f.args)
    with f.store._connect() as con:
        assert migration.legacy_digest(con)==f.before
        assert not migration.schema_ready(con)
        assert c_ready(con)
    monkeypatch.setattr(migration,'DDL',ddl)
    assert migration.migrate_isolated(f.store,**f.args)=='APPLIED'


@pytest.mark.parametrize('damage',['backup','source','schema','receipt','quarantine','changed','marker'])
def test_prerequisite_rejections(c_fixture,damage):
    f=c_fixture;args=dict(f.args)
    if damage=='backup':
        (f.root/'c-backup/state.sqlite3').write_bytes(b'corrupt')
    elif damage in {'source','schema'}:
        args['expected_'+damage]='0'*64
    elif damage=='receipt':
        (f.root/'c-restore/restore-receipt.json').write_text('{}')
    elif damage=='quarantine':
        with closing(sqlite3.connect(f.root/'c-restore/state.sqlite3')) as con:
            con.execute('PRAGMA application_id=0')
    elif damage=='changed':
        f.store.add_candidate_fact({'text':'changed'})
    else:
        (f.root/'.chief-isolated-development.json').write_text('{}')
    with pytest.raises((ValueError,PermissionError,sqlite3.Error)):
        migration.migrate_isolated(f.store,**args)
    with f.store._connect() as con:
        assert not migration.schema_ready(con)


def test_c_ledger_unmodified_and_d_tamper_rejected(d_fixture):
    with d_fixture.store._connect() as con:
        assert c_ready(con)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute('DELETE FROM chief_evidence_migrations')
        con.execute('DROP TRIGGER d_shared_evidence_update')
    with d_fixture.store._connect() as con:
        with pytest.raises(ValueError):migration.schema_ready(con)


def test_base_exception_rolls_back_before_retry(c_fixture,monkeypatch):
    ddl=migration.DDL
    class InterruptedDDL:
        def __iter__(self):
            yield from ddl[:5]
            raise KeyboardInterrupt('Synthetic interruption')
    monkeypatch.setattr(migration,'DDL',InterruptedDDL())
    with pytest.raises(KeyboardInterrupt):migration.migrate_isolated(c_fixture.store,**c_fixture.args)
    monkeypatch.setattr(migration,'DDL',ddl)
    with c_fixture.store._connect() as con:
        assert migration.legacy_digest(con)==c_fixture.before and not migration.schema_ready(con)
    assert migration.migrate_isolated(c_fixture.store,**c_fixture.args)=='APPLIED'


def test_quarantined_target_never_migrated(c_fixture):
    # Bypass Store constructor only to test the migration's independent guard.
    class Target: path=c_fixture.root/'c-restore/state.sqlite3'
    with pytest.raises(PermissionError):migration.migrate_isolated(Target(),**c_fixture.args)
