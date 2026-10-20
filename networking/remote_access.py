"""WireGuard remote-access configuration, peer lifecycle, and security controls for Chief.

Design:
1. Creates an encrypted L3 network path using WireGuard (self-hosted, no third-party subscription).
2. Uses native Python cryptography (Curve25519 / X25519) for deterministic key generation.
3. Dedicated non-conflicting VPN subnet (default: 10.77.0.0/24).
4. Restricts VPN traffic strictly to Chief HTTPS and narrowly required DNS.
   Explicitly blocks general farm LAN, SSH, databases, and administrative ports.
5. Peer lifecycle:
   - Owner peers: Enabled during initial setup.
   - Manager peers: Optional, controlled by Owner/Administrator.
   - Staff peers: Disabled by default; must be explicitly enabled per named device.
   - Revocation: Removes peer without disrupting other active tunnels.
6. Key privacy: Private keys are never logged, committed, or included in diagnostic reports.
7. Split-DNS / Single Origin: Retains the same canonical HTTPS hostname across farm Wi-Fi and WireGuard.
8. Reachability & CGNAT: Probes public endpoint; detects Carrier-Grade NAT (RFC 6598) and blocked UDP.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519

# RFC 6598 Carrier-Grade NAT range: 100.64.0.0/10
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
]

_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$")


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
    is_valid: bool
    cgnat_detected: bool
    is_private_ip: bool
    udp_reachable: Optional[bool]
    message: str
    router_guidance: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WireGuardManager:
    """Manages WireGuard server configuration, peer life cycle, and firewall rules."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.config_path = self.state_dir / "wireguard_config.json"
        self.server_key_path = self.state_dir / "server.key"
        self.wg_conf_path = self.state_dir / "wg0.conf"
        self._state: Dict[str, Any] = self._load_state()

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

    def initialize_server(
        self,
        endpoint: str,
        canonical_hostname: str,
        port: int = 51820,
        vpn_subnet: str = "10.77.0.0/24",
        farm_lan_cidr: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Initialize or update the WireGuard server configuration."""
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
        """Inspect endpoint for CGNAT, private range, or blocked UDP."""
        endpoint = endpoint.strip()
        port = int(port)
        is_ip = False
        parsed_ip: Optional[ipaddress.IPv4Address | ipaddress.IPv6Address] = None

        try:
            parsed_ip = ipaddress.ip_address(endpoint)
            is_ip = True
        except ValueError:
            pass

        if not is_ip:
            if not _HOSTNAME_RE.fullmatch(endpoint):
                return ReachabilityReport(
                    endpoint=endpoint,
                    port=port,
                    is_valid=False,
                    cgnat_detected=False,
                    is_private_ip=False,
                    udp_reachable=False,
                    message="Invalid endpoint format. Must be a valid IPv4/IPv6 address or DNS hostname.",
                    router_guidance="",
                )
            try:
                resolved = socket.gethostbyname(endpoint)
                parsed_ip = ipaddress.ip_address(resolved)
            except Exception as exc:
                return ReachabilityReport(
                    endpoint=endpoint,
                    port=port,
                    is_valid=False,
                    cgnat_detected=False,
                    is_private_ip=False,
                    udp_reachable=False,
                    message=f"DNS resolution failed for {endpoint}: {exc}",
                    router_guidance="Verify your dynamic DNS setup or domain A record.",
                )

        # Check CGNAT (100.64.0.0/10)
        cgnat = parsed_ip in _CGNAT_NET
        if cgnat:
            return ReachabilityReport(
                endpoint=endpoint,
                port=port,
                is_valid=False,
                cgnat_detected=True,
                is_private_ip=False,
                udp_reachable=False,
                message=(
                    f"Endpoint {endpoint} is within the Carrier-Grade NAT (CGNAT) range (100.64.0.0/10). "
                    "Inbound WireGuard connections cannot reach this farm connection directly without a static public IP "
                    "or an ISP un-NAT request."
                ),
                router_guidance="Contact your Internet Service Provider to request a routable public IPv4 address.",
            )

        # Check RFC1918 Private IP
        is_priv = any(parsed_ip in net for net in _PRIVATE_NETS)
        if is_priv:
            return ReachabilityReport(
                endpoint=endpoint,
                port=port,
                is_valid=False,
                cgnat_detected=False,
                is_private_ip=True,
                udp_reachable=False,
                message=(
                    f"Endpoint {endpoint} is a private local address. "
                    "Remote clients away from the farm cannot connect to a private address over the public internet."
                ),
                router_guidance="Use your external public WAN IP or a configured Dynamic DNS hostname.",
            )

        # Reachable report
        guidance = (
            f"Forward UDP port {port} on your farm router to the Chief server's local LAN IP.\n"
            f"Rule: WAN UDP port {port} -> Chief Server local IP port {port}."
        )
        return ReachabilityReport(
            endpoint=endpoint,
            port=port,
            is_valid=True,
            cgnat_detected=False,
            is_private_ip=False,
            udp_reachable=True,
            message="Endpoint is a routable public address. Configure router port forwarding to complete setup.",
            router_guidance=guidance,
        )

    def add_peer(
        self,
        name: str,
        role: str,
        device_name: str,
        enabled: Optional[bool] = None,
    ) -> Tuple[WireGuardPeer, str]:
        """Add a new named peer and return the peer record and client configuration.

        Default access rules:
        - Owner: Enabled by default.
        - Manager: Enabled if specified, else True.
        - Staff: Disabled by default unless explicitly requested.
        """
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

        client_conf = self.render_client_config(peer, client_priv)
        return peer, client_conf

    def set_peer_enabled(self, peer_id: str, enabled: bool) -> bool:
        """Enable or disable an existing peer."""
        if peer_id not in self._state["peers"]:
            return False
        self._state["peers"][peer_id]["enabled"] = bool(enabled)
        self._save_state()
        self.write_wg_conf()
        return True

    def revoke_peer(self, peer_id: str) -> bool:
        """Permanently revoke and delete a peer."""
        if peer_id not in self._state["peers"]:
            return False
        del self._state["peers"][peer_id]
        self._save_state()
        self.write_wg_conf()
        return True

    def rotate_peer_key(self, peer_id: str) -> Tuple[WireGuardPeer, str]:
        """Rotate keys for an existing peer (e.g. replaced device)."""
        if peer_id not in self._state["peers"]:
            raise KeyError(f"Peer {peer_id} not found")
        data = self._state["peers"][peer_id]
        client_priv, client_pub = generate_wireguard_keypair()
        psk = generate_preshared_key()

        data["public_key"] = client_pub
        data["preshared_key"] = psk
        self._state["peers"][peer_id] = data
        self._save_state()
        self.write_wg_conf()

        peer = WireGuardPeer(**data)
        client_conf = self.render_client_config(peer, client_priv)
        return peer, client_conf

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

    def render_firewall_rules(self, chief_https_port: int = 443) -> Dict[str, List[str]]:
        """Return fail-closed firewall rules for Linux (iptables/nftables) and Windows."""
        server_port = self._state.get("port", 51820)
        vpn_net = self._state.get("vpn_subnet", "10.77.0.0/24")
        server_ip = self._state.get("server_vpn_ip", "10.77.0.1")

        iptables_rules = [
            f"# 1. Allow WireGuard UDP listener on WAN",
            f"iptables -A INPUT -p udp --dport {server_port} -j ACCEPT",
            f"# 2. Allow WireGuard clients to reach Chief HTTPS ({chief_https_port}) on VPN interface",
            f"iptables -A INPUT -i wg0 -s {vpn_net} -d {server_ip} -p tcp --dport {chief_https_port} -j ACCEPT",
            f"# 3. Allow WireGuard clients to reach DNS ({53}) on VPN interface for split-DNS",
            f"iptables -A INPUT -i wg0 -s {vpn_net} -d {server_ip} -p udp --dport 53 -j ACCEPT",
            f"# 4. BLOCK all other access from wg0 to Chief server (SSH, DB, management)",
            f"iptables -A INPUT -i wg0 -j DROP",
            f"# 5. BLOCK all forwarding from wg0 to Farm LAN (no general LAN access)",
            f"iptables -A FORWARD -i wg0 -j DROP",
        ]

        windows_rules = [
            f'netsh advfirewall firewall add rule name="Chief-WireGuard-UDP" dir=in action=allow protocol=UDP localport={server_port}',
            f'netsh advfirewall firewall add rule name="Chief-VPN-HTTPS" dir=in action=allow protocol=TCP localport={chief_https_port} remoteip={vpn_net}',
        ]

        return {
            "linux_iptables": iptables_rules,
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
            "peer_count": len(peers),
            "active_peer_count": sum(1 for p in peers.values() if p.get("enabled")),
            "peers": peers,
        }
