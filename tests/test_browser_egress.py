import socket
from types import SimpleNamespace
from urllib.parse import urlsplit
import pytest
from browser.request_policy import RequestPolicy,origin,BrowserPolicyBlocked
from browser.egress import EgressProxy,connect_pinned,public_addresses
from browser.environment import browser_environment


@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','169.254.169.254','::1','fe80::1','::ffff:127.0.0.1','224.0.0.1','0.0.0.0','64:ff9b::7f00:1','2002:7f00:1::'])
def test_connection_time_dns_rejects_nonpublic_and_mixed_answers(monkeypatch,address):
    answers=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(a,443)) for a in ('93.184.215.14',address)]
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:answers)
    with pytest.raises(PermissionError):public_addresses('approved.fixture.test',443)


def test_connection_uses_validated_numeric_ip_not_second_hostname_resolution(monkeypatch):
    import browser.egress as egress
    calls=[]
    monkeypatch.setattr(egress,'public_addresses',lambda *a:('93.184.215.14',))
    def connect(address,timeout):
        calls.append(address);return SimpleNamespace(getpeername=lambda:address,close=lambda:None)
    monkeypatch.setattr(socket,'create_connection',connect)
    connect_pinned('approved.fixture.test',443)
    assert calls==[('93.184.215.14',443)]


@pytest.mark.parametrize('url',['http://example.com','https://127.1','https://localhost','https://x.local','https://user:secret@example.com','https://example.com:444'])
def test_origin_and_connection_boundaries(url):
    if url=='https://127.1':
        with pytest.raises(PermissionError):public_addresses('127.1',443)
        return
    with pytest.raises(BrowserPolicyBlocked):origin(url)


def test_real_local_proxy_connection_blocks_private_dns_before_connect(monkeypatch):
    import browser.egress as egress
    called=[]
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    # Construct without getaddrinfo: actual local TCP, no external network target.
    policy=RequestPolicy('https://approved.fixture.test')
    with EgressProxy(policy) as proxy:
        client=socket.socket();client.settimeout(5);client.connect(('127.0.0.1',urlsplit(proxy.url).port))
        client.sendall(b'CONNECT approved.fixture.test:443 HTTP/1.1\r\nHost: approved.fixture.test:443\r\n\r\n')
        assert b'200' not in client.recv(4096)
        client.close()
    assert 'EGRESS_CONNECTION_REFUSED' in policy.events


def test_browser_environment_excludes_provider_and_proxy_secrets():
    result=browser_environment({'PATH':'fixture','HOME':'fixture-home','GROQ_API_KEY':'canary','MISTRAL_API_KEY':'canary','HTTP_PROXY':'canary','CHIEF_SECRET_ROOT':'canary','AWS_SECRET_ACCESS_KEY':'canary'})
    assert result=={'PATH':'fixture','HOME':'fixture-home'}
