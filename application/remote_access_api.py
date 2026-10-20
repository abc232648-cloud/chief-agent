"""HTTP API endpoint handlers for Chief WireGuard remote-access settings and peer management.

Security:
- All routes are strictly restricted to principals with role 'Owner' or 'Administrator'.
- Managers, Workers, and unauthenticated callers receive fail-closed 403 Forbidden.
- Private keys are never exposed in JSON responses or stored in logs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit

from networking.remote_access import UnsupportedPlatformError, WireGuardManager


def _get_manager(store: Any) -> WireGuardManager:
    # State directory in store's private root or default fallback
    state_dir = getattr(store, "private_dir", Path("state")) / "wireguard"
    return WireGuardManager(state_dir)


def dispatch(handler: Any, store: Any, principal: Any, path: str, method: str, body: Dict[str, Any]) -> bool:
    parsed = urlsplit(handler.path)
    clean_path = parsed.path.rstrip("/")

    if not clean_path.startswith("/api/settings/remote-access"):
        return False

    # Strict role gating: Owner and Administrator only
    if getattr(principal, "role", None) not in {"Owner", "Administrator"}:
        raise PermissionError("Remote access configuration is restricted to Owner or Administrator.")

    mgr = _get_manager(store)

    # 1. Base status & configuration: /api/settings/remote-access
    if clean_path == "/api/settings/remote-access":
        if method in {"GET", "HEAD"}:
            handler.json(mgr.get_safe_status())
            return True
        elif method == "POST":
            endpoint = str(body.get("endpoint", "")).strip()
            canonical_hostname = str(body.get("canonical_hostname", getattr(handler, "host", "chief.local"))).strip()
            port = int(body.get("port", 51820))
            vpn_subnet = str(body.get("vpn_subnet", "10.77.0.0/24")).strip()
            farm_lan_cidr = body.get("farm_lan_cidr")
            allow_unsupported = bool(body.get("allow_unsupported_platform", False))
            try:
                status = mgr.initialize_server(
                    endpoint=endpoint,
                    canonical_hostname=canonical_hostname,
                    port=port,
                    vpn_subnet=vpn_subnet,
                    farm_lan_cidr=farm_lan_cidr,
                    allow_unsupported_platform=allow_unsupported,
                )
                handler.json({"status": "CONFIGURED", "details": status})
            except UnsupportedPlatformError as exc:
                handler.json({"status": "UNSUPPORTED_PLATFORM", "error": str(exc)}, 400)
            return True

    # 2. Reachability & CGNAT preflight: /api/settings/remote-access/preflight
    if clean_path == "/api/settings/remote-access/preflight" and method == "POST":
        endpoint = str(body.get("endpoint", "")).strip()
        port = int(body.get("port", 51820))
        report = mgr.check_reachability(endpoint, port)
        handler.json(report.to_dict())
        return True

    # 3. Peers list & creation: /api/settings/remote-access/peers
    if clean_path == "/api/settings/remote-access/peers":
        if method in {"GET", "HEAD"}:
            handler.json(mgr.get_safe_status().get("peers", {}))
            return True
        elif method == "POST":
            name = str(body.get("name", "")).strip()
            role = str(body.get("role", "Owner")).strip()
            device_name = str(body.get("device_name", "Mobile Device")).strip()
            enabled = body.get("enabled")  # None defaults to role-based default
            if not name:
                raise ValueError("Peer name is required.")
            peer, client_conf = mgr.add_peer(name=name, role=role, device_name=device_name, enabled=enabled)
            handler.json({
                "status": "PEER_CREATED",
                "peer": peer.to_safe_dict(),
                "client_config": client_conf,
            })
            return True

    # 4. Peer operations: /api/settings/remote-access/peers/<peer_id>[/rotate|/replace]
    if clean_path.startswith("/api/settings/remote-access/peers/"):
        parts = clean_path.split("/")
        peer_id = parts[5]
        subaction = parts[6] if len(parts) > 6 else None

        if subaction == "rotate" and method == "POST":
            peer, client_conf = mgr.rotate_peer_key(peer_id)
            handler.json({
                "status": "KEY_ROTATED",
                "peer": peer.to_safe_dict(),
                "client_config": client_conf,
            })
            return True
        elif subaction == "replace" and method == "POST":
            new_device = str(body.get("device_name", body.get("new_device_name", "Replaced Device"))).strip()
            peer, client_conf = mgr.replace_peer_device(peer_id, new_device)
            handler.json({
                "status": "DEVICE_REPLACED",
                "peer": peer.to_safe_dict(),
                "client_config": client_conf,
            })
            return True
        elif not subaction:
            if method in {"POST", "PATCH"}:
                enabled = bool(body.get("enabled", True))
                ok = mgr.set_peer_enabled(peer_id, enabled)
                if not ok:
                    handler.json({"error": "Peer not found"}, 404)
                else:
                    handler.json({"status": "UPDATED", "peer_id": peer_id, "enabled": enabled})
                return True
            elif method == "DELETE":
                ok = mgr.revoke_peer(peer_id)
                if not ok:
                    handler.json({"error": "Peer not found"}, 404)
                else:
                    handler.json({"status": "REVOKED", "peer_id": peer_id})
                return True

    return False
