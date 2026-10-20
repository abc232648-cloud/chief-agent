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
