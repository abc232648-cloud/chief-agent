from urllib.parse import urlsplit

from tests.test_http_transport import proxy_server, request


def test_production_rejects_plaintext_even_on_loopback(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    monkeypatch.setenv('DASHBOARD_TRUSTED_HOST','chief.example.com')
    code,headers,body=request(dashboard,path='/login',headers={'Host':'chief.example.com'})
    assert code==403
    assert b'trusted TLS connection is required' in body
    assert headers['X-Content-Type-Options']=='nosniff'
    assert headers['X-Frame-Options']=='DENY'
    assert headers['Referrer-Policy']=='no-referrer'
    assert headers['Permissions-Policy']=='camera=(self), microphone=(), geolocation=()'
    assert headers.get('Strict-Transport-Security') is None


def test_production_trusted_proxy_https_gets_hsts_and_browser_hardening(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    monkeypatch.setenv('DASHBOARD_TRUSTED_HOST','chief.example.com')
    with proxy_server(dashboard) as target:
        code,headers,_=request(target,path='/login',headers={
            'Host':'chief.example.com','X-Forwarded-Proto':'https'
        })
        assert code==200
        assert headers['Strict-Transport-Security']=='max-age=31536000'
        assert headers['X-Content-Type-Options']=='nosniff'
        assert headers['X-Frame-Options']=='DENY'
        assert headers['Referrer-Policy']=='no-referrer'
        assert headers['Permissions-Policy']=='camera=(self), microphone=(), geolocation=()'


def test_production_rejects_missing_or_alias_host_before_application(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    monkeypatch.setenv('DASHBOARD_TRUSTED_HOST','chief.example.com')
    with proxy_server(dashboard) as target:
        for host in ('localhost','127.0.0.1','evil.example','chief.example.com:443'):
            code,headers,body=request(target,path='/login',headers={
                'Host':host,'X-Forwarded-Proto':'https'
            })
            assert code==403 and b'reviewed production host' in body
            assert headers['Strict-Transport-Security']=='max-age=31536000'


def test_production_without_reviewed_host_fails_closed(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    monkeypatch.delenv('DASHBOARD_TRUSTED_HOST',raising=False)
    with proxy_server(dashboard) as target:
        code,_,body=request(target,path='/login',headers={
            'Host':'chief.example.com','X-Forwarded-Proto':'https'
        })
        assert code==403 and b'reviewed production host' in body


def test_test_mode_keeps_isolated_loopback_http_for_qualification(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','test')
    code,headers,_=request(dashboard,path='/login')
    assert code==200
    assert headers.get('Strict-Transport-Security') is None
