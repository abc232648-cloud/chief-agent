from urllib.parse import urlsplit

from tests.test_http_transport import proxy_server, request


def test_production_rejects_plaintext_even_on_loopback(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    code,headers,body=request(dashboard,path='/login')
    assert code==403
    assert b'trusted TLS connection is required' in body
    assert headers['X-Content-Type-Options']=='nosniff'
    assert headers['X-Frame-Options']=='DENY'
    assert headers['Referrer-Policy']=='no-referrer'
    assert headers['Permissions-Policy']=='camera=(self), microphone=(), geolocation=()'
    assert headers.get('Strict-Transport-Security') is None


def test_production_trusted_proxy_https_gets_hsts_and_browser_hardening(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    with proxy_server(dashboard) as target:
        host=urlsplit(target.url).netloc
        code,headers,_=request(target,path='/login',headers={
            'Host':host,'X-Forwarded-Proto':'https'
        })
        assert code==200
        assert headers['Strict-Transport-Security']=='max-age=31536000'
        assert headers['X-Content-Type-Options']=='nosniff'
        assert headers['X-Frame-Options']=='DENY'
        assert headers['Referrer-Policy']=='no-referrer'
        assert headers['Permissions-Policy']=='camera=(self), microphone=(), geolocation=()'


def test_test_mode_keeps_isolated_loopback_http_for_qualification(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','test')
    code,headers,_=request(dashboard,path='/login')
    assert code==200
    assert headers.get('Strict-Transport-Security') is None
