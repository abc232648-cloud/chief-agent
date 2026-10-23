import json
import socket
import ssl
from datetime import timedelta
from types import SimpleNamespace

import pytest
from application.local_health import checks
from operations.time_integrity import utc_now,utc_text
from tests.checkpoint_c_fixture import migrated,unmigrated


def rows(store):return {r['node']['id']:r for r in checks(store,utc_now)}


def test_unconfigured_checks_are_read_only_and_never_contact_network(migrated,monkeypatch):
    monkeypatch.delenv('CHIEF_INSTANCE_MODE',raising=False)
    monkeypatch.setenv('CHIEF_STATE_ROOT',str(migrated.store.path.parent))
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('Unexpected network access'))
    before=migrated.store.path.read_bytes()
    result=rows(migrated.store)
    assert result['local.models']['status']=='UNKNOWN'  # This older fixture lacks model-assignment schema.
    assert result['local.https']['monitoring']=='NOT_CONFIGURED'
    assert result['local.worker']['status']=='UNKNOWN'
    assert migrated.store.path.read_bytes()==before
    assert not (migrated.store.path.parent/'service-control').exists()
    assert all(str(migrated.store.path) not in r['reason'] for r in result.values())


@pytest.mark.parametrize('seconds,status',[(0,'HEALTHY'),(-300,'UNKNOWN'),(300,'UNKNOWN')])
def test_worker_freshness(migrated,monkeypatch,seconds,status):
    root=migrated.store.path.parent;monkeypatch.setenv('CHIEF_STATE_ROOT',str(root))
    folder=root/'service-control';folder.mkdir()
    (folder/'worker.json').write_text(json.dumps({'version':1,'component':'worker','state':'READY','heartbeat_at':utc_text(utc_now()+timedelta(seconds=seconds))}))
    assert rows(migrated.store)['local.worker']['status']==status


def test_low_disk_space_reports_attention_without_deleting(migrated,monkeypatch):
    monkeypatch.setattr('application.local_health.shutil.disk_usage',lambda root:SimpleNamespace(total=100*1024**3,free=500*1024**2))
    assert rows(migrated.store)['local.storage']['status']=='DEGRADED'
    assert migrated.store.path.exists()


@pytest.fixture
def installed(migrated,tmp_path,monkeypatch):
    root=tmp_path/'installed';root.mkdir()
    # Only installation resolution is synthetic; probes exercise real files.
    config={'dashboard_port':8765,'public_host':'chief.example.test'}
    monkeypatch.setattr('application.local_health._installation',lambda store:(root,config))
    for app in ('owner','staff','jobs'):
        folder=root/'pwas'/app/'dist';folder.mkdir(parents=True)
        (folder/'index.html').write_text('<!doctype html>synthetic')
        (folder/'sw.js').write_text('// synthetic worker')
    return migrated.store,root,config


def test_closed_ports_and_missing_assets_are_not_healthy(installed,monkeypatch):
    store,root,config=installed
    (root/'pwas/staff/dist/sw.js').unlink()
    calls=[]
    def refused(address,timeout):
        calls.append((address,timeout));raise ConnectionRefusedError('PRIVATE HOST DETAIL')
    monkeypatch.setattr(socket,'create_connection',refused)
    result=rows(store)
    assert result['local.network']['status']=='UNAVAILABLE'
    assert result['local.https']['status']=='UNAVAILABLE'
    assert result['local.staff']['status']=='UNAVAILABLE'
    assert result['local.owner']['status']=='HEALTHY'
    assert calls==[(('127.0.0.1',8765),.75),(('127.0.0.1',443),.75)]
    assert 'PRIVATE HOST DETAIL' not in json.dumps(result)


def test_https_keeps_certificate_and_hostname_verification(installed,monkeypatch):
    store,root,config=installed
    class Raw:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    def context(cafile=None):
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert ctx.check_hostname and ctx.verify_mode==ssl.CERT_REQUIRED
        class Verify:
            def wrap_socket(self,raw,server_hostname):
                assert server_hostname=='chief.example.test'
                raise ssl.SSLCertVerificationError('PRIVATE CERT DETAIL')
        return Verify()
    monkeypatch.setattr(socket,'create_connection',lambda address,timeout:Raw())
    monkeypatch.setattr(ssl,'create_default_context',context)
    result=rows(store)
    assert result['local.network']['status']=='HEALTHY'
    assert result['local.https']['status']=='UNAVAILABLE'
    assert 'PRIVATE CERT DETAIL' not in json.dumps(result)


def test_not_monitored_never_changes_authoritative_health(migrated):
    from control.health import SystemHealth
    health=SystemHealth(migrated.services.controls,probes={})
    assert all(r['monitoring']=='NOT_MONITORED' and r['status']=='UNKNOWN' for r in health.describe())


def test_readiness_api_and_readable_browser(dashboard,configured_both_workspace):
    from tests.test_dashboard_runtime import request
    from tests.browser_navigation import navigate
    from playwright.sync_api import sync_playwright,expect
    code,_,data=request(dashboard,'/api/system-health')
    assert code==200 and any(r.get('scope')=='LOCAL_READINESS' for r in data)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url);navigate(page,'health')
            expect(page.get_by_role('heading',name='Local readiness checks')).to_be_visible()
            expect(page.get_by_text('Data storage space',exact=True)).to_be_visible()
            expect(page.get_by_text('Not configured',exact=True).first).to_be_visible()
            expect(page.get_by_text('Components without a direct monitor',exact=False)).to_be_visible()
            assert page.locator('#health').inner_text().find('chief.database')==-1
        finally:browser.close()


@pytest.mark.parametrize('host,trusted,expected',[('127.0.0.1',True,'HEALTHY'),('wrong.example.test',True,'UNAVAILABLE'),('127.0.0.1',False,'UNAVAILABLE')])
def test_real_tls_listener_verifies_trust_and_hostname(tmp_path,host,trusted,expected):
    from tests.tls_proxy_fixture import local_tls_proxy
    from urllib.parse import urlsplit
    from application.local_health import _trusted_listener
    with local_tls_proxy(tmp_path,1) as (origin,_):
        ca=tmp_path/'ephemeral-tls-cert.pem' if trusted else None
        assert _trusted_listener(host,ca,port=urlsplit(origin).port)[0]==expected


def test_matching_production_paths_required_before_probing(migrated,tmp_path,monkeypatch):
    from application.local_health import _installation
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    config=tmp_path/'instance.json'
    config.write_text(json.dumps({'mode':'PRODUCTION','state_root':str(tmp_path),'database':str(tmp_path/'other.db')}))
    monkeypatch.setenv('CHIEF_INSTANCE_CONFIG',str(config))
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('Mismatched instance must not probe'))
    result=rows(migrated.store)
    assert result['local.https']['monitoring']=='NOT_CONFIGURED'


def test_prepared_model_storage_is_read_without_credentials(dashboard,monkeypatch):
    monkeypatch.setattr('model_registry.setup.ModelSetup.list',lambda *a:pytest.fail('No credential registration reads'))
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('No provider requests'))
    assert rows(dashboard.store)['local.models']['status']=='HEALTHY'
