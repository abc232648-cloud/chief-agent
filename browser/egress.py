"""Bounded local CONNECT proxy with per-connection public DNS pinning.

Application-layer egress boundary, not an OS firewall or browser-exploit sandbox.
TLS is end-to-end; the separate browser route policy checks methods and URLs.
"""
import ipaddress
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def public_addresses(host, port):
    if host.replace('.','').isdigit():
        try:ipaddress.IPv4Address(host)
        except ValueError:raise PermissionError('Abbreviated numeric destinations are blocked.') from None
    try:records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:raise PermissionError('Destination could not be verified.') from None
    addresses = tuple(dict.fromkeys(record[4][0] for record in records))
    def permitted(value):
        address=ipaddress.ip_address(value)
        if not address.is_global or address.is_multicast:return False
        if address.version==6 and (address.ipv4_mapped or address.sixtofour or address.teredo
                or address in ipaddress.ip_network('64:ff9b::/96') or address in ipaddress.ip_network('64:ff9b:1::/48')):return False
        return True
    if not addresses or not all(permitted(a) for a in addresses):
        raise PermissionError('Destination is not exclusively public.')
    return addresses


def connect_pinned(host, port):
    addresses = public_addresses(host, port)
    for address in addresses:
        try:
            channel = socket.create_connection((address, port), timeout=5)
            if ipaddress.ip_address(channel.getpeername()[0]) != ipaddress.ip_address(address):
                channel.close();raise PermissionError('Pinned destination mismatch.')
            return channel
        except OSError:
            continue
    raise OSError('Public destination unavailable.')


class EgressProxy:
    def __init__(self, policy):self.policy=policy

    def __enter__(self):
        policy=self.policy
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def setup(self):
                super().setup();self.connection.settimeout(5)
            def log_message(self,*args):pass # URLs/headers can contain private data.
            def deny(self):
                policy.block('EGRESS_DENIED')
                self.send_response(403);self.send_header('Content-Length','0');self.send_header('Connection','close');self.end_headers()
                self.close_connection=True
            def do_CONNECT(self):
                upstream=None
                try:
                    target=urlsplit('https://'+self.path)
                    if (target.username or target.password or target.path or target.query or target.fragment
                            or target.port!=443 or ('https',target.hostname,443) not in policy.origins
                            or policy.phase=='FILL'):
                        self.deny();return
                    upstream=connect_pinned(target.hostname,443)
                    self.send_response(200);self.end_headers();self.wfile.flush()
                    deadline=time.monotonic()+40;total=0
                    while time.monotonic()<deadline and total<16*1024*1024 and not self.server.stopping.is_set():
                        ready,_,_=select.select([self.connection,upstream],[],[],0.25)
                        for stream in ready:
                            if policy.phase=='FILL':return
                            data=stream.recv(32768)
                            if not data:return
                            if total+len(data)>16*1024*1024:return
                            total+=len(data)
                            if policy.phase=='FILL':return
                            (upstream if stream is self.connection else self.connection).sendall(data)
                except Exception:
                    policy.block('EGRESS_CONNECTION_REFUSED')
                finally:
                    if upstream:upstream.close()
                    self.close_connection=True
            do_GET=do_HEAD=do_POST=do_PUT=do_DELETE=do_OPTIONS=lambda self:self.deny()
        class Server(ThreadingHTTPServer):
            daemon_threads=True
            request_queue_size=8
            def process_request(self,request,address):
                if not self.slots.acquire(blocking=False):request.close();return
                try:super().process_request(request,address)
                except BaseException:self.slots.release();raise
            def process_request_thread(self,request,address):
                try:super().process_request_thread(request,address)
                finally:self.slots.release()
        self.server=Server(('127.0.0.1',0),Handler)
        self.server.slots=threading.BoundedSemaphore(8);self.server.stopping=threading.Event()
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.1},daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        return self

    def __exit__(self,*args):
        self.server.stopping.set();self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
