import json

import pytest

from deployment.ownership_bootstrap import bootstrap_initial
from domains import ownership
from tests.checkpoint_f_fixture import PASSWORD, f_fixture


def test_first_install_bootstrap_creates_initial_company_and_farm(f_fixture):
    result = bootstrap_initial(f_fixture.store, f_fixture.owner.id, "Yellow Farm Ltd", "Yellow Farm")

    assert result["organization_name"] == "Yellow Farm Ltd"
    assert result["farm_name"] == "Yellow Farm"
    assert result["organization_id"].startswith("org-")
    assert result["farm_id"].startswith("farm-")

    catalog = ownership.overview(f_fixture.store, f_fixture.owner)
    assert catalog["organization_count"] == 1
    assert catalog["farm_count"] == 1
    company = catalog["organizations"][0]
    assert company["organization_id"] == result["organization_id"]
    assert company["name"] == "Yellow Farm Ltd"
    assert company["farms"][0]["farm_id"] == result["farm_id"]
    assert company["farms"][0]["name"] == "Yellow Farm"

    with f_fixture.store._connect() as con:
        rows = [json.loads(row[0]) for row in con.execute(
            "SELECT data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id",
            (ownership.DOMAIN, ownership.KIND),
        )]
        assert all(row["actor_id"] == f_fixture.owner.id for row in rows)
        assert all(row["session_id"] is None for row in rows)
        event = con.execute(
            "SELECT operation,authority FROM human_security_events WHERE operation='CHIEF_OWNERSHIP_BOOTSTRAP'"
        ).fetchone()
        assert tuple(event) == ("CHIEF_OWNERSHIP_BOOTSTRAP", "INSTALLER")


def test_first_install_bootstrap_allows_organization_without_farm(f_fixture):
    result = bootstrap_initial(f_fixture.store, f_fixture.owner.id, "Solo Consulting")
    assert result["farm_id"] is None and result["farm_name"] is None
    catalog = ownership.overview(f_fixture.store, f_fixture.owner)
    assert catalog["organization_count"] == 1
    assert catalog["farm_count"] == 0


def test_first_install_bootstrap_requires_installation_owner(f_fixture):
    service = f_fixture.identity
    service.create_user(f_fixture.owner, "manager-bootstrap", PASSWORD, "Manager", ("farming",))
    _, manager = service.login("manager-bootstrap", PASSWORD)
    with pytest.raises(PermissionError, match="installation-wide Owner"):
        bootstrap_initial(f_fixture.store, manager.id, "Denied Ltd", "Denied Farm")


def test_first_install_bootstrap_refuses_nonempty_ownership_ledger(f_fixture):
    bootstrap_initial(f_fixture.store, f_fixture.owner.id, "One Ltd", "One Farm")
    with pytest.raises(PermissionError, match="empty ownership ledger"):
        bootstrap_initial(f_fixture.store, f_fixture.owner.id, "Two Ltd", "Two Farm")


def test_first_install_bootstrap_uses_catalog_name_validation(f_fixture):
    with pytest.raises(ValueError, match="unsupported characters"):
        bootstrap_initial(f_fixture.store, f_fixture.owner.id, "Bad\nCompany", "Farm")
