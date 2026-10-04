import hashlib,json,sqlite3
from pathlib import Path
import pytest
from tests.checkpoint_f_fixture import e_base,f_fixture
from deployment.instance import load_instance
from deployment.schema import fingerprint,preflight
from database.store import Store


def prepared(f):
    from control.notifications import initialize
    from browser.site_access import SiteAccess
    initialize(f.store);SiteAccess(f.store)
    with f.store._connect() as con:return fingerprint(con)


def test_read_only_preflight_rejects_unknown_schema_without_changes(f_fixture):
    f=f_fixture;expected=prepared(f)
    with f.store._connect() as con:con.execute('ALTER TABLE jobs ADD COLUMN unexpected TEXT')
    before=hashlib.sha256(f.store.path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='Unexpected'):preflight(f.store.path.resolve(),expected)
    assert hashlib.sha256(f.store.path.read_bytes()).hexdigest()==before


def test_operational_open_preserves_data_and_denies_ddl(f_fixture):
    f=f_fixture;expected=prepared(f);path=f.store.path.resolve()
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    store=Store(path,initialize=False,expected_schema=expected)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    from application.control_services import compose_control_services
    compose_control_services(store)
    from browser.site_access import SiteAccess
    SiteAccess(store)
    with store._connect() as con:
        assert fingerprint(con)==expected
        with pytest.raises(sqlite3.DatabaseError):con.execute('ALTER TABLE jobs ADD COLUMN forbidden TEXT')
    store.queue_command('Synthetic queued; never execute')
    assert store.counts()['commands']>=1


def test_production_requires_configuration_and_explicit_existing_target(tmp_path,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production');monkeypatch.delenv('CHIEF_INSTANCE_CONFIG',raising=False)
    with pytest.raises(ValueError):load_instance()
    assert not (tmp_path/'worker.db').exists()


def test_marked_production_cannot_be_implicitly_initialized(f_fixture):
    f=f_fixture;prepared(f)
    (f.store.path.parent/'.chief-production.json').write_text(json.dumps({'mode':'PRODUCTION'}))
    before=hashlib.sha256(f.store.path.read_bytes()).hexdigest()
    with pytest.raises(PermissionError):Store(f.store.path)
    assert hashlib.sha256(f.store.path.read_bytes()).hexdigest()==before


def test_preview_refuses_worker_and_database_override(tmp_path,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','preview');monkeypatch.setenv('CHIEF_PREVIEW_ROOT',str(tmp_path/'preview'))
    with pytest.raises(PermissionError):load_instance('worker')
    with pytest.raises(PermissionError):load_instance('dashboard')
    monkeypatch.delenv('JOB_WORKER_DB')
    instance=load_instance('dashboard')
    assert instance.database==tmp_path/'preview/preview.sqlite3'
    (instance.state_root/'.chief-production.json').write_text('{}')
    with pytest.raises(PermissionError):load_instance('dashboard')


def test_test_mode_cannot_open_outside_isolated_root(tmp_path,monkeypatch):
    monkeypatch.setenv('JOB_WORKER_DB',str(tmp_path.parent/'outside.sqlite3'))
    with pytest.raises(PermissionError):load_instance()


def test_real_production_preflight_on_synthetic_state(f_fixture,monkeypatch):
    f=f_fixture;expected=prepared(f);root=f.store.path.parent.resolve()
    (root/'.chief-production.json').write_text(json.dumps({'mode':'PRODUCTION'}))
    config=root/'instance.json';config.write_text(json.dumps({'mode':'PRODUCTION','state_root':str(root),'database':str(f.store.path.resolve()),'schema_sha256':expected}))
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production');monkeypatch.setenv('CHIEF_INSTANCE_CONFIG',str(config));monkeypatch.setenv('JOB_WORKER_DB',str(f.store.path.resolve()))
    assert load_instance().open_store().operational


def test_preview_high_impact_routes_fail_closed(dashboard,monkeypatch):
    from tests.test_identity_http import request
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','preview')
    for path in ('/api/settings/email/test','/api/applications/synthetic/retry'):
        assert request(dashboard,path,'POST',{},dashboard.credentials['raw'])[0]==403
