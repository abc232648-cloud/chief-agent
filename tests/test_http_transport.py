"""Real private HTTP boundary tests plus controlled WSGI delivery failures."""
from contextlib import contextmanager
from io import BytesIO
import http.client
import json
import logging
import socket
import struct
from threading import Thread, Event
from urllib.parse import urlsplit
import pytest
from application.http_transport import (
    TransportSettings, create_dashboard_server, close_dashboard_server,
    wsgi_application, BODY_LIMIT, RESPONSE_LIMIT,
)


def request(dashboard, path='/api/auth/session', method='GET', body=None, headers=None):
    url=urlsplit(dashboard.url)
    connection=http.client.HTTPConnection(url.hostname,url.port,timeout=5)
    try:
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse()
        return response.status,response.headers,response.read()
    finally:connection.close()


@contextmanager
def proxy_server(dashboard):
    server=create_dashboard_server(dashboard.app.Handler,TransportSettings(port=0,trusted_proxy='127.0.0.1'))
    thread=Thread(target=server.run,daemon=True);thread.start()
    original=dashboard.url;dashboard.url='http://127.0.0.1:'+str(server.effective_port)
    try:yield dashboard
    finally:
        dashboard.url=original;close_dashboard_server(server);thread.join(5)
        assert not thread.is_alive()


def raw(dashboard, data):
    url=urlsplit(dashboard.url)
    with socket.create_connection((url.hostname,url.port),timeout=5) as client:
        client.sendall(data)
        response=b''
        while True:
            try:block=client.recv(65536)
            except ConnectionResetError:break
            if not block:break
            response+=block
        return response


@pytest.mark.parametrize('changes',[
    {'host':'0.0.0.0'}, {'host':'::'}, {'host':'8.8.8.8'},
    {'trusted_proxy':'*'}, {'trusted_proxy':'192.168.1.2'},
    {'host':'192.168.1.2','trusted_proxy':'127.0.0.1'}, {'port':-1}, {'port':65536},
])
def test_configuration_cannot_expose_wildcard_public_or_remote_proxy(changes):
    with pytest.raises(ValueError):TransportSettings(**changes)


def test_real_server_preserves_auth_and_ignores_untrusted_proxy(dashboard):
    code,headers,body=request(dashboard,headers={'X-Forwarded-Proto':'https','X-Forwarded-Host':'evil.test'})
    assert code==401 and 'Traceback' not in body.decode()
    assert len(headers['X-Request-ID'])==32
    code,_,_=request(dashboard,headers={'Cookie':'chief_session='+dashboard.credentials['raw']})
    assert code==200
    code,_,_=request(dashboard,headers={'Host':'evil.test','X-Forwarded-Host':'localhost'})
    assert code==403


def test_real_request_limits_and_ambiguous_framing_never_dispatch(dashboard,monkeypatch):
    calls=[]
    monkeypatch.setattr(dashboard.app.Handler,'do_POST',lambda self:calls.append(True))
    prefix=b'POST /api/command HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n'
    response=raw(dashboard,prefix+f'Content-Length: {BODY_LIMIT+1}\r\n\r\n'.encode())
    assert b'413' in response.split(b'\r\n',1)[0] and calls==[]
    response=raw(dashboard,prefix+b'Content-Length: 1\r\nContent-Length: 2\r\n\r\nx')
    assert b'400' in response.split(b'\r\n',1)[0] and calls==[]
    response=raw(dashboard,prefix+b'X-Large: '+b'a'*17000+b'\r\n\r\n')
    assert (b'431' in response.split(b'\r\n',1)[0] or response==b'') and calls==[]
    assert request(dashboard,path='/login')[0]==200


def test_incomplete_request_never_runs_a_mutation(dashboard,monkeypatch):
    called=Event()
    monkeypatch.setattr(dashboard.app.Handler,'do_POST',lambda self:called.set())
    url=urlsplit(dashboard.url)
    with socket.create_connection((url.hostname,url.port),timeout=5) as client:
        client.sendall(b'POST /api/command HTTP/1.1\r\nHost: localhost\r\nContent-Length: 99\r\n\r\nx')
    assert not called.wait(0.2)
    assert request(dashboard,path='/login')[0]==200


@pytest.mark.parametrize('error',[RuntimeError,ConnectionAbortedError])
def test_application_errors_are_not_disguised_as_disconnects_or_leaked(dashboard,monkeypatch,caplog,error):
    import application.auth_routes as routes
    calls=[]
    def broken(*args):calls.append(True);raise error('synthetic-key-DO-NOT-LOG')
    monkeypatch.setattr(routes,'intercept',broken)
    code,headers,body=request(dashboard,path='/api/state?token=synthetic-url-DO-NOT-LOG')
    assert code==500 and calls==[True]
    assert json.loads(body)['correlation_id']==headers['X-Request-ID']
    assert 'APPLICATION_ERROR' in caplog.text
    assert all(secret not in caplog.text+body.decode() for secret in ('synthetic-key','synthetic-url','Traceback'))


@pytest.mark.parametrize('failure',[BrokenPipeError,ConnectionResetError,ConnectionAbortedError])
def test_delivery_failure_does_not_repeat_committed_mutation(dashboard,monkeypatch,caplog,failure):
    calls=[]
    def mutation(handler):
        calls.append(dashboard.store.queue_command('Synthetic once-only command'))
        handler.json({'status':'QUEUED'},201)
    monkeypatch.setattr(dashboard.app.Handler,'do_POST',mutation)
    env={'REQUEST_METHOD':'POST','PATH_INFO':'/api/command','REMOTE_ADDR':'127.0.0.1',
         'wsgi.url_scheme':'http','wsgi.input':BytesIO(b'{}'),'CONTENT_LENGTH':'2'}
    statuses=[]
    def unavailable(status,headers):statuses.append(status);raise failure('synthetic-secret')
    result=wsgi_application(dashboard.app.Handler,TransportSettings())(env,unavailable)
    assert result==[] and statuses==['201 Created'] and len(calls)==1
    assert len(dashboard.store.commands())==1
    assert 'CLIENT_DISCONNECTED' in caplog.text and 'synthetic-secret' not in caplog.text


def test_actual_socket_reset_does_not_replay_or_poison_next_request(dashboard,monkeypatch,caplog):
    original=dashboard.app.Handler.do_POST
    entered=Event();release=Event();calls=[]
    def response(handler):
        calls.append(dashboard.store.queue_command('Synthetic disconnected mutation'))
        entered.set();assert release.wait(5)
        handler.send_response(200);handler.send_header('Content-Length',str(2*1024*1024));handler.end_headers()
        handler.wfile.write(b'x'*(2*1024*1024))
    monkeypatch.setattr(dashboard.app.Handler,'do_POST',response)
    url=urlsplit(dashboard.url)
    client=socket.create_connection((url.hostname,url.port),timeout=5)
    try:
        client.sendall(b'POST /api/command HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n')
        assert entered.wait(5)
        # SO_LINGER uses unsigned shorts on Winsock, ints on POSIX.
        import os
        client.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('HH' if os.name=='nt' else 'ii',1,0))
        client.close();release.set()
        monkeypatch.setattr(dashboard.app.Handler,'do_POST',original)
        assert request(dashboard,path='/login')[0]==200
        assert len(calls)==1 and len(dashboard.store.commands())==1
        assert 'Traceback' not in caplog.text and 'APPLICATION_ERROR' not in caplog.text
    finally:client.close();release.set()


def test_trusted_local_proxy_requires_https_and_uses_secure_cookies(dashboard,monkeypatch):
    from identity.service import IdentityService
    # Synthetic service seam; proxy/HTTP/cookie behavior is exercised over TCP.
    principal=dashboard.credentials['principal'];token=dashboard.credentials['raw']
    monkeypatch.setattr(IdentityService,'login',lambda *args:(token,principal))
    with proxy_server(dashboard) as target:
        host=urlsplit(target.url).netloc
        assert request(target,path='/login')[0]==403
        headers={'Host':host,'Origin':'https://'+host,'X-Forwarded-Proto':'https','Content-Type':'application/json'}
        code,response,_=request(target,path='/api/auth/login',method='POST',body='{}',headers=headers)
        assert code==200 and '; Secure' in response['Set-Cookie'] and 'HttpOnly' in response['Set-Cookie']
        # Host and client identity are never accepted from forwarded headers.
        code,_,_=request(target,headers={'Host':'evil.test','X-Forwarded-Proto':'https','X-Forwarded-Host':host})
        assert code==403
        headers['Origin']='http://'+host
        assert request(target,path='/api/auth/login',method='POST',body='{}',headers=headers)[0]==403
        headers.update(Origin='https://'+host, Cookie='chief_session='+token,
                       **{'X-Chief-CSRF':dashboard.credentials['csrf']})
        assert request(target,path='/api/auth/logout',method='POST',body='{}',headers=headers)[0]==200


def test_head_redirect_and_response_limits(dashboard,monkeypatch):
    code,headers,body=request(dashboard,path='/login',method='HEAD')
    assert code==200 and body==b'' and int(headers['Content-Length'])>0
    assert request(dashboard,path='/')[0]==303
    def huge(handler):handler.wfile.write(b'x'*(RESPONSE_LIMIT+1))
    monkeypatch.setattr(dashboard.app.Handler,'do_GET',huge)
    code,_,body=request(dashboard)
    assert code==500 and b'Server error' in body


def test_real_tls_proxy_login_rotation_logout_and_secure_cookie_jar(dashboard,tmp_path):
    import urllib.request
    import http.cookiejar
    from tests.tls_proxy_fixture import local_tls_proxy
    from tests.checkpoint_f_fixture import PASSWORD
    with proxy_server(dashboard):
        with local_tls_proxy(tmp_path,urlsplit(dashboard.url).port) as (origin,context):
            jar=http.cookiejar.CookieJar()
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(jar),urllib.request.HTTPSHandler(context=context))
            def post(path,body,csrf=None):
                headers={'Origin':origin,'Content-Type':'application/json','X-Forwarded-Proto':'http'}
                if csrf:headers['X-Chief-CSRF']=csrf
                req=urllib.request.Request(origin+path,data=json.dumps(body).encode(),headers=headers)
                with opener.open(req,timeout=5) as response:return json.load(response),response.headers
            login,headers=post('/api/auth/login',{'username':'fixture-owner','password':PASSWORD})
            assert '; Secure' in headers['Set-Cookie']
            old=next(iter(jar)).value
            assert next(iter(jar)).secure
            with opener.open(origin+'/api/auth/session',timeout=5) as response:assert response.status==200
            insecure=urllib.request.Request(origin.replace('https:','http:')+'/api/auth/session')
            jar.add_cookie_header(insecure);assert not insecure.has_header('Cookie')
            rotated,headers=post('/api/auth/reauthenticate',{'password':PASSWORD},login['csrf'])
            assert next(iter(jar)).value!=old and '; Secure' in headers['Set-Cookie']
            result,headers=post('/api/auth/logout',{},rotated['csrf'])
            assert result['status']=='SIGNED_OUT' and '; Secure' in headers['Set-Cookie'] and len(jar)==0


def test_repeated_shutdown_and_bound_port_failure_leave_no_threads_or_sockets(dashboard):
    for _ in range(5):
        server=create_dashboard_server(dashboard.app.Handler,TransportSettings(port=0))
        thread=Thread(target=server.run);thread.start()
        close_dashboard_server(server);thread.join(5)
        assert not thread.is_alive() and not server.server.task_dispatcher.threads and not server.server._map
        close_dashboard_server(server)
    active=create_dashboard_server(dashboard.app.Handler,TransportSettings(port=0))
    try:
        with pytest.raises(OSError):create_dashboard_server(dashboard.app.Handler,TransportSettings(port=int(active.effective_port)))
    finally:close_dashboard_server(active)


def test_malformed_proxy_header_values_are_never_logged(dashboard,caplog):
    with proxy_server(dashboard):
        code,_,body=request(dashboard,headers={'X-Forwarded-Proto':'synthetic-secret, https'})
        assert code==400
        assert 'PROXY_HEADER_REJECTED' in caplog.text
        assert 'synthetic-secret' not in caplog.text+body.decode()


def test_localhost_bind_is_one_loopback_listener_and_wrong_proxy_peer_is_rejected(dashboard):
    assert TransportSettings(host='localhost').host=='127.0.0.1'
    server=create_dashboard_server(dashboard.app.Handler,TransportSettings(host='localhost',port=0,trusted_proxy='127.0.0.2'))
    thread=Thread(target=server.run);thread.start();original=dashboard.url
    try:
        dashboard.url='http://127.0.0.1:'+str(server.effective_port)
        assert request(dashboard,path='/login',headers={'X-Forwarded-Proto':'https'})[0]==403
    finally:dashboard.url=original;close_dashboard_server(server);thread.join(5)


def test_handler_initialization_failure_is_redacted_and_not_retried(dashboard,monkeypatch,caplog):
    calls=[]
    def broken(self):calls.append(True);raise RuntimeError('synthetic-initialization-key')
    monkeypatch.setattr(dashboard.app.Handler,'setup',broken)
    code,headers,body=request(dashboard)
    assert code==500 and calls==[True]
    assert json.loads(body)['correlation_id']==headers['X-Request-ID']
    assert 'synthetic-initialization-key' not in caplog.text+body.decode()
    assert 'Traceback' not in caplog.text


@pytest.mark.parametrize('host',['localhost:bad-port','localhost:70000','user@localhost','localhost/#extra'])
def test_malformed_authority_is_rejected_before_authentication(dashboard,host):
    assert request(dashboard,headers={'Host':host,'Cookie':'chief_session='+dashboard.credentials['raw']})[0] in {400,403}
