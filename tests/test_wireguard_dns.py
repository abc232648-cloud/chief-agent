"""Tests for Chief WireGuard split-DNS server and RFC 1035 query processing."""
import socket
import struct
import time
import pytest

from networking.wireguard_dns import (
    WireGuardDNSServer,
    build_dns_response,
    parse_dns_question,
)


def test_parse_dns_question():
    # 10chief-farm5local0 + QTYPE(1) + QCLASS(1)
    wire_q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x0achief-farm\x05local\x00\x00\x01\x00\x01"
    qname, qtype, qclass, end = parse_dns_question(wire_q, 12)
    assert qname == "chief-farm.local"
    assert qtype == 1  # Type A
    assert qclass == 1  # Class IN
    assert end == len(wire_q)


def test_build_dns_response_canonical_hostname():
    wire_q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x0achief-farm\x05local\x00\x00\x01\x00\x01"
    resp = build_dns_response(wire_q, "chief-farm.local", "10.77.0.1")

    # Header check
    assert resp[:2] == b"\x12\x34"  # Matching transaction ID
    flags, qd, an, ns, ar = struct.unpack("!HHHHH", resp[2:12])
    assert (flags & 0x8000) != 0  # QR = 1 (Response)
    assert (flags & 0x0400) != 0  # AA = 1 (Authoritative)
    assert (flags & 0x000F) == 0  # RCODE = 0 (No Error)
    assert qd == 1
    assert an == 1

    # Answer check
    assert resp[-4:] == socket.inet_aton("10.77.0.1")


def test_build_dns_response_external_refusal():
    # External query for google.com
    wire_q = b"\xab\xcd\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x06google\x03com\x00\x00\x01\x00\x01"
    resp = build_dns_response(wire_q, "chief-farm.local", "10.77.0.1")

    flags, qd, an, ns, ar = struct.unpack("!HHHHH", resp[2:12])
    assert (flags & 0x8000) != 0  # QR = 1
    assert (flags & 0x000F) == 5  # RCODE = 5 (REFUSED)
    assert an == 0  # Zero answers, strictly avoiding open recursive resolution


def test_wireguard_dns_server_socket_interaction():
    # Use ephemeral high port on localhost for testing
    server = WireGuardDNSServer(
        bind_ip="127.0.0.1",
        port=19553,
        canonical_hostname="chief-host.local",
        answer_ip="10.77.0.1",
    )
    server.start()
    assert server.is_running is True

    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(2.0)

        # 1. Query for chief-host.local
        q = b"\x44\x55\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x0achief-host\x05local\x00\x00\x01\x00\x01"
        client.sendto(q, ("127.0.0.1", 19553))
        resp, _ = client.recvfrom(2048)
        assert resp[:2] == b"\x44\x55"
        assert resp[-4:] == socket.inet_aton("10.77.0.1")

        # 2. Query for external domain
        q_ext = b"\x77\x88\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01"
        client.sendto(q_ext, ("127.0.0.1", 19553))
        resp_ext, _ = client.recvfrom(2048)
        flags, _, an, _, _ = struct.unpack("!HHHHH", resp_ext[2:12])
        assert (flags & 0x000F) == 5  # REFUSED
        assert an == 0
    finally:
        server.stop()
        assert server.is_running is False
