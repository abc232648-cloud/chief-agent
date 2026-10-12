"""The browser outage fixture must block actual transport, never fake success."""
import http.client
from urllib.parse import urlsplit

import pytest

from tests.test_http_transport import proxy_server
from tests.tls_proxy_fixture import local_tls_proxy


def test_outage_blocks_static_and_api_before_backend_and_restores(dashboard, tmp_path, monkeypatch):
    upstream = []
    original = dashboard.app.Handler.do_GET

    def record_get(handler):
        upstream.append(handler.path)
        return original(handler)

    monkeypatch.setattr(dashboard.app.Handler, 'do_GET', record_get)
    bundle = tmp_path / 'static'
    bundle.mkdir()
    (bundle / 'index.html').write_text('synthetic shell')
    faults = {}
    before = dashboard.store.counts()
    with proxy_server(dashboard):
        with local_tls_proxy(tmp_path, urlsplit(dashboard.url).port,
                             bundles={'/staff/': bundle}, faults=faults) as (origin, trust):
            def request(method, path):
                connection = http.client.HTTPSConnection('127.0.0.1', urlsplit(origin).port,
                                                         context=trust, timeout=5)
                try:
                    connection.request(method, path)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            assert request('GET', '/staff/') == (200, b'synthetic shell')
            faults['network_unavailable'] = True
            for method, path in [('GET', '/staff/'), ('GET', '/api/auth/session'),
                                 ('POST', '/api/farm/worker-reports')]:
                with pytest.raises(http.client.RemoteDisconnected):
                    request(method, path)
            assert faults['blocked_requests'] == 3
            assert not upstream
            assert dashboard.store.counts() == before
            faults['network_unavailable'] = False
            assert request('GET', '/staff/') == (200, b'synthetic shell')
            assert request('GET', '/api/auth/session')[0] in (200, 401)
            assert upstream == ['/api/auth/session']
