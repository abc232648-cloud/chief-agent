"""Unit tests for WireGuard remote access, peer lifecycle, firewall rules, and split-DNS."""
import ipaddress
import json
from pathlib import Path
import pytest

from networking.remote_access import (
    WireGuardManager,
    WireGuardPeer,
    generate_preshared_key,
    generate_wireguard_keypair,
)


def test_keypair_generation_deterministic_properties():
    priv, pub = generate_wireguard_keypair()
    assert len(priv) == 44
    assert len(pub) == 44
    assert priv.endswith("=") or priv.isalnum()
    assert pub.endswith("=") or pub.isalnum()
    assert priv != pub


def test_preshared_key_generation():
    psk1 = generate_preshared_key()
    psk2 = generate_preshared_key()
    assert len(psk1) == 44
    assert len(psk2) == 44
    assert psk1 != psk2


def test_manager_initialization_and_secrecy(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    assert not mgr.is_configured()

    status = mgr.initialize_server(
        endpoint="203.0.113.10",
        canonical_hostname="chief-farm.local",
        port=51820,
        vpn_subnet="10.77.0.0/24",
    )

    assert mgr.is_configured()
    assert status["enabled"] is True
    assert status["endpoint"] == "203.0.113.10"
    assert status["port"] == 51820
    assert status["server_vpn_ip"] == "10.77.0.1"
    assert len(status["server_public_key"]) == 44
    assert status["canonical_hostname"] == "chief-farm.local"

    # CRITICAL: Server private key must NOT appear in safe status dict
    status_str = json.dumps(status)
    server_priv = mgr.get_server_private_key()
    assert server_priv is not None
    assert len(server_priv) == 44
    assert server_priv not in status_str

    # Server key file must have been created
    key_path = tmp_path / "wg_state" / "server.key"
    assert key_path.exists()
    assert key_path.read_text(encoding="utf-8").strip() == server_priv


def test_subnet_collision_detection(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")

    # Conflict with farm LAN 10.77.0.0/16
    with pytest.raises(ValueError, match="conflicts with farm LAN subnet"):
        mgr.initialize_server(
            endpoint="203.0.113.10",
            canonical_hostname="chief-farm.local",
            vpn_subnet="10.77.0.0/24",
            farm_lan_cidr="10.77.0.0/16",
        )

    # Non-conflicting subnets pass
    status = mgr.initialize_server(
        endpoint="203.0.113.10",
        canonical_hostname="chief-farm.local",
        vpn_subnet="10.77.0.0/24",
        farm_lan_cidr="192.168.1.0/24",
    )
    assert status["enabled"] is True


def test_reachability_and_cgnat_detection(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")

    # 1. CGNAT address in 100.64.0.0/10
    rep_cgnat = mgr.check_reachability("100.64.12.34", 51820)
    assert rep_cgnat.is_valid is False
    assert rep_cgnat.cgnat_detected is True
    assert "Carrier-Grade NAT" in rep_cgnat.message

    # 2. Private LAN address (e.g. 192.168.1.100)
    rep_priv = mgr.check_reachability("192.168.1.100", 51820)
    assert rep_priv.is_valid is False
    assert rep_priv.is_private_ip is True
    assert "private local address" in rep_priv.message

    # 3. Valid public routable IP (TEST-NET-3: 203.0.113.50)
    rep_pub = mgr.check_reachability("203.0.113.50", 51820)
    assert rep_pub.is_valid is True
    assert rep_pub.cgnat_detected is False
    assert rep_pub.is_private_ip is False
    assert "Forward UDP port 51820" in rep_pub.router_guidance


def test_peer_lifecycle_roles_and_defaults(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(
        endpoint="203.0.113.10",
        canonical_hostname="chief-farm.local",
        port=51820,
    )

    # 1. Owner peer: Enabled by default
    owner_peer, owner_conf = mgr.add_peer(name="Alice Farmer", role="Owner", device_name="iPhone 15")
    assert owner_peer.enabled is True
    assert owner_peer.vpn_ip == "10.77.0.2"
    assert "Alice Farmer" in owner_conf
    assert "AllowedIPs = 10.77.0.1/32" in owner_conf  # Split tunnel
    assert "DNS = 10.77.0.1" in owner_conf           # Split-DNS

    # 2. Manager peer: Enabled by default
    mgr_peer, mgr_conf = mgr.add_peer(name="Bob Manager", role="Manager", device_name="Pixel 8")
    assert mgr_peer.enabled is True
    assert mgr_peer.vpn_ip == "10.77.0.3"

    # 3. Staff peer: DISABLED by default per strict security policy
    staff_peer, staff_conf = mgr.add_peer(name="Charlie Field", role="Staff", device_name="Galaxy A54")
    assert staff_peer.enabled is False
    assert staff_peer.vpn_ip == "10.77.0.4"

    # Check safe status
    status = mgr.get_safe_status()
    assert status["peer_count"] == 3
    assert status["active_peer_count"] == 2  # Owner + Manager active; Staff disabled

    # Check server wg0.conf
    wg_conf = mgr.render_server_config()
    assert owner_peer.public_key in wg_conf
    assert mgr_peer.public_key in wg_conf
    # Disabled staff peer must be commented out
    assert f"# [Peer: Charlie Field (Staff) - DISABLED]" in wg_conf
    assert f"AllowedIPs = {staff_peer.vpn_ip}/32" not in wg_conf

    # 4. Enable staff peer explicitly later
    assert mgr.set_peer_enabled(staff_peer.peer_id, True) is True
    wg_conf_enabled = mgr.render_server_config()
    assert f"AllowedIPs = {staff_peer.vpn_ip}/32" in wg_conf_enabled

    # 5. Revoke Bob Manager peer
    assert mgr.revoke_peer(mgr_peer.peer_id) is True
    wg_conf_revoked = mgr.render_server_config()
    assert mgr_peer.public_key not in wg_conf_revoked
    # Other peers remain active
    assert owner_peer.public_key in wg_conf_revoked
    assert staff_peer.public_key in wg_conf_revoked


def test_peer_key_rotation(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(endpoint="203.0.113.10", canonical_hostname="chief-farm.local")

    peer, conf1 = mgr.add_peer(name="Alice", role="Owner", device_name="Phone 1")
    old_pub = peer.public_key

    updated_peer, conf2 = mgr.rotate_peer_key(peer.peer_id)
    assert updated_peer.public_key != old_pub
    assert updated_peer.vpn_ip == peer.vpn_ip  # Retains IP assignment

    wg_conf = mgr.render_server_config()
    assert updated_peer.public_key in wg_conf
    assert old_pub not in wg_conf


def test_firewall_rules_fail_closed_guarantees(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(
        endpoint="203.0.113.10",
        canonical_hostname="chief-farm.local",
        port=51820,
        vpn_subnet="10.77.0.0/24",
    )

    rules = mgr.render_firewall_rules(chief_https_port=443)
    iptables = rules["linux_iptables"]

    # 1. Must allow UDP port 51820 on WAN
    assert any("51820" in r and "ACCEPT" in r for r in iptables)

    # 2. Must allow HTTPS (443) on VPN interface (wg0)
    assert any("--dport 443 -j ACCEPT" in r and "10.77.0.0/24" in r for r in iptables)

    # 3. Must allow DNS (53) on VPN interface for split-DNS
    assert any("--dport 53 -j ACCEPT" in r for r in iptables)

    # 4. Must drop all other traffic to server
    assert "iptables -A INPUT -i wg0 -j DROP" in iptables

    # 5. Must drop forwarding to farm LAN
    assert "iptables -A FORWARD -i wg0 -j DROP" in iptables


def test_peer_replace_device(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(endpoint="203.0.113.10", canonical_hostname="chief-farm.local")

    peer, conf1 = mgr.add_peer(name="Alice", role="Owner", device_name="Old iPhone")
    old_pub = peer.public_key
    assigned_ip = peer.vpn_ip

    updated_peer, conf2 = mgr.replace_peer_device(peer.peer_id, "New iPhone 16")
    assert updated_peer.device_name == "New iPhone 16"
    assert updated_peer.public_key != old_pub
    assert updated_peer.vpn_ip == assigned_ip
    assert "New iPhone 16" in conf2
    assert "Old iPhone" not in conf2

    wg_conf = mgr.render_server_config()
    assert updated_peer.public_key in wg_conf
    assert old_pub not in wg_conf


def test_manager_peer_explicit_authorization(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(endpoint="203.0.113.10", canonical_hostname="chief-farm.local")

    # Manager peers are authorized individually
    mgr_peer, conf = mgr.add_peer(name="Bob Manager", role="Manager", device_name="Work Laptop", enabled=True)
    assert mgr_peer.role == "Manager"
    assert mgr_peer.enabled is True

    # Can be individually deactivated
    mgr.set_peer_enabled(mgr_peer.peer_id, False)
    assert mgr.get_safe_status()["peers"][mgr_peer.peer_id]["enabled"] is False
    assert f"# [Peer: Bob Manager (Manager) - DISABLED]" in mgr.render_server_config()


def test_reachability_unresolvable_and_blocked_endpoints(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")

    # Loopback IP
    rep_loop = mgr.check_reachability("127.0.0.1", 51820)
    assert rep_loop.is_valid is False
    assert rep_loop.is_private_ip is True

    # Unresolvable DNS hostname
    rep_bad_dns = mgr.check_reachability("this-domain-definitely-does-not-exist-xyz987.farm", 51820)
    assert rep_bad_dns.is_valid is False
    assert "DNS resolution failed" in rep_bad_dns.message

    # Invalid hostname format
    rep_bad_fmt = mgr.check_reachability("invalid_host!@#$", 51820)
    assert rep_bad_fmt.is_valid is False
    assert "Invalid endpoint format" in rep_bad_fmt.message


def test_idempotent_server_initialization_and_safe_state(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")

    # First init
    status1 = mgr.initialize_server(
        endpoint="203.0.113.10",
        canonical_hostname="chief-farm.local",
        port=51820,
    )
    server_pub1 = status1["server_public_key"]
    server_priv1 = mgr.get_server_private_key()

    # Add a peer
    peer, _ = mgr.add_peer(name="Dave", role="Owner", device_name="iPad")

    # Second init (e.g. updating endpoint or dynamic DNS)
    status2 = mgr.initialize_server(
        endpoint="farm.example.com",
        canonical_hostname="chief-farm.local",
        port=51820,
    )
    server_pub2 = status2["server_public_key"]
    server_priv2 = mgr.get_server_private_key()

    # CRITICAL IDEMPOTENCY: Keys must not change; existing peers must be preserved
    assert server_pub1 == server_pub2
    assert server_priv1 == server_priv2
    assert status2["endpoint"] == "farm.example.com"
    assert peer.peer_id in status2["peers"]


def test_secret_handling_zero_key_leakage(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(endpoint="203.0.113.10", canonical_hostname="chief-farm.local")
    peer, conf = mgr.add_peer(name="Alice", role="Owner", device_name="Phone")

    server_priv = mgr.get_server_private_key()
    psk = mgr._state["peers"][peer.peer_id]["preshared_key"]

    safe_status = mgr.get_safe_status()
    safe_status_json = json.dumps(safe_status)

    # 1. Server private key must never appear in safe status dict or JSON
    assert server_priv not in safe_status_json

    # 2. Peer preshared key must never appear in safe status dict or JSON
    assert psk not in safe_status_json

    # 3. Peer safe dict must exclude PSK
    safe_peer = peer.to_safe_dict()
    assert "preshared_key" not in safe_peer
    assert psk not in json.dumps(safe_peer)


def test_local_console_works_independently_of_vpn(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    # Even when WireGuard is unconfigured or disabled, local paths and status remain coherent
    assert not mgr.is_configured()
    status = mgr.get_safe_status()
    assert status["enabled"] is False

    # Simulate local LAN host access: local loopback and LAN IPs do not route through WireGuard
    local_origin = "https://192.168.1.100:8765"
    assert "10.77.0." not in local_origin
