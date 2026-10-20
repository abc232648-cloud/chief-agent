"""Public HTTPS adapter for Chief-owned urllib clients; no ambient proxy bypass."""
import http.client
import socket
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, build_opener
from urllib.error import HTTPError
from urllib.parse import urlsplit
from networking.dns_policy import public_addresses


class ChiefHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self._tunnel_host:
            raise PermissionError('External proxy tunnelling is not supported by Chief HTTPS.')
        for address in public_addresses(self.host, self.port):
            raw = None
            try:
                raw = socket.create_connection((address, self.port), self.timeout)
                # Keep original hostname for SNI and certificate verification.
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError:
                if raw is not None:
                    raw.close()
        raise OSError('Chief HTTPS destination unavailable.')


class ChiefHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(ChiefHTTPSConnection, request, context=self._context)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Caller must approve each new URL before any redirected request is sent.
        raise HTTPError(request.full_url, code, 'Chief requires an explicitly approved direct URL.', headers, fp)


def urlopen(request, timeout=30):
    url = request.full_url if hasattr(request, 'full_url') else request
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise PermissionError('Chief public transport requires credential-free HTTPS.')
    return build_opener(ProxyHandler({}), ChiefHTTPSHandler(), NoRedirect()).open(request, timeout=timeout)
