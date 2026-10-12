import os
from types import SimpleNamespace

import pytest

from deployment.install_readiness import installation_readiness


SCHEMA='a'*64
SOURCE='b'*64
MANIFEST='c'*64


def seams(tmp_path,monkeypatch,*,browser=None,files=None,actual_source=SOURCE):
    config=(tmp_path/'instance.json').resolve();config.write_text('{}')
    source=(tmp_path/'source').resolve();source.mkdir()
    source_manifest=(tmp_path/'source-manifest.json').resolve();source_manifest.write_text('{}')
    backup=(tmp_path/'backup').resolve();backup.mkdir()
    instance=SimpleNamespace(mode='production',state_root=tmp_path,schema_sha256=SCHEMA)
    calls=[]
    def configure(path):
        calls.append(('configure',path))
        os.environ['CHIEF_INSTANCE_MODE']='production'
        os.environ['CHIEF_BROWSER_RUNTIME']=str(tmp_path/'browser.json')
        return instance
    monkeypatch.setattr('deployment.launch.configure',configure)
    monkeypatch.setattr('operations.storage_health.storage_readiness',
        lambda root:(calls.append(('storage',root)) or {'scope':'STARTUP_PROBE_ONLY'}))
    monkeypatch.setattr('browser.runtime.launch_options',
        lambda:(calls.append(('browser',None)) or (browser if browser is not None else {'executable_path':'/synthetic/browser','chromium_sandbox':True})))
    monkeypatch.setattr('operations.backup.source_identity',
        lambda root,manifest:(calls.append(('source',root,manifest)) or actual_source))
    monkeypatch.setattr('operations.backup.verify_backup',
        lambda *a,**kw:(calls.append(('backup',a,kw)) or {'backup_id':'synthetic-backup','coverage':'database-only'}))
    expected={'chief-agent-dashboard.service','chief-agent-worker.service','chief-agent-scheduler.service'}
    monkeypatch.setattr('deployment.service_files.definitions',
        lambda *a,**kw:(calls.append(('services',a,kw)) or ({name:b'x' for name in (files if files is not None else expected)})))
    return config,calls,source,source_manifest,backup


def ready(config,source,source_manifest,backup):
    return installation_readiness(config,source_root=source,source_manifest=source_manifest,
        backup_directory=backup,manifest_sha256=MANIFEST,expected_source=SOURCE,expected_schema=SCHEMA,
        platform='linux',project='/home/fixture/chief',user='fixture',python='/home/fixture/chief-python')


def test_install_readiness_requires_all_gates_and_restores_environment(tmp_path,monkeypatch):
    config,calls,source,source_manifest,backup=seams(tmp_path,monkeypatch)
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','test')
    monkeypatch.setenv('CHIEF_BROWSER_RUNTIME','/previous/browser.json')
    result=ready(config,source,source_manifest,backup)
    assert result=={
        'status':'SOURCE_READY_FOR_INSTALL_REVIEW','installation_performed':False,
        'services_started':False,'restore_promoted':False,'network_checks_performed':False,
        'mode':'production','source_tree_sha256':SOURCE,'schema_sha256':SCHEMA,
        'storage_scope':'STARTUP_PROBE_ONLY','browser_sandbox_required':True,
        'backup_id':'synthetic-backup','backup_coverage':'database-only',
        'service_definitions':['chief-agent-dashboard.service','chief-agent-scheduler.service','chief-agent-worker.service'],
    }
    assert [item[0] for item in calls]==['configure','storage','browser','source','backup','services']
    assert os.environ['CHIEF_INSTANCE_MODE']=='test'
    assert os.environ['CHIEF_BROWSER_RUNTIME']=='/previous/browser.json'


def test_install_readiness_rejects_backup_schema_not_matching_instance(tmp_path,monkeypatch):
    config,calls,source,source_manifest,backup=seams(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='schema pin'):
        installation_readiness(config,source_root=source,source_manifest=source_manifest,backup_directory=backup,
            manifest_sha256=MANIFEST,expected_source=SOURCE,expected_schema='d'*64,
            platform='linux',project='/home/fixture/chief',user='fixture')
    assert [item[0] for item in calls]==['configure']


def test_install_readiness_rejects_unsandboxed_browser_before_source_or_backup(tmp_path,monkeypatch):
    config,calls,source,source_manifest,backup=seams(tmp_path,monkeypatch,browser={'executable_path':'/synthetic/browser','chromium_sandbox':False})
    with pytest.raises(PermissionError,match='native sandbox'):
        ready(config,source,source_manifest,backup)
    assert [item[0] for item in calls]==['configure','storage','browser']


def test_install_readiness_rejects_source_tree_not_matching_backup_pin(tmp_path,monkeypatch):
    config,calls,source,source_manifest,backup=seams(tmp_path,monkeypatch,actual_source='d'*64)
    with pytest.raises(ValueError,match='source tree'):
        ready(config,source,source_manifest,backup)
    assert [item[0] for item in calls]==['configure','storage','browser','source']


def test_install_readiness_requires_complete_service_definition_set(tmp_path,monkeypatch):
    config,calls,source,source_manifest,backup=seams(tmp_path,monkeypatch,files={'chief-agent-dashboard.service','chief-agent-worker.service'})
    with pytest.raises(ValueError,match='Incomplete service-definition'):
        ready(config,source,source_manifest,backup)
    assert [item[0] for item in calls]==['configure','storage','browser','source','backup','services']


def test_install_readiness_never_accepts_nonproduction_instance(tmp_path,monkeypatch):
    config,calls,source,source_manifest,backup=seams(tmp_path,monkeypatch)
    monkeypatch.setattr('deployment.launch.configure',lambda path:SimpleNamespace(mode='test',state_root=tmp_path,schema_sha256=SCHEMA))
    with pytest.raises(PermissionError,match='prepared production'):
        ready(config,source,source_manifest,backup)


def test_install_readiness_requires_absolute_evidence_paths(tmp_path,monkeypatch):
    config,calls,source,source_manifest,backup=seams(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='Backup directory'):
        installation_readiness(config,source_root=source,source_manifest=source_manifest,backup_directory='relative-backup',
            manifest_sha256=MANIFEST,expected_source=SOURCE,expected_schema=SCHEMA,
            platform='linux',project='/home/fixture/chief',user='fixture')
    assert calls==[]
