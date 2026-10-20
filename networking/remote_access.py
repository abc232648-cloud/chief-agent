"""WireGuard remote-access configuration, peer lifecycle, and security controls for Chief.

Design:
1. Encrypted L3 network path using WireGuard (self-hosted, no third-party subscription).
2. Native Python cryptography (Curve25519 / X25519) for deterministic key generation.
3. Dedicated non-conflicting VPN subnet (default: 10.77.0.0/24).
4. Restricts VPN traffic strictly to Chief HTTPS and narrowly required DNS.
   Explicitly blocks general farm LAN, SSH, databases, and administrative ports.
   Applies atomic, dedicated iptables chains (CHIEF-WG-IN, CHIEF-WG-FWD) without clobbering host rules.
5. Peer lifecycle:
   - Owner peers: Enabled during initial setup.
   - Manager peers: Individually authorized by Owner/Administrator.
   - Staff peers: Disabled by default; must be explicitly enabled per named device.
   - Revocation: Instantly removes peer from active kernel interface without dropping other tunnels.
6. Key privacy: Private keys are never logged, committed, or included in diagnostic reports.
7. Split-DNS / Single Origin: Retains the same canonical HTTPS hostname across farm Wi-Fi and WireGuard.
   Operates lightweight authoritative DNS daemon on 10.77.0.1:53 answering only for canonical hostname.
8. Reachability & CGNAT: Distinguishes syntax, DNS resolution, CGNAT (RFC 6598), private WAN,
   and unverified public UDP reachability. Never marks UDP reachable based on IP syntax alone.
9. Platform support: WireGuard server hosting is supported on Linux (Ubuntu systemd).
   Windows server hosting fails closed with UnsupportedPlatformError while preserving local access.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519

from .wireguard_dns import WireGuardDNSServer

logger = logging.getLogger(__name__)

# RFC 6598 Carrier-Grade NAT range: 100.64.0.0/10
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
]

_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$")


class UnsupportedPlatformError(Exception):
    """Raised when server-side WireGuard hosting is requested on an unsupported platform."""
    pass


def generate_wireguard_keypair() -> Tuple[str, str]:
    """Generate a WireGuard-compatible Curve25519 keypair.

    Returns:
        (private_key_b64, public_key_b64)
    """
    private_key = x25519.X25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    return (
        base64.b64encode(priv_raw).decode("ascii"),
        base64.b64encode(pub_raw).decode("ascii"),
    )


def generate_preshared_key() -> str:
    """Generate a random 32-byte WireGuard preshared key."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


@dataclass
class WireGuardPeer:
    peer_id: str
    name: str
    role: str  # Owner, Manager, Staff
    device_name: str
    public_key: str
    vpn_ip: str  # e.g. 10.77.0.2
    enabled: bool = True
    preshared_key: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_handshake: Optional[str] = None

    def to_safe_dict(self) -> Dict[str, Any]:
        """Return dict representation safe for logging or unprivileged inspection (no keys)."""
        d = asdict(self)
        d.pop("preshared_key", None)
        return d


@dataclass
class ReachabilityReport:
    endpoint: str
    port: int
    syntax_valid: bool
    dns_resolved: bool
    resolved_ip: Optional[str]
    cgnat_detected: bool
    is_private_ip: bool
    is_public: bool
    udp_verified: bool
    reachability_status: str  # SYNTAX_INVALID, DNS_FAILED, CGNAT_BLOCKED, PRIVATE_IP_BLOCKED, PUBLIC_UNVERIFIED, VERIFIED_ACTIVE
    message: str
    router_guidance: str

    @property
    def is_valid(self) -> bool:
        """Backward-compatibility property: True if endpoint is a valid public IP/hostname not in CGNAT/private range."""
        return self.syntax_valid and self.dns_resolved and not self.cgnat_detected and not self.is_private_ip

    @property
    def udp_reachable(self) -> Optional[bool]:
        """Backward-compatibility property: None if unverified, True if verified."""
        return True if self.udp_verified else None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_valid"] = self.is_valid
        d["udp_reachable"] = self.udp_reachable
        return d


class WireGuardFirewallManager:
    """Manages atomic, dedicated iptables firewall chains for Chief WireGuard."""

    CHAIN_INPUT = "CHIEF-WG-IN"
    CHAIN_FORWARD = "CHIEF-WG-FWD"

    @classmethod
    def render_rules(
        cls,
        vpn_subnet: str = "10.77.0.0/24",
        server_vpn_ip: str = "10.77.0.1",
        port: int = 51820,
        chief_https_port: int = 443,
    ) -> List[List[str]]:
        """Render ordered list of command arguments for iptables configuration."""
        cmds: List[List[str]] = [
            # 1. Create chains if missing
            ["iptables", "-N", cls.CHAIN_INPUT],
            ["iptables", "-N", cls.CHAIN_FORWARD],
            # 2. Idempotent jumps
            ["iptables", "-C", "INPUT", "-j", cls.CHAIN_INPUT],
            ["iptables", "-I", "INPUT", "1", "-j", cls.CHAIN_INPUT],
            ["iptables", "-C", "FORWARD", "-j", cls.CHAIN_FORWARD],
            ["iptables", "-I", "FORWARD", "1", "-j", cls.CHAIN_FORWARD],
            # 3. Flush chains atomically
            ["iptables", "-F", cls.CHAIN_INPUT],
            ["iptables", "-F", cls.CHAIN_FORWARD],
            # 4. CHIEF-WG-IN: Allow incoming UDP port on WAN
            ["iptables", "-A", cls.CHAIN_INPUT, "-p", "udp", "--dport", str(port), "-j", "ACCEPT"],
            # 5. CHIEF-WG-IN: Allow established traffic
            ["iptables", "-A", cls.CHAIN_INPUT, "-i", "wg0", "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
            # 6. CHIEF-WG-IN: Allow Chief HTTPS on wg0
            ["iptables", "-A", cls.CHAIN_INPUT, "-i", "wg0", "-s", vpn_subnet, "-d", server_vpn_ip, "-p", "tcp", "--dport", str(chief_https_port), "-j", "ACCEPT"],
            # 7. CHIEF-WG-IN: Allow Split-DNS on wg0
            ["iptables", "-A", cls.CHAIN_INPUT, "-i", "wg0", "-s", vpn_subnet, "-d", server_vpn_ip, "-p", "udp", "--dport", "53", "-j", "ACCEPT"],
            # 8. CHIEF-WG-IN: DROP all other traffic arriving on wg0 (SSH, DB, admin)
            ["iptables", "-A", cls.CHAIN_INPUT, "-i", "wg0", "-j", "DROP"],
            # 9. CHIEF-WG-FWD: DROP all forwarding from wg0 (no general farm LAN or internet access)
            ["iptables", "-A", cls.CHAIN_FORWARD, "-i", "wg0", "-j", "DROP"],
        ]
        return cmds

    @classmethod
    def apply(
        cls,
        vpn_subnet: str = "10.77.0.0/24",
        server_vpn_ip: str = "10.77.0.1",
        port: int = 51820,
        chief_https_port: int = 443,
        runner: Optional[Callable[[List[str]], int]] = None,
    ) -> bool:
        """Apply firewall rules safely without overwriting other host policies."""
        if runner is None:
            def default_runner(cmd: List[str]) -> int:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return res.returncode
            runner = default_runner

        # Check/create chains and jumps
        for check_cmd, add_cmd in [
            (["iptables", "-C", "INPUT", "-j", cls.CHAIN_INPUT], ["iptables", "-I", "INPUT", "1", "-j", cls.CHAIN_INPUT]),
            (["iptables", "-C", "FORWARD", "-j", cls.CHAIN_FORWARD], ["iptables", "-I", "FORWARD", "1", "-j", cls.CHAIN_FORWARD]),
        ]:
            runner(["iptables", "-N", check_cmd[4]])
            if runner(check_cmd) != 0:
                runner(add_cmd)

        # Flush chains
        runner(["iptables", "-F", cls.CHAIN_INPUT])
        runner(["iptables", "-F", cls.CHAIN_FORWARD])

        # Populate rules
        rules = [
            ["iptables", "-A", cls.CHAIN_INPUT, "-p", "udp", "--dport", str(port), "-j", "ACCEPT"],
            ["iptables", "-A", cls.CHAIN_INPUT, "-i", "wg0", "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
            ["iptables", "-A", cls.CHAIN_INPUT, "-i", "wg0", "-s", vpn_subnet, "-d", server_vpn_ip, "-p", "tcp", "--dport", str(chief_https_port), "-j", "ACCEPT"],
            ["iptables", "-A", cls.CHAIN_INPUT, "-i", "wg0", "-s", vpn_subnet, "-d", server_vpn_ip, "-p", "udp", "--dport", "53", "-j", "ACCEPT"],
            ["iptables", "-A", cls.CHAIN_INPUT, "-i", "wg0", "-j", "DROP"],
            ["iptables", "-A", cls.CHAIN_FORWARD, "-i", "wg0", "-j", "DROP"],
        ]
        success = True
        for rule in rules:
            if runner(rule) != 0:
                success = False

        return success

    @classmethod
    def rollback(cls, runner: Optional[Callable[[List[str]], int]] = None) -> None:
        """Safely remove dedicated chains on failure or uninstall."""
        if runner is None:
            def default_runner(cmd: List[str]) -> int:
                return subprocess.run(cmd, capture_output=True, text=True, check=False).returncode
            runner = default_runner

        runner(["iptables", "-D", "INPUT", "-j", cls.CHAIN_INPUT])
        runner(["iptables", "-D", "FORWARD", "-j", cls.CHAIN_FORWARD])
        runner(["iptables", "-F", cls.CHAIN_INPUT])
        runner(["iptables", "-F", cls.CHAIN_FORWARD])
        runner(["iptables", "-X", cls.CHAIN_INPUT])
        runner(["iptables", "-X", cls.CHAIN_FORWARD])


class WireGuardManager:
    """Manages WireGuard server configuration, peer lifecycle, firewall rules, and split-DNS."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.config_path = self.state_dir / "wireguard_config.json"
        self.server_key_path = self.state_dir / "server.key"
        self.wg_conf_path = self.state_dir / "wg0.conf"
        self._state: Dict[str, Any] = self._load_state()
        self._dns_server: Optional[WireGuardDNSServer] = None

    def _load_state(self) -> Dict[str, Any]:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "enabled": False,
            "endpoint": "",
            "port": 51820,
            "vpn_subnet": "10.77.0.0/24",
            "server_vpn_ip": "10.77.0.1",
            "server_public_key": "",
            "canonical_hostname": "",
            "split_dns_enabled": True,
            "firewall_applied": False,
            "service_active": False,
            "peers": {},
            "next_ip_index": 2,  # .1 is server, peers start at .2
        }

    def _save_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except Exception:
            pass
        tmp.replace(self.config_path)

    def is_configured(self) -> bool:
        return bool(self._state.get("enabled") and self._state.get("server_public_key"))

    def get_server_private_key(self) -> Optional[str]:
        if self.server_key_path.exists():
            try:
                return self.server_key_path.read_text(encoding="utf-8").strip()
            except Exception:
                return None
        return None

    def detect_platform(self) -> str:
        """Detect operating system platform."""
        s = platform.system().lower()
        if "linux" in s:
            return "linux"
        elif "windows" in s:
            return "windows"
        return s

    def initialize_server(
        self,
        endpoint: str,
        canonical_hostname: str,
        port: int = 51820,
        vpn_subnet: str = "10.77.0.0/24",
        farm_lan_cidr: Optional[str] = None,
        allow_unsupported_platform: bool = False,
    ) -> Dict[str, Any]:
        """Initialize or update the WireGuard server configuration."""
        plat = self.detect_platform()
        if plat == "windows" and not allow_unsupported_platform:
            raise UnsupportedPlatformError(
                "WireGuard server hosting is supported only on Linux (Ubuntu systemd). "
                "Windows server hosting is not supported. Local on-farm console remains available at http://127.0.0.1:8765."
            )

        endpoint = endpoint.strip()
        if not endpoint:
            raise ValueError("A public IP or dynamic DNS hostname is required for WireGuard endpoint.")

        # Subnet collision check
        vpn_net = ipaddress.ip_network(vpn_subnet, strict=False)
        if farm_lan_cidr:
            lan_net = ipaddress.ip_network(farm_lan_cidr, strict=False)
            if vpn_net.overlaps(lan_net):
                raise ValueError(
                    f"WireGuard VPN subnet {vpn_subnet} conflicts with farm LAN subnet {farm_lan_cidr}. "
                    "Choose an alternate non-overlapping range."
                )

        # Generate or load server keys
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        priv_key = self.get_server_private_key()
        if not priv_key:
            priv_key, pub_key = generate_wireguard_keypair()
            self.server_key_path.write_text(priv_key, encoding="utf-8")
            try:
                self.server_key_path.chmod(0o600)
            except Exception:
                pass
        else:
            # Recompute public key from private key
            raw_priv = base64.b64decode(priv_key)
            priv_obj = x25519.X25519PrivateKey.from_private_bytes(raw_priv)
            raw_pub = priv_obj.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            pub_key = base64.b64encode(raw_pub).decode("ascii")

        server_vpn_ip = str(list(vpn_net.hosts())[0])

        self._state["enabled"] = True
        self._state["endpoint"] = endpoint
        self._state["port"] = port
        self._state["vpn_subnet"] = str(vpn_net)
        self._state["server_vpn_ip"] = server_vpn_ip
        self._state["server_public_key"] = pub_key
        self._state["canonical_hostname"] = canonical_hostname.strip().lower()
        self._save_state()

        self.write_wg_conf()
        return self.get_safe_status()

    def check_reachability(self, endpoint: str, port: int = 51820) -> ReachabilityReport:
        """Inspect endpoint syntax, DNS resolution, CGNAT, private range, and honest UDP state."""
        endpoint = endpoint.strip()
        port = int(port)
        is_ip = False
        parsed_ip: Optional[ipaddress.IPv4Address | ipaddress.IPv6Address] = None
        dns_resolved = False
        resolved_ip_str: Optional[str] = None

        try:
            parsed_ip = ipaddress.ip_address(endpoint)
            is_ip = True
            dns_resolved = True
            resolved_ip_str = str(parsed_ip)
        except ValueError:
            pass

        if not is_ip:
            if not _HOSTNAME_RE.fullmatch(endpoint):
                return ReachabilityReport(
                    endpoint=endpoint,
                    port=port,
                    syntax_valid=False,
                    dns_resolved=False,
                    resolved_ip=None,
                    cgnat_detected=False,
                    is_private_ip=False,
                    is_public=False,
                    udp_verified=False,
                    reachability_status="SYNTAX_INVALID",
                    message="Invalid endpoint format. Must be a valid IPv4/IPv6 address or DNS hostname.",
                    router_guidance="",
                )
            try:
                resolved = socket.gethostbyname(endpoint)
                parsed_ip = ipaddress.ip_address(resolved)
                dns_resolved = True
                resolved_ip_str = resolved
            except Exception as exc:
                return ReachabilityReport(
                    endpoint=endpoint,
                    port=port,
                    syntax_valid=True,
                    dns_resolved=False,
                    resolved_ip=None,
                    cgnat_detected=False,
                    is_private_ip=False,
                    is_public=False,
                    udp_verified=False,
                    reachability_status="DNS_FAILED",
                    message=f"DNS resolution failed for {endpoint}: {exc}",
                    router_guidance="Verify your dynamic DNS setup or domain provider A record.",
                )

        # Check CGNAT (100.64.0.0/10)
        cgnat = parsed_ip in _CGNAT_NET
        if cgnat:
            return ReachabilityReport(
                endpoint=endpoint,
                port=port,
                syntax_valid=True,
                dns_resolved=dns_resolved,
                resolved_ip=resolved_ip_str,
                cgnat_detected=True,
                is_private_ip=False,
                is_public=False,
                udp_verified=False,
                reachability_status="CGNAT_BLOCKED",
                message=(
                    f"Endpoint {endpoint} is within Carrier-Grade NAT (CGNAT) range (100.64.0.0/10). "
                    "Direct inbound WireGuard connections cannot reach this farm connection without a public IP from your ISP."
                ),
                router_guidance="Contact your Internet Service Provider to request a routable public IPv4 address.",
            )

        # Check RFC 1918 Private IP
        is_priv = any(parsed_ip in net for net in _PRIVATE_NETS)
        if is_priv:
            return ReachabilityReport(
                endpoint=endpoint,
                port=port,
                syntax_valid=True,
                dns_resolved=dns_resolved,
                resolved_ip=resolved_ip_str,
                cgnat_detected=False,
                is_private_ip=True,
                is_public=False,
                udp_verified=False,
                reachability_status="PRIVATE_IP_BLOCKED",
                message=(
                    f"Endpoint {endpoint} is a private local address. "
                    "Remote clients away from the farm cannot connect over the public internet."
                ),
                router_guidance="Use your external public WAN IP or a configured Dynamic DNS hostname.",
            )

        # Public IP: Inbound UDP reachability is unverified until an actual client handshake occurs
        guidance = (
            f"Forward UDP port {port} on your farm router to the Chief server's local LAN IP.\n"
            f"Rule: WAN UDP port {port} -> Chief Server local IP port {port}."
        )
        return ReachabilityReport(
            endpoint=endpoint,
            port=port,
            syntax_valid=True,
            dns_resolved=dns_resolved,
            resolved_ip=resolved_ip_str,
            cgnat_detected=False,
            is_private_ip=False,
            is_public=True,
            udp_verified=False,  # Unverified until client handshake
            reachability_status="PUBLIC_UNVERIFIED",
            message="Endpoint is a routable public address. Inbound UDP reachability requires router port-forwarding and remains unverified until an initial client handshake is received.",
            router_guidance=guidance,
        )

    def add_peer(
        self,
        name: str,
        role: str,
        device_name: str,
        enabled: Optional[bool] = None,
        runner: Optional[Callable[[List[str]], int]] = None,
    ) -> Tuple[WireGuardPeer, str]:
        """Add a new named peer and return the peer record and client configuration."""
        role = role.strip().title()
        if role not in {"Owner", "Administrator", "Manager", "Staff"}:
            raise ValueError(f"Invalid peer role {role}. Must be Owner, Manager, or Staff.")

        if enabled is None:
            # Staff peers start DISABLED initially per policy
            enabled = False if role == "Staff" else True

        vpn_net = ipaddress.ip_network(self._state["vpn_subnet"], strict=False)
        hosts = list(vpn_net.hosts())
        next_idx = self._state.get("next_ip_index", 2)
        if next_idx >= len(hosts):
            raise ValueError("VPN subnet address pool exhausted.")

        assigned_ip = str(hosts[next_idx - 1])
        peer_id = f"peer-{next_idx:03d}-{re.sub(r'[^a-z0-9]', '', name.lower())[:12]}"

        client_priv, client_pub = generate_wireguard_keypair()
        psk = generate_preshared_key()

        peer = WireGuardPeer(
            peer_id=peer_id,
            name=name.strip(),
            role=role,
            device_name=device_name.strip(),
            public_key=client_pub,
            vpn_ip=assigned_ip,
            enabled=enabled,
            preshared_key=psk,
        )

        self._state["peers"][peer_id] = asdict(peer)
        self._state["next_ip_index"] = next_idx + 1
        self._save_state()
        self.write_wg_conf()

        # Update running kernel interface immediately if active
        if enabled:
            self._sync_live_peer(peer, client_pub, psk, assigned_ip, runner=runner)

        client_conf = self.render_client_config(peer, client_priv)
        return peer, client_conf

    def _sync_live_peer(
        self,
        peer: WireGuardPeer,
        pubkey: str,
        psk: Optional[str],
        vpn_ip: str,
        remove: bool = False,
        runner: Optional[Callable[[List[str]], int]] = None,
    ) -> None:
        """Sync peer changes directly with live wg0 kernel interface if active."""
        if not self._state.get("service_active"):
            return
        if runner is None:
            def default_runner(cmd: List[str]) -> int:
                return subprocess.run(cmd, capture_output=True, text=True, check=False).returncode
            runner = default_runner

        if remove:
            runner(["wg", "set", "wg0", "peer", pubkey, "remove"])
        else:
            cmd = ["wg", "set", "wg0", "peer", pubkey, "allowed-ips", f"{vpn_ip}/32"]
            if psk:
                # Write ephemeral temp file for PSK to prevent command-line exposure
                psk_path = self.state_dir / "tmp_psk"
                psk_path.write_text(psk, encoding="utf-8")
                try:
                    psk_path.chmod(0o600)
                except Exception:
                    pass
                cmd.extend(["preshared-key", str(psk_path)])
                runner(cmd)
                try:
                    psk_path.unlink()
                except Exception:
                    pass
            else:
                runner(cmd)

    def set_peer_enabled(self, peer_id: str, enabled: bool, runner: Optional[Callable[[List[str]], int]] = None) -> bool:
        """Enable or disable an existing peer."""
        if peer_id not in self._state["peers"]:
            return False
        peer_data = self._state["peers"][peer_id]
        peer_data["enabled"] = bool(enabled)
        self._save_state()
        self.write_wg_conf()

        peer = WireGuardPeer(**peer_data)
        if enabled:
            self._sync_live_peer(peer, peer.public_key, peer.preshared_key, peer.vpn_ip, remove=False, runner=runner)
        else:
            self._sync_live_peer(peer, peer.public_key, None, peer.vpn_ip, remove=True, runner=runner)
        return True

    def revoke_peer(self, peer_id: str, runner: Optional[Callable[[List[str]], int]] = None) -> bool:
        """Permanently revoke and delete a peer."""
        if peer_id not in self._state["peers"]:
            return False
        peer_data = self._state["peers"][peer_id]
        pubkey = peer_data.get("public_key")

        del self._state["peers"][peer_id]
        self._save_state()
        self.write_wg_conf()

        if pubkey:
            peer = WireGuardPeer(**peer_data)
            self._sync_live_peer(peer, pubkey, None, "", remove=True, runner=runner)
        return True

    def rotate_peer_key(self, peer_id: str, runner: Optional[Callable[[List[str]], int]] = None) -> Tuple[WireGuardPeer, str]:
        """Rotate keys for an existing peer (e.g. replaced device)."""
        if peer_id not in self._state["peers"]:
            raise KeyError(f"Peer {peer_id} not found")
        data = self._state["peers"][peer_id]
        old_pub = data.get("public_key")

        client_priv, client_pub = generate_wireguard_keypair()
        psk = generate_preshared_key()

        data["public_key"] = client_pub
        data["preshared_key"] = psk
        self._state["peers"][peer_id] = data
        self._save_state()
        self.write_wg_conf()

        peer = WireGuardPeer(**data)
        # Remove old key and register new key on live interface
        if old_pub:
            self._sync_live_peer(peer, old_pub, None, "", remove=True, runner=runner)
        if peer.enabled:
            self._sync_live_peer(peer, client_pub, psk, peer.vpn_ip, remove=False, runner=runner)

        client_conf = self.render_client_config(peer, client_priv)
        return peer, client_conf

    def replace_peer_device(self, peer_id: str, new_device_name: str, runner: Optional[Callable[[List[str]], int]] = None) -> Tuple[WireGuardPeer, str]:
        """Replace a peer's device: updates device name and rotates keypair/PSK, preserving assigned IP."""
        if peer_id not in self._state["peers"]:
            raise KeyError(f"Peer {peer_id} not found")
        new_device_name = new_device_name.strip()
        if not new_device_name:
            raise ValueError("New device name cannot be empty.")
        data = self._state["peers"][peer_id]
        data["device_name"] = new_device_name
        self._state["peers"][peer_id] = data
        return self.rotate_peer_key(peer_id, runner=runner)

    def render_server_config(self) -> str:
        """Render standard wg0.conf server configuration."""
        priv_key = self.get_server_private_key() or "<MISSING_PRIVATE_KEY>"
        server_ip = self._state["server_vpn_ip"]
        port = self._state["port"]

        lines = [
            "# Chief Authoritative WireGuard Configuration",
            "# Automatically generated. Fail-closed access to Chief HTTPS.",
            "[Interface]",
            f"Address = {server_ip}/32",
            f"ListenPort = {port}",
            f"PrivateKey = {priv_key}",
            "",
        ]

        for p_id, p_data in sorted(self._state.get("peers", {}).items()):
            if not p_data.get("enabled", True):
                lines.append(f"# [Peer: {p_data.get('name')} ({p_data.get('role')}) - DISABLED]")
                continue
            lines.append(f"# Peer: {p_data.get('name')} ({p_data.get('role')}) - {p_data.get('device_name')}")
            lines.append("[Peer]")
            lines.append(f"PublicKey = {p_data['public_key']}")
            if p_data.get("preshared_key"):
                lines.append(f"PresharedKey = {p_data['preshared_key']}")
            lines.append(f"AllowedIPs = {p_data['vpn_ip']}/32")
            lines.append("")

        return "\n".join(lines)

    def write_wg_conf(self) -> Path:
        """Write wg0.conf with 0600 permissions."""
        content = self.render_server_config()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.wg_conf_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except Exception:
            pass
        tmp.replace(self.wg_conf_path)
        return self.wg_conf_path

    def render_client_config(self, peer: WireGuardPeer, client_private_key: str) -> str:
        """Generate client .conf file for mobile WireGuard app."""
        server_pub = self._state.get("server_public_key", "")
        endpoint = f"{self._state.get('endpoint')}:{self._state.get('port', 51820)}"
        server_vpn_ip = self._state.get("server_vpn_ip", "10.77.0.1")

        lines = [
            f"# Chief Remote Access Profile: {peer.name} ({peer.role})",
            f"# Device: {peer.device_name}",
            f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "[Interface]",
            f"PrivateKey = {client_private_key}",
            f"Address = {peer.vpn_ip}/32",
            # Route DNS through Chief server VPN IP for split-DNS resolution
            f"DNS = {server_vpn_ip}",
            "",
            "[Peer]",
            f"PublicKey = {server_pub}",
        ]
        if peer.preshared_key:
            lines.append(f"PresharedKey = {peer.preshared_key}")
        lines.append(f"Endpoint = {endpoint}")
        # Only route Chief's VPN IP through the tunnel (split-tunneling: preserves phone internet)
        lines.append(f"AllowedIPs = {server_vpn_ip}/32")
        lines.append("PersistentKeepalive = 25")
        lines.append("")

        return "\n".join(lines)

    def apply_firewall_policy(self, chief_https_port: int = 443, runner: Optional[Callable[[List[str]], int]] = None) -> bool:
        """Apply fail-closed iptables rules using dedicated chains."""
        ok = WireGuardFirewallManager.apply(
            vpn_subnet=self._state.get("vpn_subnet", "10.77.0.0/24"),
            server_vpn_ip=self._state.get("server_vpn_ip", "10.77.0.1"),
            port=self._state.get("port", 51820),
            chief_https_port=chief_https_port,
            runner=runner,
        )
        self._state["firewall_applied"] = ok
        self._save_state()
        return ok

    def rollback_firewall_policy(self, runner: Optional[Callable[[List[str]], int]] = None) -> None:
        """Rollback firewall rules on failure."""
        WireGuardFirewallManager.rollback(runner=runner)
        self._state["firewall_applied"] = False
        self._save_state()

    def start_interface_service(self, runner: Optional[Callable[[List[str]], int]] = None) -> bool:
        """Enable and start the wg-quick@wg0 systemd service."""
        if runner is None:
            def default_runner(cmd: List[str]) -> int:
                return subprocess.run(cmd, capture_output=True, text=True, check=False).returncode
            runner = default_runner

        # Link or copy config to /etc/wireguard/wg0.conf if running as root
        etc_wg = Path("/etc/wireguard")
        if etc_wg.exists():
            try:
                dest = etc_wg / "wg0.conf"
                dest.write_text(self.render_server_config(), encoding="utf-8")
                dest.chmod(0o600)
            except Exception:
                pass

        c1 = runner(["systemctl", "enable", "wg-quick@wg0"])
        c2 = runner(["systemctl", "start", "wg-quick@wg0"])
        active = (c2 == 0)
        self._state["service_active"] = active
        self._save_state()
        return active

    def stop_interface_service(self, runner: Optional[Callable[[List[str]], int]] = None) -> None:
        """Stop wg-quick@wg0 service."""
        if runner is None:
            def default_runner(cmd: List[str]) -> int:
                return subprocess.run(cmd, capture_output=True, text=True, check=False).returncode
            runner = default_runner

        runner(["systemctl", "stop", "wg-quick@wg0"])
        self._state["service_active"] = False
        self._save_state()

    def check_service_readiness(self, runner: Optional[Callable[[List[str]], Tuple[int, str]]] = None) -> bool:
        """Check if WireGuard interface wg0 is active and responding."""
        if runner is None:
            def default_runner(cmd: List[str]) -> Tuple[int, str]:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return res.returncode, res.stdout
            runner = default_runner

        code, out = runner(["wg", "show", "wg0"])
        return code == 0 and bool(out.strip())

    def check_peer_handshake(self, peer_public_key: str, max_age_seconds: int = 180, runner: Optional[Callable[[List[str]], Tuple[int, str]]] = None) -> bool:
        """Verify whether an actual client handshake has been recorded recently."""
        if runner is None:
            def default_runner(cmd: List[str]) -> Tuple[int, str]:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return res.returncode, res.stdout
            runner = default_runner

        code, out = runner(["wg", "show", "wg0", "latest-handshakes"])
        if code != 0 or not out:
            return False

        now = int(datetime.now(timezone.utc).timestamp())
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == peer_public_key:
                try:
                    ts = int(parts[1])
                    if ts > 0 and (now - ts) <= max_age_seconds:
                        return True
                except ValueError:
                    pass
        return False

    def start_dns_server(
        self,
        port: Union[int, str] = 53,
        canonical_hostname: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """Start the split-DNS server on 10.77.0.1:port.

        Accepts port and canonical_hostname flexibly by keyword or position.
        Ensures the configured canonical hostname reaches the DNS server without breaking existing callers.
        """
        actual_port = 53
        actual_hostname = canonical_hostname

        if isinstance(port, int):
            actual_port = port
        elif isinstance(port, str):
            if port.isdigit():
                actual_port = int(port)
            else:
                actual_hostname = port

        if isinstance(canonical_hostname, int):
            actual_port = canonical_hostname
            if isinstance(port, str):
                actual_hostname = port

        if "port" in kwargs and kwargs["port"] is not None:
            actual_port = int(kwargs["port"])
        if "canonical_hostname" in kwargs and kwargs["canonical_hostname"] is not None:
            actual_hostname = str(kwargs["canonical_hostname"])

        if actual_hostname:
            self._state["canonical_hostname"] = str(actual_hostname).strip().rstrip(".")

        if self._dns_server and getattr(self._dns_server, "is_running", False):
            return True

        server_ip = self._state.get("server_vpn_ip", "10.77.0.1")
        hostname = actual_hostname or self._state.get("canonical_hostname", "chief-farm.local")
        try:
            self._dns_server = WireGuardDNSServer(
                bind_ip=server_ip,
                port=actual_port,
                canonical_hostname=hostname,
                answer_ip=server_ip,
            )
            self._dns_server.start()
            return True
        except Exception as exc:
            logger.warning("Could not start WireGuard split-DNS daemon on %s:%d: %s", server_ip, actual_port, exc)
            return False

    def stop_dns_server(self) -> None:
        """Stop the split-DNS server."""
        if self._dns_server:
            try:
                self._dns_server.stop()
            except Exception:
                pass
            self._dns_server = None

    def check_dns_readiness(
        self,
        canonical_hostname: Optional[str] = None,
        server_ip: Optional[str] = None,
        port: int = 53,
        timeout: float = 2.0,
    ) -> bool:
        """Verify real DNS query resolution through UDP socket."""
        try:
            target_ip = server_ip or self._state.get("server_vpn_ip", "10.77.0.1")
            target_host = canonical_hostname or self._state.get("canonical_hostname", "chief-farm.local")
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            labels = target_host.strip().lower().split(".")
            qname_bytes = b"".join(bytes([len(l)]) + l.encode("ascii") for l in labels) + b"\x00"
            query = b"\xaa\xbb\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + qname_bytes + b"\x00\x01\x00\x01"
            sock.sendto(query, (target_ip, port))
            resp, _ = sock.recvfrom(2048)
            sock.close()
            if len(resp) >= 12 and socket.inet_aton(target_ip) in resp:
                return True
            return False
        except Exception:
            return False

    def rollback(self) -> None:
        """Safely clean up WireGuard server resources without affecting local Chief services."""
        if self._dns_server:
            try:
                self.stop_dns_server()
            except Exception:
                pass
        try:
            self.stop_interface_service()
        except Exception:
            pass
        try:
            WireGuardFirewallManager.rollback()
        except Exception:
            pass
        if hasattr(self, "wg_conf_path") and self.wg_conf_path and Path(self.wg_conf_path).exists():
            try:
                Path(self.wg_conf_path).unlink()
            except OSError:
                pass

    def render_firewall_rules(self, chief_https_port: int = 443) -> Dict[str, List[str]]:
        """Return firewall rules for inspection or documentation."""
        raw_cmds = WireGuardFirewallManager.render_rules(
            vpn_subnet=self._state.get("vpn_subnet", "10.77.0.0/24"),
            server_vpn_ip=self._state.get("server_vpn_ip", "10.77.0.1"),
            port=self._state.get("port", 51820),
            chief_https_port=chief_https_port,
        )
        iptables_lines = [" ".join(cmd) for cmd in raw_cmds]
        windows_rules = [
            f'netsh advfirewall firewall add rule name="Chief-WireGuard-UDP" dir=in action=allow protocol=UDP localport={self._state.get("port", 51820)}',
            f'netsh advfirewall firewall add rule name="Chief-VPN-HTTPS" dir=in action=allow protocol=TCP localport={chief_https_port} remoteip={self._state.get("vpn_subnet", "10.77.0.0/24")}',
        ]
        return {
            "linux_iptables": iptables_lines,
            "windows_netsh": windows_rules,
        }

    def get_safe_status(self) -> Dict[str, Any]:
        """Return comprehensive status excluding private keys."""
        peers = {}
        for p_id, p_data in self._state.get("peers", {}).items():
            peer_copy = dict(p_data)
            peer_copy.pop("preshared_key", None)
            peers[p_id] = peer_copy

        return {
            "enabled": self._state.get("enabled", False),
            "endpoint": self._state.get("endpoint", ""),
            "port": self._state.get("port", 51820),
            "vpn_subnet": self._state.get("vpn_subnet", "10.77.0.0/24"),
            "server_vpn_ip": self._state.get("server_vpn_ip", "10.77.0.1"),
            "server_public_key": self._state.get("server_public_key", ""),
            "canonical_hostname": self._state.get("canonical_hostname", ""),
            "split_dns_enabled": self._state.get("split_dns_enabled", True),
            "dns_service_running": bool(self._dns_server and self._dns_server.is_running),
            "firewall_applied": self._state.get("firewall_applied", False),
            "service_active": self._state.get("service_active", False),
            "peer_count": len(peers),
            "active_peer_count": sum(1 for p in peers.values() if p.get("enabled")),
            "peers": peers,
        }
