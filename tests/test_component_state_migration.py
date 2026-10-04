from contextlib import closing
import sqlite3
import pytest
from checkpoint_c_fixture import unmigrated, migrated
from database import migrations as m
from database.store import Store
from operations.restore_guard import RestoreQuarantined


def test_populated_b5_upgrade_retry_and_exact_legacy_preservation(unmigrated):
    fixture=unmigrated
    assert m.migrate_isolated(fixture.store,**fixture.args)=='APPLIED'
    with fixture.store._connect() as con:
        assert m.legacy_digest(con)==fixture.before
        assert m.schema_ready(con)
        assert con.execute('SELECT COUNT(*) FROM component_modes').fetchone()[0]==0
    assert m.migrate_isolated(fixture.store,**fixture.args)=='ALREADY_APPLIED'
    with fixture.store._connect() as con:
        assert m.legacy_digest(con)==fixture.before
    with pytest.raises(RestoreQuarantined):
        Store(fixture.root/'restore/state.sqlite3')


@pytest.mark.parametrize('failure',[sqlite3.OperationalError('synthetic DDL failure'),KeyboardInterrupt()])
def test_interruption_rolls_back_all_ddl_and_can_retry(unmigrated,monkeypatch,failure):
    fixture=unmigrated
    original=fixture.store._connect
    class Failing:
        def __enter__(self):
            self.con=original();return self
        def __exit__(self,*args):
            return self.con.__exit__(*args)
        def execute(self,sql,args=()):
            if sql==m.DDL[1]:raise failure
            return self.con.execute(sql,args)
    with monkeypatch.context() as patch:
        patch.setattr(fixture.store,'_connect',lambda:Failing())
        with pytest.raises(type(failure)):
            m.migrate_isolated(fixture.store,**fixture.args)
    with fixture.store._connect() as con:
        assert not m.schema_ready(con)
        assert m.legacy_digest(con)==fixture.before
    assert m.migrate_isolated(fixture.store,**fixture.args)=='APPLIED'


def test_changed_fixture_requires_fresh_recovery_point(unmigrated):
    unmigrated.store.queue_command('New post-backup work')
    with pytest.raises(ValueError,match='changed after backup'):
        m.migrate_isolated(unmigrated.store,**unmigrated.args)
    with unmigrated.store._connect() as con:assert not m.schema_ready(con)


def test_unknown_version_or_altered_schema_refused(migrated):
    with migrated.store._connect() as con:
        con.execute("UPDATE chief_schema_migrations SET checksum='changed'")
    with pytest.raises(ValueError,match='version/checksum'):
        m.migrate_isolated(migrated.store,**migrated.args)


def test_wrong_root_or_missing_development_declaration_refused(unmigrated,tmp_path):
    (unmigrated.root/'.chief-isolated-development.json').write_text('{}')
    with pytest.raises(PermissionError):
        m.migrate_isolated(unmigrated.store,**unmigrated.args)


def test_restored_copy_cannot_be_migration_target(unmigrated):
    fake=object.__new__(Store);fake.path=unmigrated.root/'restore/state.sqlite3'
    with pytest.raises(RestoreQuarantined):m.migrate_isolated(fake,**unmigrated.args)


def test_unlocked_or_modified_drill_is_rejected(unmigrated):
    with closing(sqlite3.connect(unmigrated.root/'restore/state.sqlite3')) as con:
        # Test tampering of a disposable drill, not operational promotion.
        con.execute("UPDATE commands SET instruction='corrupted drill'");con.commit()
    with pytest.raises(ValueError,match='preserve'):
        m.migrate_isolated(unmigrated.store,**unmigrated.args)


def test_composition_and_startup_do_not_migrate(unmigrated):
    from application.control_services import compose_control_services
    services=compose_control_services(unmigrated.store)
    services.controls.describe();services.health.describe()
    with unmigrated.store._connect() as con:
        assert not m.schema_ready(con)
        assert m.legacy_digest(con)==unmigrated.before


def test_schema_only_change_after_backup_is_rejected(unmigrated):
    with unmigrated.store._connect() as con:con.execute('CREATE INDEX synthetic_index ON commands(status)')
    with pytest.raises(ValueError,match='changed after backup'):
        m.migrate_isolated(unmigrated.store,**unmigrated.args)


def test_concurrent_migration_attempts_apply_once(unmigrated):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:m.migrate_isolated(unmigrated.store,**unmigrated.args),range(2)))
    assert sorted(results)==['ALREADY_APPLIED','APPLIED']
    with unmigrated.store._connect() as con:
        assert m.legacy_digest(con)==unmigrated.before
        assert con.execute('SELECT COUNT(*) FROM chief_schema_migrations').fetchone()[0]==1
