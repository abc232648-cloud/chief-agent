"""Lightweight authoritative DNS daemon for Chief WireGuard split-DNS.

Binds exclusively to the WireGuard server VPN address (default: 10.77.0.1:53).
Answers A queries for the configured Chief canonical hostname with 10.77.0.1.
Responds with REFUSED (RCODE=5) for all other domains, preventing open recursive resolver abuse.
Operates using standard Python socket primitives without third-party external dependencies.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def parse_dns_question(data: bytes, offset: int = 12) -> Tuple[str, int, int, int]:
    """Parse RFC 1035 QNAME, QTYPE, and QCLASS from a DNS query payload.

    Returns:
        (qname_str, qtype, qclass, end_offset)
    """
    labels = []
    curr = offset
    length_limit = len(data)
    while curr < length_limit:
        length = data[curr]
        if length == 0:
            curr += 1
            break
        # Pointer check (not standard in queries, but safe guard)
        if (length & 0xC0) == 0xC0:
            curr += 2
            break
        curr += 1
        if curr + length > length_limit:
            raise ValueError("Malformed DNS label length")
        labels.append(data[curr : curr + length].decode("ascii", errors="ignore"))
        curr += length

    qname = ".".join(labels).lower()
    if curr + 4 > length_limit:
        raise ValueError("Truncated DNS question section")
    qtype, qclass = struct.unpack("!HH", data[curr : curr + 4])
    curr += 4
    return qname, qtype, qclass, curr


def build_dns_response(query_data: bytes, canonical_hostname: str, answer_ip: str) -> bytes:
    """Build an RFC 1035 compliant response.

    If query is for canonical_hostname (or root-relative equivalent), returns A record answer.
    For all other domains, returns REFUSED (RCODE 5) with zero answers to prevent open resolver abuse.
    """
    if len(query_data) < 12:
        return b""

    trans_id = query_data[:2]
    try:
        qname, qtype, qclass, q_end = parse_dns_question(query_data, 12)
    except Exception:
        # Format error
        flags = struct.pack("!H", 0x8101)  # QR=1, RCODE=1 (Format Error)
        return trans_id + flags + struct.pack("!HHHH", 0, 0, 0, 0)

    question_section = query_data[12:q_end]
    canonical_clean = canonical_hostname.strip().lower().rstrip(".")
    qname_clean = qname.strip().lower().rstrip(".")

    rd_flag = query_data[2] & 0x01

    if qname_clean == canonical_clean and qtype in (1, 255):  # Type A (1) or ANY (255)
        # 0x8400: Response (QR=1), Authoritative (AA=1), No Error (RCODE=0)
        flags = struct.pack("!H", 0x8400 | (rd_flag << 8))
        counts = struct.pack("!HHHH", 1, 1, 0, 0)
        header = trans_id + flags + counts

        # Answer RR pointing to question QNAME at offset 0x000c
        ip_bytes = socket.inet_aton(answer_ip)
        # Name pointer 0xc00c, Type A (1), Class IN (1), TTL (60s), RdLength (4)
        answer = struct.pack("!HHHIH", 0xC00C, 1, 1, 60, 4) + ip_bytes
        return header + question_section + answer
    else:
        # Refuse all other domains: 0x8105 (Response, Query Refused)
        flags = struct.pack("!H", 0x8105 | (rd_flag << 8))
        counts = struct.pack("!HHHH", 1, 0, 0, 0)
        header = trans_id + flags + counts
        return header + question_section


class WireGuardDNSServer:
    """UDP DNS Server that strictly answers canonical hostname lookups on the WireGuard VPN interface."""

    def __init__(
        self,
        bind_ip: str = "10.77.0.1",
        port: int = 53,
        canonical_hostname: str = "chief-farm.local",
        answer_ip: str = "10.77.0.1",
    ):
        self.bind_ip = bind_ip
        self.port = port
        self.canonical_hostname = canonical_hostname
        self.answer_ip = answer_ip
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the DNS server in a background daemon thread."""
        if self.is_running:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.bind_ip, self.port))
        except Exception as exc:
            sock.close()
            raise OSError(f"Failed to bind WireGuard DNS server on {self.bind_ip}:{self.port}: {exc}") from exc

        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._serve, name="ChiefWireGuardDNS", daemon=True)
        self._thread.start()
        logger.info("WireGuard split-DNS server listening on %s:%d for %s -> %s", self.bind_ip, self.port, self.canonical_hostname, self.answer_ip)

    def _serve(self) -> None:
        while self._running and self._sock:
            try:
                self._sock.settimeout(0.5)
                data, addr = self._sock.recvfrom(2048)
                resp = build_dns_response(data, self.canonical_hostname, self.answer_ip)
                if resp:
                    self._sock.sendto(resp, addr)
            except (socket.timeout, OSError):
                continue
            except Exception as exc:
                logger.debug("DNS query handler error: %s", exc)

    def stop(self) -> None:
        """Stop the DNS server and release the socket."""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("WireGuard split-DNS server stopped.")


def main(argv: Optional[List[str]] = None) -> int:
    """Standalone CLI entrypoint for running the authoritative split-DNS daemon."""
    import argparse
    import sys
    import time
    from typing import List

    parser = argparse.ArgumentParser(description="Chief Authoritative WireGuard Split-DNS Daemon")
    parser.add_argument("--bind", default="10.77.0.1", help="Virtual IP to bind to (default: 10.77.0.1)")
    parser.add_argument("--port", type=int, default=53, help="UDP port (default: 53)")
    parser.add_argument("--hostname", required=True, help="Canonical Chief hostname to answer authoritatively")
    parser.add_argument("--answer", default="10.77.0.1", help="IPv4 address returned for the canonical hostname")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    server = WireGuardDNSServer(
        bind_ip=args.bind,
        port=args.port,
        canonical_hostname=args.hostname,
        answer_ip=args.answer,
    )
    server.start()
    try:
        while server.is_running:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
