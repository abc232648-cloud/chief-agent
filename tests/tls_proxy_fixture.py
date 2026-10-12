"""Disposable, loopback-only TLS fixture; never an operational proxy configuration."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import ipaddress
import os
import ssl
from threading import Thread


@contextmanager
def local_tls_proxy(root, backend_port, *, bundles=None, faults=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key=ec.generate_private_key(ec.SECP256R1())
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Chief isolated TLS fixture')])
    now=datetime.now(timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
        .sign(key,hashes.SHA256()))
    cert_path=root/'ephemeral-tls-cert.pem';key_path=root/'ephemeral-tls-key.pem'
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    fd=os.open(key_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path,key_path)
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def forward(self):
            # Fail before static serving or backend forwarding. This models an
            # unreachable Chief origin without disabling local Blob/File I/O.
            if faults is not None and faults.get('network_unavailable'):
                faults['blocked_requests'] = faults.get('blocked_requests', 0) + 1
                self.close_connection = True
                return
            # Optional production bundles for isolated paired-client acceptance.
            # Never serve API responses or another client's fallback from disk.
            if bundles and self.command in {'GET', 'HEAD'}:
                from pathlib import Path
                from urllib.parse import unquote, urlsplit
                import mimetypes
                path = unquote(urlsplit(self.path).path)
                for prefix, directory in bundles.items():
                    if path.startswith(prefix):
                        base = Path(directory).resolve()
                        target = (base / (path[len(prefix):] or 'index.html')).resolve()
                        if not target.is_relative_to(base) or not target.is_file():
                            self.send_error(404)
                            return
                        data = target.read_bytes()
                        self.send_response(200)
                        self.send_header('Content-Type', mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                        self.send_header('Content-Length', str(len(data)))
                        self.send_header('Cache-Control', 'no-store')
                        self.end_headers()
                        if self.command != 'HEAD':
                            self.wfile.write(data)
                        return
            backend=http.client.HTTPConnection('127.0.0.1',backend_port,timeout=5)
            try:
                data=self.rfile.read(int(self.headers.get('Content-Length','0')))
                headers={k:v for k,v in self.headers.items() if not k.lower().startswith(('x-forwarded-','forwarded')) and k.lower()!='connection'}
                headers['X-Forwarded-Proto']='https'
                backend.request(self.command,self.path,body=data or None,headers=headers)
                response=backend.getresponse();body=response.read()
                if faults is not None and faults.get('drop_response') == (self.command, self.path):
                    faults['accepted_status'] = response.status
                    # Keep the outage active until the test restores transport.
                    # Browsers may transparently retry a disconnected request.
                    faults.setdefault('dropped_statuses', []).append(response.status)
                    self.close_connection = True
                    return
                self.send_response(response.status)
                for k,v in response.getheaders():
                    if k.lower() not in {'server','date','connection','transfer-encoding'}:self.send_header(k,v)
                self.end_headers();self.wfile.write(body)
            finally:backend.close()
        do_GET=do_POST=do_HEAD=forward
    server=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'https://127.0.0.1:'+str(server.server_port),ssl.create_default_context(cafile=cert_path)
    finally:
        server.shutdown();server.server_close();thread.join(5)
        key_path.unlink();cert_path.unlink()
