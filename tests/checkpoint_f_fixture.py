"""Every actual schema step gets its own verified B.5 backup and quarantine drill."""
import hashlib,importlib,json,uuid
from pathlib import Path
import pytest
from operations.backup import create_backup,restore_isolated
from domains.storage import initialize
from control.agents import AgentControls
from application.composition import default_registry
from identity.service import IdentityService
from tests.checkpoint_e_fixture import prepare_e
from database.execution_migrations import migrate_isolated as migrate_e
from database.identity_migrations import migrate_isolated,legacy_digest

PASSWORD='Synthetic-fixture-passphrase-2026'

def recovery_point(store,root):
    root=Path(root);(root/'.chief-isolated-development.json').write_text(json.dumps({'purpose':'ISOLATED_DEVELOPMENT'}))
    unique='f-drill-'+uuid.uuid4().hex
    release=root/(unique+'-source');release.mkdir();(release/'fixture.py').write_text('# Synthetic migration source identity; not a deployment release.\n')
    files={'fixture.py':hashlib.sha256((release/'fixture.py').read_bytes()).hexdigest()}
    tree=hashlib.sha256('\n'.join(n+'\0'+h for n,h in sorted(files.items())).encode()).hexdigest()
    manifest=root/(unique+'.json');manifest.write_text(json.dumps({'files':files,'source_tree_sha256':tree}))
    backup=root/(unique+'-backup');restored=root/(unique+'-restore')
    receipt=create_backup(store.path,backup,source_root=release,source_manifest=manifest,private_storage_confirmed=True)
    pins=dict(manifest_sha256=receipt['manifest_sha256'],expected_source=receipt['source_tree_sha256'],expected_schema=receipt['schema_sha256'])
    restore_isolated(backup,restored,**pins,private_storage_confirmed=True)
    return dict(isolated_root=root,backup_directory=backup,restore_directory=restored,**pins)

def secure_store(store):
    initialize(store);AgentControls(store,default_registry())
    for name in ('migrations','evidence_migrations','execution_migrations','identity_migrations'):
        module=importlib.import_module('database.'+name)
        with store._connect() as con:ready=module.schema_ready(con)
        if not ready:assert module.migrate_isolated(store,**recovery_point(store,store.path.parent))=='APPLIED'
    service=IdentityService(store)
    with store._connect() as con:exists=con.execute('SELECT 1 FROM human_identities').fetchone()
    if not exists:service.bootstrap('fixture-owner',PASSWORD)
    raw,principal=service.login('fixture-owner',PASSWORD)
    return raw,principal

@pytest.fixture
def e_base(tmp_path):
    f=prepare_e(tmp_path);assert migrate_e(f.store,**f.args)=='APPLIED'
    from tests.test_model_routing import registry
    from tests.test_runbooks import make_engine,define
    from domains.jobs.evidence_adapter import job_scope
    with job_scope():
        registry(f.store).set_global_state('one','DISABLED',actor='synthetic-operator')
        engine=make_engine(f);define(engine);engine.start('synthetic','1.0.0')
    with f.store._connect() as con:f.before=legacy_digest(con)
    f.args=recovery_point(f.store,tmp_path)
    return f

@pytest.fixture
def f_fixture(e_base):
    f=e_base;assert migrate_isolated(f.store,**f.args)=='APPLIED'
    f.identity=IdentityService(f.store);f.owner_id=f.identity.bootstrap('owner',PASSWORD)
    f.raw,f.owner=f.identity.login('owner',PASSWORD)
    return f
