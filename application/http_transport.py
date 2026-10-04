"""Private-dashboard WSGI boundary; application handlers never write to sockets."""
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from io import BytesIO
import ipaddress
import json
import logging
import os
import socket
import uuid
from threading import Event, Lock, get_ident
from urllib.parse import quote_from_bytes

BODY_LIMIT = 12 * 1024 * 1024
RESPONSE_LIMIT = 16 * 1024 * 1024
LOG = logging.getLogger('chief.http')


def diagnostic(code, correlation_id):
    # Never log request paths, headers, bodies, exception values or stack locals.
    LOG.warning('%s correlation=%s', code, correlation_id)


class ResponseBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > RESPONSE_LIMIT:
            raise RuntimeError('Response exceeds the transport limit.')
        return super().write(data)


class ResponseHandler:
    def __init__(self, environ):
        self.command = environ['REQUEST_METHOD']
        # WSGI PATH_INFO is decoded; existing domain routes expect escaped paths.
        self.path = quote_from_bytes(environ.get('PATH_INFO', '/').encode('latin-1'), safe='/')
        if environ.get('QUERY_STRING'):
            self.path += '?' + environ['QUERY_STRING']
        self.headers = Message()
        for name, value in environ.items():
            if name.startswith('HTTP_'):
                self.headers[name[5:].replace('_', '-')] = value
        for name in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            if name in environ:
                self.headers[name.replace('_', '-')] = environ[name]
        self.client_address = (environ.get('REMOTE_ADDR', ''), 0)
        self.scheme = environ.get('wsgi.url_scheme', 'http')
        self.secure_transport = self.scheme == 'https'
        self.rfile = environ['wsgi.input']
        self.wfile = ResponseBuffer()
        self.correlation_id = environ['chief.request_id']
        self.status = 200
        self.response_headers = []
        self.setup()

    def setup(self):
        """Compatibility hook; no socket or request parsing occurs here."""

    def send_response(self, code):
        self.status = int(code)
        self.response_headers = []
        self.wfile.seek(0)
        self.wfile.truncate()

    def send_header(self, name, value):
        if '\r' in name + value or '\n' in name + value:
            raise ValueError('Invalid response header.')
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def send_error(self, code):
        self.send_response(code)
        data = json.dumps({'status': HTTPStatus(code).name}).encode()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        if self.command != 'HEAD':
            self.wfile.write(data)

    def log_error(self, *unused):
        diagnostic('APPLICATION_ERROR', self.correlation_id)


@dataclass(frozen=True)
class TransportSettings:
    host: str = '127.0.0.1'
    port: int = 8765
    trusted_proxy: str | None = None

    def __post_init__(self):
        from application.auth_routes import private_bind
        private_bind(self.host)
        if self.host == 'localhost':object.__setattr__(self, 'host', '127.0.0.1')
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 0 <= self.port <= 65535:
            raise ValueError('Invalid dashboard port.')
        if self.trusted_proxy:
            peer = ipaddress.ip_address(self.trusted_proxy)
            bind = ipaddress.ip_address(self.host if self.host != 'localhost' else '127.0.0.1')
            if not peer.is_loopback or not bind.is_loopback:
                raise ValueError('TLS proxy mode requires an explicit same-host loopback proxy and listener.')

    @classmethod
    def from_environment(cls):
        return cls(os.getenv('DASHBOARD_HOST', '127.0.0.1'),
                   int(os.getenv('DASHBOARD_PORT', '8765')),
                   os.getenv('CHIEF_TRUSTED_PROXY') or None)


def wsgi_application(handler_type, settings):
    def application(environ, start_response):
        correlation_id = uuid.uuid4().hex
        from operations.correlation import scope
        with scope(request_id=correlation_id):
            return correlated_application(environ,start_response,correlation_id)

    def correlated_application(environ,start_response,correlation_id):
        environ['chief.request_id'] = correlation_id
        try:
            handler = handler_type(environ)
            # Waitress alone establishes scheme from the explicitly trusted peer.
            # A private LAN bind is not permission to serve credentials over HTTP.
            local = ipaddress.ip_address(handler.client_address[0]).is_loopback
            if (settings.trusted_proxy or not local) and not handler.secure_transport:
                handler.json({'status': 'REJECTED', 'reason': 'A trusted TLS connection is required.'}, 403)
            elif handler.command not in {'GET', 'HEAD', 'POST', 'DELETE'}:
                handler.send_error(405)
            else:
                getattr(handler, 'do_' + handler.command)()
        except Exception:
            # Initialization/response-construction failures cannot recursively call
            # a broken handler or hand a secret-bearing exception to server logs.
            diagnostic('APPLICATION_ERROR', correlation_id)
            data = json.dumps({'status':'FAILED','reason':'Server error.','correlation_id':correlation_id}).encode()
            headers = [('Content-Type','application/json'),('Cache-Control','no-store'),
                       ('Content-Length',str(len(data))),('X-Request-ID',correlation_id)]
            return deliver(start_response, '500 Internal Server Error', headers,
                           b'' if environ.get('REQUEST_METHOD')=='HEAD' else data, correlation_id)
        headers = handler.response_headers
        headers.append(('X-Request-ID', handler.correlation_id))
        if not any(k.lower() == 'content-length' for k, _ in headers):
            headers.append(('Content-Length', str(handler.wfile.tell())))
        data = b'' if handler.command == 'HEAD' else handler.wfile.getvalue()
        # No route re-entry or automatic mutation retry if response delivery fails.
        return deliver(start_response, f'{handler.status} {HTTPStatus(handler.status).phrase}',
                       headers, data, correlation_id)
    return application


def deliver(start_response, status, headers, data, correlation_id):
    try:start_response(status, headers)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        diagnostic('CLIENT_DISCONNECTED', correlation_id)
        return []
    return [data]


def create_dashboard_server(handler_type, settings=None):
    from waitress import create_server
    from waitress.task import ThreadedTaskDispatcher
    from waitress import wasyncore
    from waitress.proxy_headers import proxy_headers_middleware
    settings = settings or TransportSettings.from_environment()
    options = dict(threads=4,
                   connection_limit=32, backlog=16, channel_timeout=15,
                   cleanup_interval=1, max_request_header_size=16 * 1024,
                   max_request_body_size=BODY_LIMIT, inbuf_overflow=BODY_LIMIT + 1,
                   outbuf_overflow=RESPONSE_LIMIT + 1, outbuf_high_watermark=1024 * 1024,
                   clear_untrusted_proxy_headers=False, log_untrusted_proxy_headers=False,
                   log_socket_errors=False, expose_tracebacks=False, ident='',
                   asyncore_loop_timeout=0.1, channel_request_lookahead=0)
    # Use Waitress's maintained parser with a scoped, redacted logger. The stock
    # middleware logs malformed header *values*, which may contain secrets.
    class ProxyDiagnostics:
        def warning(self, *unused):diagnostic('PROXY_HEADER_REJECTED', uuid.uuid4().hex)
    application = proxy_headers_middleware(wsgi_application(handler_type, settings),
        trusted_proxy=settings.trusted_proxy, trusted_proxy_count=1,
        trusted_proxy_headers={'x-forwarded-proto'} if settings.trusted_proxy else set(),
        clear_untrusted=True, log_untrusted=False, logger=ProxyDiagnostics())
    dispatcher = ThreadedTaskDispatcher()
    socket_map = {}
    listener = socket.socket(socket.AF_INET6 if ':' in settings.host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == 'nt':listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if listener.family == socket.AF_INET6:listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind((settings.host, settings.port))
        server = create_server(application, sockets=[listener],
                               map=socket_map, _dispatcher=dispatcher, **options)
        dispatcher.set_thread_count(options['threads'])
    except Exception:
        dispatcher.shutdown()
        wasyncore.close_all(socket_map)
        listener.close()
        raise
    return DashboardServer(server)


class DashboardServer:
    """The event-loop thread owns socket closure, including Windows select handles."""
    def __init__(self, server):
        self.server = server
        self.effective_port = server.effective_port
        self.effective_host = server.effective_host
        self.stop_requested = Event()
        self.stopped = Event()
        self.running = Event()
        self.cleanup_lock = Lock()
        self.thread_id = None

    def _cleanup(self):
        from waitress import wasyncore
        with self.cleanup_lock:
            if self.stopped.is_set():return
            self.server.accepting = False
            self.server.task_dispatcher.shutdown(timeout=5)
            wasyncore.close_all(self.server._map)
            self.stopped.set()

    def run(self):
        from waitress import wasyncore
        self.thread_id = get_ident()
        self.running.set()
        try:
            while not self.stop_requested.is_set():
                wasyncore.loop(timeout=0.1, count=1, map=self.server._map)
        except KeyboardInterrupt:
            pass
        finally:self._cleanup()

    def close(self):
        self.stop_requested.set()
        if not self.running.is_set() or self.thread_id == get_ident():
            self._cleanup()
        elif not self.stopped.wait(10):
            raise TimeoutError('Dashboard transport shutdown did not complete.')


def close_dashboard_server(server):
    server.close()
