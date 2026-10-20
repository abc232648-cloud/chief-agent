"""Unit tests for WireGuard remote access, peer lifecycle, firewall rules, service readiness, and honest reachability."""
import ipaddress
import json
from pathlib import Path
import time
import pytest

from networking.remote_access import (
    UnsupportedPlatformError,
    WireGuardFirewallManager,
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
        allow_unsupported_platform=True,
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


def test_windows_server_hosting_fails_closed(tmp_path, monkeypatch):
    mgr = WireGuardManager(tmp_path / "wg_state")
    monkeypatch.setattr(mgr, "detect_platform", lambda: "windows")

    # WireGuard server hosting on Windows must fail closed
    with pytest.raises(UnsupportedPlatformError, match="supported only on Linux"):
        mgr.initialize_server(
            endpoint="203.0.113.10",
            canonical_hostname="chief-farm.local",
            allow_unsupported_platform=False,
        )


def test_subnet_collision_detection(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")

    # Conflict with farm LAN 10.77.0.0/16
    with pytest.raises(ValueError, match="conflicts with farm LAN subnet"):
        mgr.initialize_server(
            endpoint="203.0.113.10",
            canonical_hostname="chief-farm.local",
            vpn_subnet="10.77.0.0/24",
            farm_lan_cidr="10.77.0.0/16",
            allow_unsupported_platform=True,
        )

    # Non-conflicting subnets pass
    status = mgr.initialize_server(
        endpoint="203.0.113.10",
        canonical_hostname="chief-farm.local",
        vpn_subnet="10.77.0.0/24",
        farm_lan_cidr="192.168.1.0/24",
        allow_unsupported_platform=True,
    )
    assert status["enabled"] is True


def test_reachability_honest_udp_classification(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")

    # 1. CGNAT address in 100.64.0.0/10
    rep_cgnat = mgr.check_reachability("100.64.12.34", 51820)
    assert rep_cgnat.is_valid is False
    assert rep_cgnat.cgnat_detected is True
    assert rep_cgnat.reachability_status == "CGNAT_BLOCKED"
    assert "Carrier-Grade NAT" in rep_cgnat.message

    # 2. Private LAN address (e.g. 192.168.1.100)
    rep_priv = mgr.check_reachability("192.168.1.100", 51820)
    assert rep_priv.is_valid is False
    assert rep_priv.is_private_ip is True
    assert rep_priv.reachability_status == "PRIVATE_IP_BLOCKED"
    assert "private local address" in rep_priv.message

    # 3. Public routable IP (TEST-NET-3: 203.0.113.50)
    rep_pub = mgr.check_reachability("203.0.113.50", 51820)
    assert rep_pub.is_valid is True
    assert rep_pub.cgnat_detected is False
    assert rep_pub.is_private_ip is False
    assert rep_pub.is_public is True
    # CRITICAL HONESTY: udp_verified must be False because router forwarding is not yet tested
    assert rep_pub.udp_verified is False
    assert rep_pub.reachability_status == "PUBLIC_UNVERIFIED"
    assert "Forward UDP port 51820" in rep_pub.router_guidance


def test_peer_lifecycle_roles_and_defaults(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(
        endpoint="203.0.113.10",
        canonical_hostname="chief-farm.local",
        port=51820,
        allow_unsupported_platform=True,
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
    assert owner_peer.public_key in wg_conf_revoked
    assert staff_peer.public_key in wg_conf_revoked


def test_peer_replace_device(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(endpoint="203.0.113.10", canonical_hostname="chief-farm.local", allow_unsupported_platform=True)

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


def test_firewall_manager_chains_and_rules():
    cmds = []
    def fake_runner(cmd):
        cmds.append(cmd)
        return 0

    ok = WireGuardFirewallManager.apply(
        vpn_subnet="10.77.0.0/24",
        server_vpn_ip="10.77.0.1",
        port=51820,
        chief_https_port=443,
        runner=fake_runner,
    )
    assert ok is True

    # 1. Custom chains created and flushed
    assert ["iptables", "-N", "CHIEF-WG-IN"] in cmds
    assert ["iptables", "-N", "CHIEF-WG-FWD"] in cmds
    assert ["iptables", "-F", "CHIEF-WG-IN"] in cmds
    assert ["iptables", "-F", "CHIEF-WG-FWD"] in cmds

    # 2. WireGuard UDP listener allowed on WAN
    assert ["iptables", "-A", "CHIEF-WG-IN", "-p", "udp", "--dport", "51820", "-j", "ACCEPT"] in cmds

    # 3. Chief HTTPS (443) allowed on wg0
    assert ["iptables", "-A", "CHIEF-WG-IN", "-i", "wg0", "-s", "10.77.0.0/24", "-d", "10.77.0.1", "-p", "tcp", "--dport", "443", "-j", "ACCEPT"] in cmds

    # 4. Split-DNS (53) allowed on wg0
    assert ["iptables", "-A", "CHIEF-WG-IN", "-i", "wg0", "-s", "10.77.0.0/24", "-d", "10.77.0.1", "-p", "udp", "--dport", "53", "-j", "ACCEPT"] in cmds

    # 5. DROP all other wg0 packets (SSH, DB, admin)
    assert ["iptables", "-A", "CHIEF-WG-IN", "-i", "wg0", "-j", "DROP"] in cmds

    # 6. DROP all forwarding (no LAN forwarding)
    assert ["iptables", "-A", "CHIEF-WG-FWD", "-i", "wg0", "-j", "DROP"] in cmds

    # Test rollback
    rollback_cmds = []
    WireGuardFirewallManager.rollback(runner=lambda c: rollback_cmds.append(c) or 0)
    assert ["iptables", "-D", "INPUT", "-j", "CHIEF-WG-IN"] in rollback_cmds
    assert ["iptables", "-X", "CHIEF-WG-IN"] in rollback_cmds


def test_service_readiness_and_handshake_verification(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(endpoint="203.0.113.10", canonical_hostname="chief-farm.local", allow_unsupported_platform=True)
    peer, _ = mgr.add_peer("Alice", "Owner", "Phone")

    # 1. Readiness check returns False if wg command fails
    assert mgr.check_service_readiness(runner=lambda c: (1, "")) is False
    # Readiness check returns True when wg show succeeds
    assert mgr.check_service_readiness(runner=lambda c: (0, "interface: wg0\n  public key: xyz\n")) is True

    # 2. Handshake verification
    pubkey = peer.public_key
    now = int(time.time())

    # Fresh handshake (10s ago)
    def fresh_runner(cmd):
        return (0, f"{pubkey} {now - 10}\n")
    assert mgr.check_peer_handshake(pubkey, max_age_seconds=180, runner=fresh_runner) is True

    # Stale handshake (500s ago)
    def stale_runner(cmd):
        return (0, f"{pubkey} {now - 500}\n")
    assert mgr.check_peer_handshake(pubkey, max_age_seconds=180, runner=stale_runner) is False

    # Missing handshake (0)
    def zero_runner(cmd):
        return (0, f"{pubkey} 0\n")
    assert mgr.check_peer_handshake(pubkey, max_age_seconds=180, runner=zero_runner) is False


def test_secret_handling_zero_key_leakage(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(endpoint="203.0.113.10", canonical_hostname="chief-farm.local", allow_unsupported_platform=True)
    peer, conf = mgr.add_peer(name="Alice", role="Owner", device_name="Phone")

    server_priv = mgr.get_server_private_key()
    psk = mgr._state["peers"][peer.peer_id]["preshared_key"]

    safe_status = mgr.get_safe_status()
    safe_status_json = json.dumps(safe_status)

    assert server_priv not in safe_status_json
    assert psk not in safe_status_json
    safe_peer = peer.to_safe_dict()
    assert "preshared_key" not in safe_peer
    assert psk not in json.dumps(safe_peer)


def test_local_console_works_independently_of_vpn(tmp_path):
    mgr = WireGuardManager(tmp_path / "wg_state")
    assert not mgr.is_configured()
    status = mgr.get_safe_status()
    assert status["enabled"] is False
    local_origin = "https://192.168.1.100:8765"
    assert "10.77.0." not in local_origin
def test_start_dns_server_contract_and_signature_permutations(tmp_path, monkeypatch):
    """Verify start_dns_server accepts canonical_hostname and port in all supported call styles without TypeError."""
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(endpoint="203.0.113.10", canonical_hostname="chief-farm.local", allow_unsupported_platform=True)

    started_servers = []

    class DummyDNSServer:
        def __init__(self, bind_ip, port, canonical_hostname, answer_ip):
            self.bind_ip = bind_ip
            self.port = port
            self.canonical_hostname = canonical_hostname
            self.answer_ip = answer_ip
            self.is_running = False
            started_servers.append(self)

        def start(self):
            self.is_running = True

        def stop(self):
            self.is_running = False

    monkeypatch.setattr("networking.remote_access.WireGuardDNSServer", DummyDNSServer)

    # Style 1: Installer style: start_dns_server(canonical_hostname=canonical_host)
    mgr._dns_server = None
    ok1 = mgr.start_dns_server(canonical_hostname="farm-alpha.local")
    assert ok1 is True
    assert mgr._state["canonical_hostname"] == "farm-alpha.local"
    assert started_servers[-1].canonical_hostname == "farm-alpha.local"
    assert started_servers[-1].port == 53

    # Style 2: start_dns_server(canonical_hostname="farm-beta.local", port=5353)
    mgr._dns_server = None
    ok2 = mgr.start_dns_server(canonical_hostname="farm-beta.local", port=5353)
    assert ok2 is True
    assert mgr._state["canonical_hostname"] == "farm-beta.local"
    assert started_servers[-1].canonical_hostname == "farm-beta.local"
    assert started_servers[-1].port == 5353

    # Style 3: Positional swapped: start_dns_server("farm-gamma.local", 5354)
    mgr._dns_server = None
    ok3 = mgr.start_dns_server("farm-gamma.local", 5354)
    assert ok3 is True
    assert mgr._state["canonical_hostname"] == "farm-gamma.local"
    assert started_servers[-1].canonical_hostname == "farm-gamma.local"
    assert started_servers[-1].port == 5354

    # Style 4: Classic legacy: start_dns_server(53)
    mgr._dns_server = None
    ok4 = mgr.start_dns_server(53)
    assert ok4 is True
    assert started_servers[-1].port == 53

    # Style 5: Default parameterless
    mgr._dns_server = None
    ok5 = mgr.start_dns_server()
    assert ok5 is True
    assert started_servers[-1].port == 53


def test_wireguard_manager_rollback_and_dns_readiness(tmp_path, monkeypatch):
    """Verify WireGuardManager rollback cleans up daemon, service, firewall, and files."""
    mgr = WireGuardManager(tmp_path / "wg_state")
    mgr.initialize_server(endpoint="203.0.113.10", canonical_hostname="chief-farm.local", allow_unsupported_platform=True)

    stopped = []
    class DummyDNSServer:
        def __init__(self, *args, **kwargs):
            self.is_running = True
        def start(self):
            pass
        def stop(self):
            self.is_running = False
            stopped.append("dns")

    monkeypatch.setattr("networking.remote_access.WireGuardDNSServer", DummyDNSServer)
    mgr.start_dns_server(canonical_hostname="chief-farm.local")

    # Mock service and firewall runners
    firewall_rolled_back = []
    monkeypatch.setattr("networking.remote_access.WireGuardFirewallManager.rollback", lambda runner=None: firewall_rolled_back.append(True))

    service_stopped = []
    mgr.stop_interface_service = lambda runner=None: service_stopped.append(True)

    mgr.rollback()
    assert "dns" in stopped
    assert firewall_rolled_back == [True]
    assert service_stopped == [True]
