import json
import os

import pytest

from deployment.https_policy import contract, public_hostname, trusted_loopback


def test_https_contract_is_loopback_only_and_non_secret():
    value=contract('chief.example.com',8765,'127.0.0.1')
    assert value['public_origin']=='https://chief.example.com'
    assert value['application_protocol']=='HTTPS_ONLY'
    assert value['backend_listener']=='127.0.0.1:8765'
    assert value['trusted_proxy']=='127.0.0.1'
    assert value['response_security']['Strict-Transport-Security']=='max-age=31536000'
    encoded=json.dumps(value)
    assert 'password' not in encoded.lower() and 'token' not in encoded.lower()


@pytest.mark.parametrize('value',['','localhost','https://chief.example.com','chief.example.com:443',
                                  '127.0.0.1','chief','-bad.example.com','bad_.example.com'])
def test_public_hostname_rejects_ambiguous_or_non_certificate_targets(value):
    with pytest.raises(ValueError):public_hostname(value)


@pytest.mark.parametrize('value',['192.168.1.2','8.8.8.8','*','localhost'])
def test_trusted_proxy_requires_one_loopback_ip(value):
    with pytest.raises(ValueError):trusted_loopback(value)


def test_production_service_config_requires_https_and_defaults_farm_off(tmp_path,monkeypatch):
    from deployment import instance,launch
    root=tmp_path/'state';root.mkdir();database=root/'chief.sqlite3';database.write_bytes(b'fixture')
    config=tmp_path/'instance.json'
    config.write_text(json.dumps({
        'mode':'PRODUCTION','state_root':str(root),'database':str(database),
        'schema_sha256':'0'*64,'dashboard_port':8765,
        'trusted_tls_proxy':'127.0.0.1','public_host':'chief.example.com'
    }))
    for key in ('JOB_WORKER_DB','CHIEF_STATE_ROOT','DASHBOARD_HOST','DASHBOARD_PORT',
                'CHIEF_TRUSTED_PROXY','DASHBOARD_TRUSTED_HOST','CHIEF_FARM_PRODUCTION',
                'CHIEF_INSTANCE_MODE','CHIEF_INSTANCE_CONFIG','CHIEF_SERVICE_CONFIGURED'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(instance,'load_instance',lambda component:'validated-'+component)
    assert launch.configure(str(config.resolve()))=='validated-worker'
    assert os.environ['CHIEF_INSTANCE_MODE']=='production'
    assert os.environ['DASHBOARD_HOST']=='127.0.0.1'
    assert os.environ['CHIEF_TRUSTED_PROXY']=='127.0.0.1'
    assert os.environ['DASHBOARD_TRUSTED_HOST']=='chief.example.com'
    assert os.environ['CHIEF_FARM_PRODUCTION']=='DISABLED'
    assert os.environ['CHIEF_SERVICE_CONFIGURED']=='1'


def test_production_config_missing_tls_or_invalid_farm_fails_before_instance_open(tmp_path,monkeypatch):
    from deployment import instance,launch
    root=tmp_path/'state';root.mkdir();database=root/'chief.sqlite3';database.write_bytes(b'fixture')
    opened=[];monkeypatch.setattr(instance,'load_instance',lambda component:opened.append(component))
    for payload in (
        {'mode':'PRODUCTION','state_root':str(root),'database':str(database),'schema_sha256':'0'*64},
        {'mode':'PRODUCTION','state_root':str(root),'database':str(database),'schema_sha256':'0'*64,
         'trusted_tls_proxy':'192.168.1.2','public_host':'chief.example.com'},
        {'mode':'PRODUCTION','state_root':str(root),'database':str(database),'schema_sha256':'0'*64,
         'trusted_tls_proxy':'127.0.0.1','public_host':'localhost'},
        {'mode':'PRODUCTION','state_root':str(root),'database':str(database),'schema_sha256':'0'*64,
         'trusted_tls_proxy':'127.0.0.1','public_host':'chief.example.com','farm_operations':'AUTO'},
    ):
        config=tmp_path/'instance.json';config.write_text(json.dumps(payload))
        for key in ('JOB_WORKER_DB','CHIEF_STATE_ROOT','DASHBOARD_HOST','DASHBOARD_PORT',
                    'CHIEF_TRUSTED_PROXY','DASHBOARD_TRUSTED_HOST','CHIEF_FARM_PRODUCTION',
                    'CHIEF_INSTANCE_MODE','CHIEF_INSTANCE_CONFIG','CHIEF_SERVICE_CONFIGURED'):
            monkeypatch.delenv(key,raising=False)
        with pytest.raises((ValueError,PermissionError)):launch.configure(str(config.resolve()))
    assert opened==[]


def test_isolated_instance_cannot_turn_on_production_farm_or_proxy(tmp_path,monkeypatch):
    from deployment import launch
    root=tmp_path/'state';root.mkdir();database=root/'chief.sqlite3';database.write_bytes(b'fixture')
    config=tmp_path/'instance.json';config.write_text(json.dumps({
        'mode':'ISOLATED_DEVELOPMENT','state_root':str(root),'database':str(database),
        'schema_sha256':'0'*64,'farm_operations':'ENABLED',
        'trusted_tls_proxy':'127.0.0.1','public_host':'chief.example.com'}))
    for key in ('JOB_WORKER_DB','CHIEF_STATE_ROOT','DASHBOARD_HOST'):
        monkeypatch.delenv(key,raising=False)
    with pytest.raises(ValueError,match='Production Farm/TLS fields'):
        launch.configure(str(config.resolve()))
