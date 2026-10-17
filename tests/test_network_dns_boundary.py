import socket
from types import SimpleNamespace
import pytest

import networking.dns_policy as dns_policy


def _answer(host, port, family=0, type=0, proto=0, flags=0):
    version = socket.AF_INET6 if ':' in str(host) else socket.AF_INET
    if family not in (0, version):
        raise socket.gaierror(socket.EAI_NONAME, 'family mismatch')
    socktype = type or socket.SOCK_STREAM
    return [(version, socktype, proto or 6, '', (str(host), port))]


def test_process_policy_resolves_named_hosts_then_uses_numeric_connection(monkeypatch):
    lookups=[]
    orig = (lambda host, port, family=0, type=0, proto=0, flags=0:
            lookups.append((host, flags)) or _answer(host, port, family, type, proto, flags))
    monkeypatch.setattr(dns_policy, '_ORIGINAL_GETADDRINFO', orig)
    monkeypatch.setattr(socket, 'getaddrinfo', orig)
    monkeypatch.setattr(dns_policy, 'resolve_addresses',
                        lambda host, port, mode=None, provider=dns_policy.QUAD9_SECURE: ('93.184.215.14',))

    dns_policy.install_process_policy('quad9')
    result=socket.getaddrinfo('api.fixture.test',443,type=socket.SOCK_STREAM)

    assert dns_policy.process_policy_mode() == 'quad9'
    assert result[0][4][0] == '93.184.215.14'
    assert [host for host,_ in lookups] == ['93.184.215.14']
    assert lookups[0][1] & socket.AI_NUMERICHOST


def test_process_policy_rejects_private_or_mixed_named_answers(monkeypatch):
    monkeypatch.setattr(dns_policy, 'resolve_addresses',
                        lambda *a, **kw: ('93.184.215.14','127.0.0.1'))
    dns_policy.install_process_policy('quad9')
    with pytest.raises(PermissionError, match='exclusively public'):
        socket.getaddrinfo('mixed.fixture.test',443,type=socket.SOCK_STREAM)


def test_explicit_numeric_loopback_does_not_use_external_dns(monkeypatch):
    calls=[]
    monkeypatch.setattr(dns_policy, 'resolve_addresses',
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError('DNS must not run for numeric loopback')))
    orig = (lambda host, port, family=0, type=0, proto=0, flags=0:
            calls.append(host) or _answer(host, port, family, type, proto, flags))
    monkeypatch.setattr(dns_policy, '_ORIGINAL_GETADDRINFO', orig)
    monkeypatch.setattr(socket, 'getaddrinfo', orig)
    dns_policy.install_process_policy('quad9')
    result=socket.getaddrinfo('127.0.0.1',5678,type=socket.SOCK_STREAM)
    assert result[0][4][0] == '127.0.0.1'
    assert calls == ['127.0.0.1']


def test_system_mode_is_explicit_and_still_pins_returned_numeric_address(monkeypatch):
    calls=[]
    def original(host, port, family=0, type=0, proto=0, flags=0):
        calls.append((host, flags))
        if host == 'smtp.fixture.test':
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.215.14',port))]
        return _answer(host, port, family, type, proto, flags)
    monkeypatch.setattr(dns_policy, '_ORIGINAL_GETADDRINFO', original)
    monkeypatch.setattr(socket, 'getaddrinfo', original)
    dns_policy.install_process_policy('system')
    result=socket.getaddrinfo('smtp.fixture.test',587,type=socket.SOCK_STREAM)
    assert result[0][4][0] == '93.184.215.14'
    assert calls[0][0] == 'smtp.fixture.test'
    assert calls[1][0] == '93.184.215.14'
    assert calls[1][1] & socket.AI_NUMERICHOST


def test_policy_refuses_unknown_process_resolver_hook(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[])
    with pytest.raises(RuntimeError, match='unknown process resolver hook'):
        dns_policy.install_process_policy('quad9')


def test_provider_sdk_boundary_installs_process_policy(monkeypatch):
    import gateway.providers as providers
    calls=[]
    monkeypatch.setattr(dns_policy,'ensure_process_policy',lambda mode=None:calls.append(mode) or 'quad9')
    assert providers._sdk_call(lambda:'ok','provider failed') == 'ok'
    assert calls == [None]


def test_service_config_installs_selected_process_policy(tmp_path, monkeypatch):
    import json
    from deployment import launch
    from deployment.schema import fingerprint
    import sqlite3

    root=tmp_path/'state';root.mkdir()
    database=root/'state.sqlite3'
    with sqlite3.connect(database) as con:
        con.execute('CREATE TABLE fixture (id INTEGER)')
        schema=fingerprint(con)
    browser=tmp_path/'browser.json';browser.write_text('{}')
    # Stop after resolver installation; later production validation is unrelated
    # to this boundary test and requires a qualified browser fixture.
    calls=[]
    monkeypatch.setattr(dns_policy,'ensure_process_policy',lambda mode=None:calls.append(mode) or mode)
    monkeypatch.setattr(launch,'_plain',lambda *a,**kw:None)
    config=tmp_path/'instance.json'
    config.write_text(json.dumps({
        'mode':'ISOLATED_DEVELOPMENT','state_root':str(root),'database':str(database),
        'schema_sha256':schema,'dashboard_port':8765,'dns_mode':'system',
        'farm_operations':'DISABLED'
    }))
    # load_instance may enforce additional fixture state; we only need evidence
    # that configuration crossed into the resolver boundary first.
    try:
        launch.configure(str(config))
    except Exception:
        pass
    assert calls == ['system']
