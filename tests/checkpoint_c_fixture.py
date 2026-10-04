"""Every migration uses a populated B.5-schema fixture and a quarantined drill."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import pytest

from database.store import Store
from database.migrations import migrate_isolated, legacy_digest
from application.composition import default_registry
from application.control_services import compose_control_services
from control.agents import AgentControls
from domains.storage import initialize
from operations.backup import create_backup, restore_isolated, database_identity


def prepare(root):
    root.mkdir(exist_ok=True)
    (root/'.chief-isolated-development.json').write_text(json.dumps({'purpose':'ISOLATED_DEVELOPMENT'}))
    store=Store(root/'fixture.sqlite3')
    registry=default_registry()
    controls=AgentControls(store,registry)
    initialize(store)
    controls.change('jobs',{'running':False,'autostart':False})
    store.queue_command('Synthetic pending work')
    action=store.add_action('Synthetic approval','submit_application')
    store.resolve_action(action,'APPROVED')
    with store._connect() as con:
        # Exercise the real legacy recorder grant. Arbitrary unregistered kinds
        # must not acquire access to new human-private Farm records.
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming','soil_test','{}',1)")
        con.execute("INSERT INTO domain_reminders(domain,title,due_at) VALUES('farming','fixture',1)")
        con.execute("INSERT INTO agent_runs(domain,task,started) VALUES('jobs','fixture',1)")
        con.execute("INSERT INTO control_state VALUES('heartbeat','1')")
        before=legacy_digest(con)
    # Fingerprint established by the accepted B.5 synthetic restore demonstration.
    assert database_identity(store.path)['schema_sha256']=='12fcde6008ccbdd623da03ca271dca73c10bf8d958f03930657510f766adc1cd'
    release=root/'synthetic-release'
    release.mkdir()
    (release/'fixture.py').write_text('# Synthetic source context; B.5 schema fixture\n')
    files={'fixture.py':hashlib.sha256((release/'fixture.py').read_bytes()).hexdigest()}
    tree=hashlib.sha256('\n'.join(n+'\0'+h for n,h in sorted(files.items())).encode()).hexdigest()
    source_manifest=root/'release.json'
    source_manifest.write_text(json.dumps({'files':files,'source_tree_sha256':tree}))
    receipt=create_backup(store.path,root/'backup',source_root=release,source_manifest=source_manifest,private_storage_confirmed=True)
    pins=dict(manifest_sha256=receipt['manifest_sha256'],expected_source=receipt['source_tree_sha256'],expected_schema=receipt['schema_sha256'])
    restore_isolated(root/'backup',root/'restore',**pins,private_storage_confirmed=True)
    args=dict(isolated_root=root,backup_directory=root/'backup',restore_directory=root/'restore',**pins)
    return SimpleNamespace(root=root,store=store,registry=registry,controls=controls,before=before,args=args)


@pytest.fixture
def unmigrated(tmp_path):
    return prepare(tmp_path)


@pytest.fixture
def migrated(unmigrated):
    assert migrate_isolated(unmigrated.store,**unmigrated.args)=='APPLIED'
    unmigrated.services=compose_control_services(unmigrated.store)
    return unmigrated
