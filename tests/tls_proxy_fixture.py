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
def local_tls_proxy(root, backend_port):
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
            backend=http.client.HTTPConnection('127.0.0.1',backend_port,timeout=5)
            try:
                data=self.rfile.read(int(self.headers.get('Content-Length','0')))
                headers={k:v for k,v in self.headers.items() if not k.lower().startswith(('x-forwarded-','forwarded')) and k.lower()!='connection'}
                headers['X-Forwarded-Proto']='https'
                backend.request(self.command,self.path,body=data or None,headers=headers)
                response=backend.getresponse();body=response.read()
                self.send_response(response.status)
                for k,v in response.getheaders():
                    if k.lower() not in {'server','date','connection','transfer-encoding'}:self.send_header(k,v)
                self.end_headers();self.wfile.write(body)
            finally:backend.close()
        do_GET=do_POST=forward
    server=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'https://127.0.0.1:'+str(server.server_port),ssl.create_default_context(cafile=cert_path)
    finally:
        server.shutdown();server.server_close();thread.join(5)
        key_path.unlink();cert_path.unlink()
