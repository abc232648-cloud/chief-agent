import socket
from types import SimpleNamespace
import pytest
import dns.query
import dns.resolver
from browser.dns_policy import resolve_addresses, selected_mode, DoHProvider
from browser.egress import public_addresses


def forbidden(*args, **kwargs):
    raise AssertionError('System DNS must not be called')


def test_secure_default_and_explicit_system(monkeypatch):
    monkeypatch.delenv('CHIEF_DNS_MODE', raising=False)
    assert selected_mode() == 'quad9'
    monkeypatch.setenv('CHIEF_DNS_MODE', 'system')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('93.184.215.14', 443))])
    assert resolve_addresses('example.org', 443) == ('93.184.215.14',)


@pytest.mark.parametrize('mode', ['', 'automatic', 'google', None, 1])
def test_invalid_configuration_fails_closed(monkeypatch, mode):
    from browser.dns_policy import validate_mode
    with pytest.raises(ValueError):
        validate_mode(mode)


def test_secure_transport_bootstrap_tls_h2_and_both_families(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    calls = []
    def resolve(self, host, kind, **kwargs):
        server = self.nameservers[0]
        calls.append((host, kind, kwargs))
        assert server.url == 'https://dns.quad9.net/dns-query'
        assert server.bootstrap_address == '9.9.9.9'
        assert server.verify is True
        assert server.http_version == dns.query.HTTPVersion.H2
        assert kwargs['search'] is False and 0 < kwargs['lifetime'] <= 5
        return [SimpleNamespace(address='93.184.215.14' if kind == 'A' else '2606:2800:220:1:248:1893:25c8:1946')]
    monkeypatch.setattr(dns.resolver.Resolver, 'resolve', resolve)
    assert len(resolve_addresses('example.org', 443, mode='quad9')) == 2
    assert [(c[0], c[1]) for c in calls] == [('example.org.', 'A'), ('example.org.', 'AAAA')]
    assert calls[1][2]['lifetime'] <= calls[0][2]['lifetime']


@pytest.mark.parametrize('error', [dns.resolver.NXDOMAIN(), dns.resolver.NoNameservers(), TimeoutError(), OSError('TLS failed')])
def test_secure_failure_never_falls_back(monkeypatch, error):
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    def resolve(*args, **kwargs):
        raise error
    monkeypatch.setattr(dns.resolver.Resolver, 'resolve', resolve)
    with pytest.raises(PermissionError, match='no fallback'):
        resolve_addresses('blocked.example', 443, mode='quad9')


@pytest.mark.parametrize('answers', [[], ['0.0.0.0'], ['93.184.215.14', '127.0.0.1'], ['::ffff:127.0.0.1']])
def test_empty_blocked_and_mixed_secure_answers_never_connect(monkeypatch, answers):
    monkeypatch.setenv('CHIEF_DNS_MODE', 'quad9')
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(dns.resolver.Resolver, 'resolve', lambda *a, **kw: [SimpleNamespace(address=x) for x in answers])
    with pytest.raises(PermissionError):
        public_addresses('example.org', 443)


def test_provider_metadata_can_change_without_transport_change(monkeypatch):
    def resolve(self, *args, **kwargs):
        assert self.nameservers[0].url == 'https://resolver.example/dns-query'
        assert self.nameservers[0].bootstrap_address == '93.184.215.14'
        return [SimpleNamespace(address='93.184.215.14')]
    monkeypatch.setattr(dns.resolver.Resolver, 'resolve', resolve)
    assert resolve_addresses('example.org', 443, mode='quad9',
        provider=DoHProvider('https://resolver.example/dns-query', '93.184.215.14')) == ('93.184.215.14',)


def test_https_pins_address_and_preserves_tls_hostname(monkeypatch):
    import browser.https_transport as transport
    calls = []
    monkeypatch.setattr(transport, 'public_addresses', lambda *a: ('93.184.215.14',))
    raw = object()
    monkeypatch.setattr(socket, 'create_connection', lambda address, timeout: calls.append(address) or raw)
    connection = transport.ChiefHTTPSConnection('example.org', timeout=2)
    connection._context = SimpleNamespace(wrap_socket=lambda sock, server_hostname: calls.append((sock, server_hostname)) or raw)
    connection.connect()
    assert calls == [('93.184.215.14', 443), (raw, 'example.org')]


def test_https_rejects_redirect_before_second_request():
    from browser.https_transport import NoRedirect
    from urllib.request import Request
    from urllib.error import HTTPError
    with pytest.raises(HTTPError):
        NoRedirect().redirect_request(Request('https://example.org'), None, 302, 'redirect', {}, 'https://other.example')


def test_login_proxy_policy_retains_public_https_only():
    from browser.request_policy import UserLoginPolicy
    policy = UserLoginPolicy()
    assert ('https', 'identity.example', 443) in policy.origins
    assert ('http', 'identity.example', 80) not in policy.origins


def test_real_resolver_uses_only_secure_wire_transport(monkeypatch):
    import dns.message
    import dns.rrset
    calls = []
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    def https(query, where, **kwargs):
        calls.append((where, kwargs))
        assert kwargs['bootstrap_address'] == '9.9.9.9'
        assert kwargs['verify'] is True
        assert kwargs['http_version'] == dns.query.HTTPVersion.H2
        response = dns.message.make_response(query)
        kind = query.question[0].rdtype
        if kind == 1:
            rrset = response.find_rrset(
                response.answer,
                query.question[0].name,
                query.question[0].rdclass,
                query.question[0].rdtype,
                create=True,
            )
            rrset.update(dns.rrset.from_text('example.org.', 30, 'IN', 'A', '93.184.215.14'))
        return response
    monkeypatch.setattr(dns.query, 'https', https)
    assert resolve_addresses('example.org',443,mode='quad9') == ('93.184.215.14',)
    assert len(calls) == 2
