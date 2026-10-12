"""Bounded loopback-only reproduction; no actual n8n or credentials involved."""
import socket
import threading
import time
import pytest
from integrations import n8n


def test_slow_headers_are_covered_by_total_inventory_deadline(monkeypatch):
    listener=socket.socket()
    listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(12)
    stop=threading.Event()
    def serve():
        try:
            peer,_=listener.accept()
            with peer:
                peer.settimeout(2)
                request=b''
                while b'\r\n\r\n' not in request:
                    request+=peer.recv(4096)
                peer.sendall(b'HTTP/1.1 200 OK\r\nX-Synthetic: ')
                for _ in range(35):
                    if stop.wait(.2):return
                    peer.sendall(b'x')
                peer.sendall(b'\r\nContent-Length: 11\r\n\r\n{"data":[]} ')
        except (OSError,TimeoutError):
            pass
        finally:
            listener.close()
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    monkeypatch.setenv('CHIEF_N8N_URL',f'http://127.0.0.1:{listener.getsockname()[1]}')
    monkeypatch.setenv('CHIEF_N8N_API_KEY','synthetic-review-only')
    start=time.monotonic()
    try:
        with pytest.raises(ValueError):n8n.inventory()
        elapsed=time.monotonic()-start
        assert elapsed<6, f'Five-second total deadline exceeded: {elapsed:.2f}s during headers'
    finally:
        stop.set();thread.join(timeout=3)
