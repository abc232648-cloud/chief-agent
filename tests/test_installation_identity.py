import json
import os

import pytest

from deployment.installation_identity import (
    SCHEMA,
    display_name,
    local_host_label,
    local_hostname,
    new_identity,
    rename_plan,
    write_identity,
)


def test_owner_name_drives_safe_local_hostname():
    assert display_name("  Yellow   Farm Ltd  ") == "Yellow Farm Ltd"
    assert local_host_label("Yellow Farm Ltd") == "chief-yellow-farm-ltd"
    assert local_hostname("Yellow Farm Ltd") == "chief-yellow-farm-ltd.local"
    assert len(local_host_label("A" * 200 if False else "A" * 120)) <= 63


def test_non_ascii_name_still_gets_dns_safe_stable_shape():
    host = local_hostname("農場")
    assert host.startswith("chief-owner-")
    assert host.endswith(".local")
    assert host == local_hostname("農場")


def test_identity_has_immutable_ids_and_managed_network_metadata():
    identity = new_identity("Green Farm")
    assert identity["schema"] == SCHEMA
    assert len(identity["chief_id"]) == 32
    assert len(identity["owner_id"]) == 32
    assert identity["owner_display_name"] == "Green Farm"
    assert identity["network"] == {
        "hostname": "chief-green-farm.local",
        "mode": "LOCAL_MDNS",
        "managed_from_owner_name": True,
    }


def test_public_hostname_is_explicit_advanced_override():
    identity = new_identity("Red Farm", hostname="chief.redfarm.example")
    assert identity["network"]["hostname"] == "chief.redfarm.example"
    assert identity["network"]["mode"] == "PUBLIC_DNS"
    assert identity["network"]["managed_from_owner_name"] is False


def test_rename_proposes_origin_change_but_does_not_apply_it_silently():
    identity = new_identity("Yellow Farm")
    plan = rename_plan(identity, "Yellow Agro Industries")
    assert plan == {
        "owner_display_name": "Yellow Agro Industries",
        "current_hostname": "chief-yellow-farm.local",
        "proposed_hostname": "chief-yellow-agro-industries.local",
        "hostname_change": True,
        "requires_origin_migration": True,
    }
    assert identity["owner_display_name"] == "Yellow Farm"
    assert identity["network"]["hostname"] == "chief-yellow-farm.local"


def test_public_dns_identity_rename_does_not_rewrite_manually_managed_origin():
    identity = new_identity("Acme Farms", hostname="chief.acme.example")
    plan = rename_plan(identity, "Acme Agriculture")
    assert plan["proposed_hostname"] == "chief.acme.example"
    assert plan["hostname_change"] is False


def test_identity_write_is_private_and_exclusive(tmp_path):
    path = tmp_path / "installation-identity.json"
    identity = new_identity("Yellow Farm")
    write_identity(path, identity)
    assert json.loads(path.read_text(encoding="utf-8")) == identity
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_identity(path, identity)


@pytest.mark.parametrize("value", ["", "   ", "bad\nname", "x" * 121])
def test_invalid_owner_names_fail_closed(value):
    with pytest.raises(ValueError):
        display_name(value)
