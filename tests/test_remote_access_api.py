"""Integration and role-gating tests for WireGuard remote access API endpoints."""
from pathlib import Path
from types import SimpleNamespace
import pytest

from application.remote_access_api import dispatch


class FakeHandler:
    def __init__(self, path: str, method: str, host: str = "chief-farm.local"):
        self.path = path
        self.method = method
        self.host = host
        self.response_data = None
        self.response_status = 200

    def json(self, data, status: int = 200):
        self.response_data = data
        self.response_status = status


class FakeStore:
    def __init__(self, tmp_path: Path):
        self.private_dir = tmp_path / "private"
        self.private_dir.mkdir(parents=True, exist_ok=True)


def test_remote_access_api_role_gating(tmp_path):
    store = FakeStore(tmp_path)
    owner = SimpleNamespace(id="u1", role="Owner")
    manager = SimpleNamespace(id="u2", role="Manager")
    worker = SimpleNamespace(id="u3", role="Worker")

    handler = FakeHandler("/api/settings/remote-access", "GET")

    # 1. Manager must be rejected with PermissionError
    with pytest.raises(PermissionError, match="restricted to Owner or Administrator"):
        dispatch(handler, store, manager, "/api/settings/remote-access", "GET", {})

    # 2. Worker must be rejected with PermissionError
    with pytest.raises(PermissionError, match="restricted to Owner or Administrator"):
        dispatch(handler, store, worker, "/api/settings/remote-access", "GET", {})

    # 3. Owner is permitted
    assert dispatch(handler, store, owner, "/api/settings/remote-access", "GET", {}) is True
    assert handler.response_status == 200
    assert handler.response_data["enabled"] is False


def test_remote_access_api_configuration_lifecycle(tmp_path):
    store = FakeStore(tmp_path)
    owner = SimpleNamespace(id="u1", role="Owner")

    # 1. Preflight check for CGNAT
    h_pre = FakeHandler("/api/settings/remote-access/preflight", "POST")
    assert dispatch(h_pre, store, owner, "/api/settings/remote-access/preflight", "POST", {"endpoint": "100.64.1.2"}) is True
    assert h_pre.response_data["cgnat_detected"] is True

    # 2. Initialize server
    h_init = FakeHandler("/api/settings/remote-access", "POST")
    init_body = {
        "endpoint": "203.0.113.15",
        "canonical_hostname": "chief-farm.local",
        "allow_unsupported_platform": True,
        "port": 51820,
        "vpn_subnet": "10.77.0.0/24",
        "allow_unsupported_platform": True,
    }
    assert dispatch(h_init, store, owner, "/api/settings/remote-access", "POST", init_body) is True
    assert h_init.response_data["status"] == "CONFIGURED"

    # 3. Add Owner peer
    h_peer1 = FakeHandler("/api/settings/remote-access/peers", "POST")
    peer1_body = {"name": "Jane Owner", "role": "Owner", "device_name": "iPhone 15 Pro"}
    assert dispatch(h_peer1, store, owner, "/api/settings/remote-access/peers", "POST", peer1_body) is True
    res1 = h_peer1.response_data
    assert res1["status"] == "PEER_CREATED"
    assert res1["peer"]["enabled"] is True
    assert "AllowedIPs = 10.77.0.1/32" in res1["client_config"]
    owner_peer_id = res1["peer"]["peer_id"]

    # 4. Add Staff peer (must default to DISABLED)
    h_peer2 = FakeHandler("/api/settings/remote-access/peers", "POST")
    peer2_body = {"name": "Sam Field", "role": "Staff", "device_name": "Rugged Tablet"}
    assert dispatch(h_peer2, store, owner, "/api/settings/remote-access/peers", "POST", peer2_body) is True
    res2 = h_peer2.response_data
    assert res2["peer"]["enabled"] is False  # Policy default: Staff disabled
    staff_peer_id = res2["peer"]["peer_id"]

    # 5. Enable Staff peer
    h_enable = FakeHandler(f"/api/settings/remote-access/peers/{staff_peer_id}", "PATCH")
    assert dispatch(h_enable, store, owner, f"/api/settings/remote-access/peers/{staff_peer_id}", "PATCH", {"enabled": True}) is True
    assert h_enable.response_data["enabled"] is True

    # 6. Rotate keys for Owner peer
    h_rotate = FakeHandler(f"/api/settings/remote-access/peers/{owner_peer_id}/rotate", "POST")
    assert dispatch(h_rotate, store, owner, f"/api/settings/remote-access/peers/{owner_peer_id}/rotate", "POST", {}) is True
    assert h_rotate.response_data["status"] == "KEY_ROTATED"
    assert "client_config" in h_rotate.response_data

    # 7. Revoke Staff peer
    h_del = FakeHandler(f"/api/settings/remote-access/peers/{staff_peer_id}", "DELETE")
    assert dispatch(h_del, store, owner, f"/api/settings/remote-access/peers/{staff_peer_id}", "DELETE", {}) is True
    assert h_del.response_data["status"] == "REVOKED"


def test_remote_access_api_replace_device(tmp_path):
    store = FakeStore(tmp_path)
    owner = SimpleNamespace(id="u1", role="Owner")

    # Initialize server
    h_init = FakeHandler("/api/settings/remote-access", "POST")
    dispatch(h_init, store, owner, "/api/settings/remote-access", "POST", {
        "endpoint": "203.0.113.15",
        "canonical_hostname": "chief-farm.local",
    })

    # Add peer
    h_peer = FakeHandler("/api/settings/remote-access/peers", "POST")
    dispatch(h_peer, store, owner, "/api/settings/remote-access/peers", "POST", {
        "name": "Jane Owner",
        "role": "Owner",
        "device_name": "Old Phone",
    })
    peer_id = h_peer.response_data["peer"]["peer_id"]

    # Replace device
    h_rep = FakeHandler(f"/api/settings/remote-access/peers/{peer_id}/replace", "POST")
    dispatch(h_rep, store, owner, f"/api/settings/remote-access/peers/{peer_id}/replace", "POST", {
        "device_name": "New Phone Pro",
    })
    assert h_rep.response_data["status"] == "DEVICE_REPLACED"
    assert h_rep.response_data["peer"]["device_name"] == "New Phone Pro"
    assert "client_config" in h_rep.response_data


def test_vpn_peer_still_requires_chief_account_and_role_authorization(tmp_path):
    store = FakeStore(tmp_path)
    # A caller connected via VPN IP with no Chief session or with unprivileged role is denied
    worker = SimpleNamespace(id="u_field", role="Worker")
    handler = FakeHandler("/api/settings/remote-access", "POST", host="10.77.0.1")

    with pytest.raises(PermissionError, match="restricted to Owner or Administrator"):
        dispatch(handler, store, worker, "/api/settings/remote-access", "POST", {"endpoint": "203.0.113.10"})
