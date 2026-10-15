import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from deployment import prepare_instance


def _browser_runtime(tmp_path):
    executable=tmp_path/'chromium'
    executable.write_bytes(b'qualified-browser-fixture')
    digest=hashlib.sha256(executable.read_bytes()).hexdigest()
    config=tmp_path/'browser-runtime.json'
    config.write_text(json.dumps({'executable':str(executable),'sha256':digest,'version':'140.0.0.1'}),encoding='utf-8')
    return config


def test_prepare_production_promotes_verified_isolated_snapshot(tmp_path,monkeypatch):
    source_root=tmp_path/'source';source_root.mkdir()
    source_manifest=tmp_path/'SOURCE_MANIFEST.json';source_manifest.write_text('{}',encoding='utf-8')
    browser=_browser_runtime(tmp_path)
    calls=[]

    def fake_isolated(root,source_root_arg,source_manifest_arg,username,password):
        root=Path(root);root.mkdir(mode=0o700)
        db=root/'preview.sqlite3'
        with closing(sqlite3.connect(db)) as con:
            con.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY,value TEXT)')
            con.execute("INSERT INTO fixture(value) VALUES('ready')")
            con.commit()
        calls.append((root,Path(source_root_arg),Path(source_manifest_arg),username,password))
        return {'status':'PREVIEW_PREPARED','database':str(db),'schema_sha256':'a'*64,'owner_id':'owner-1'}

    checks=[]
    monkeypatch.setattr(prepare_instance,'prepare_isolated',fake_isolated)
    monkeypatch.setattr('deployment.schema.preflight',lambda database,schema: checks.append((Path(database),schema)))

    root=tmp_path/'private'/'production'
    config=tmp_path/'config'/'production-instance.json'
    result=prepare_instance.prepare_production(
        root,config,source_root,source_manifest,'owner','secret-password',
        public_host='chief.example.test',browser_runtime=browser,farm_operations='ENABLED')

    assert result['status']=='PRODUCTION_PREPARED'
    assert result['migrations_on_production'] is False and result['services_started'] is False
    assert json.loads((root/'.chief-production.json').read_text())=={'mode':'PRODUCTION'}
    data=json.loads(config.read_text())
    assert data['mode']=='PRODUCTION'
    assert data['state_root']==str(root)
    assert data['database']==str(root/'state.sqlite3')
    assert data['schema_sha256']=='a'*64
    assert data['farm_operations']=='ENABLED'
    assert data['trusted_tls_proxy']=='127.0.0.1'
    assert data['public_host']=='chief.example.test'
    assert data['browser_runtime']==str(browser)
    with closing(sqlite3.connect(root/'state.sqlite3')) as con:
        assert con.execute('SELECT value FROM fixture').fetchone()[0]=='ready'
    assert calls and calls[0][3:]==('owner','secret-password')
    assert len(checks)==2
    assert not calls[0][0].exists(), 'successful isolated preparation workspace must be removed'


def test_prepare_production_refuses_existing_state_or_config(tmp_path):
    browser=_browser_runtime(tmp_path)
    root=tmp_path/'production';root.mkdir()
    with pytest.raises(FileExistsError,match='state root'):
        prepare_instance.prepare_production(root,tmp_path/'instance.json',tmp_path,tmp_path/'manifest','owner','pw',
            public_host='chief.example.test',browser_runtime=browser)

    root.rmdir();config=tmp_path/'instance.json';config.write_text('{}',encoding='utf-8')
    with pytest.raises(FileExistsError,match='config'):
        prepare_instance.prepare_production(root,config,tmp_path,tmp_path/'manifest','owner','pw',
            public_host='chief.example.test',browser_runtime=browser)


def test_prepare_production_requires_qualified_browser_and_valid_https_host(tmp_path):
    missing=tmp_path/'missing-browser.json'
    with pytest.raises(ValueError,match='browser runtime'):
        prepare_instance.prepare_production(tmp_path/'production',tmp_path/'instance.json',tmp_path,tmp_path/'manifest','owner','pw',
            public_host='chief.example.test',browser_runtime=missing)

    browser=_browser_runtime(tmp_path)
    with pytest.raises(ValueError,match='public host'):
        prepare_instance.prepare_production(tmp_path/'production',tmp_path/'instance.json',tmp_path,tmp_path/'manifest','owner','pw',
            public_host='localhost',browser_runtime=browser)
