import pytest

from domains import ownership, tenancy
from tests.checkpoint_f_fixture import PASSWORD, e_base, f_fixture


def make_org(store, owner, suffix, name):
    organization_id = f"org-{suffix}"
    ownership.append(store, owner, {
        "event_id": f"event-org-{suffix}",
        "operation": "create_organization",
        "organization_id": organization_id,
        "name": name,
    })
    return organization_id


def make_farm(store, owner, suffix, organization_id, name):
    farm_id = f"farm-{suffix}"
    ownership.append(store, owner, {
        "event_id": f"event-farm-{suffix}",
        "operation": "create_farm",
        "farm_id": farm_id,
        "organization_id": organization_id,
        "name": name,
    })
    return farm_id


def assign(store, owner, suffix, human_id, organization_id, farm_id, role="WORKER"):
    assignment_id = f"assignment-{suffix}"
    result = tenancy.append(store, owner, {
        "event_id": f"event-assignment-{suffix}",
        "operation": "assign_user_farm",
        "assignment_id": assignment_id,
        "human_id": human_id,
        "organization_id": organization_id,
        "farm_id": farm_id,
        "role": role,
        "reason": "Synthetic tenant scope",
    })
    assert result["status"] == "RECORDED"
    return assignment_id


def test_unrelated_company_users_only_see_their_assigned_farm(f_fixture):
    store, owner, service = f_fixture.store, f_fixture.owner, f_fixture.identity
    yellow = make_org(store, owner, "yellow", "Yellow Farm Ltd")
    green = make_org(store, owner, "green", "Green Agriculture Ltd")
    yellow_farm = make_farm(store, owner, "yellow-main", yellow, "Yellow Main Farm")
    green_farm = make_farm(store, owner, "green-main", green, "Green Main Farm")

    yellow_user = service.create_user(owner, "yellow-worker", PASSWORD, "Worker", ("farming",))
    green_user = service.create_user(owner, "green-worker", PASSWORD, "Worker", ("farming",))
    assign(store, owner, "yellow-worker", yellow_user, yellow, yellow_farm)
    assign(store, owner, "green-worker", green_user, green, green_farm)

    _, yellow_principal = service.login("yellow-worker", PASSWORD)
    _, green_principal = service.login("green-worker", PASSWORD)

    yellow_scopes = tenancy.visible_farms(store, yellow_principal)
    green_scopes = tenancy.visible_farms(store, green_principal)
    assert [(item["organization_name"], item["farm_name"]) for item in yellow_scopes] == [("Yellow Farm Ltd", "Yellow Main Farm")]
    assert [(item["organization_name"], item["farm_name"]) for item in green_scopes] == [("Green Agriculture Ltd", "Green Main Farm")]
    assert tenancy.overview(store, yellow_principal) == {"global": False, "farms": yellow_scopes, "farm_count": 1}
    with pytest.raises(PermissionError, match="not authorized"):
        tenancy.resolve_farm(store, yellow_principal, green_farm)


def test_one_user_can_have_multiple_farms_but_must_select_when_ambiguous(f_fixture):
    store, owner, service = f_fixture.store, f_fixture.owner, f_fixture.identity
    company = make_org(store, owner, "multi", "ABC Agriculture Ltd")
    lagos = make_farm(store, owner, "lagos", company, "Lagos Farm")
    ogun = make_farm(store, owner, "ogun", company, "Ogun Farm")
    manager_id = service.create_user(owner, "multi-manager", PASSWORD, "Manager", ("farming",))
    assign(store, owner, "multi-lagos", manager_id, company, lagos, "GENERAL_MANAGER")
    assign(store, owner, "multi-ogun", manager_id, company, ogun, "SUPERVISOR")
    _, manager = service.login("multi-manager", PASSWORD)

    scopes = tenancy.visible_farms(store, manager)
    assert {item["farm_id"] for item in scopes} == {lagos, ogun}
    with pytest.raises(ValueError, match="Select a Farm"):
        tenancy.resolve_farm(store, manager)
    assert tenancy.resolve_farm(store, manager, lagos)["tenant_role"] == "GENERAL_MANAGER"
    assert tenancy.resolve_farm(store, manager, ogun)["tenant_role"] == "SUPERVISOR"


def test_tenant_roles_enforce_farm_local_authority(f_fixture):
    store, owner, service = f_fixture.store, f_fixture.owner, f_fixture.identity
    company = make_org(store, owner, "roles", "Role Farm Ltd")
    farm_id = make_farm(store, owner, "roles", company, "Role Farm")
    worker_id = service.create_user(owner, "role-worker", PASSWORD, "Worker", ("farming",))
    supervisor_id = service.create_user(owner, "role-supervisor", PASSWORD, "Worker", ("farming",))
    manager_id = service.create_user(owner, "role-manager", PASSWORD, "Manager", ("farming",))
    assign(store, owner, "roles-worker", worker_id, company, farm_id, "WORKER")
    assign(store, owner, "roles-supervisor", supervisor_id, company, farm_id, "SUPERVISOR")
    assign(store, owner, "roles-manager", manager_id, company, farm_id, "GENERAL_MANAGER")
    _, worker = service.login("role-worker", PASSWORD)
    _, supervisor = service.login("role-supervisor", PASSWORD)
    _, manager = service.login("role-manager", PASSWORD)

    assert tenancy.require_farm(store, worker, farm_id, action="request")["farm_id"] == farm_id
    with pytest.raises(PermissionError):
        tenancy.require_farm(store, worker, farm_id, action="manage")
    assert tenancy.require_farm(store, supervisor, farm_id, action="manage")["farm_id"] == farm_id
    with pytest.raises(PermissionError):
        tenancy.require_farm(store, supervisor, farm_id, action="setup")
    assert tenancy.require_farm(store, manager, farm_id, action="setup")["farm_id"] == farm_id


def test_general_manager_assignment_requires_manager_identity(f_fixture):
    store, owner, service = f_fixture.store, f_fixture.owner, f_fixture.identity
    company = make_org(store, owner, "gm", "GM Farm Ltd")
    farm_id = make_farm(store, owner, "gm", company, "GM Farm")
    worker_id = service.create_user(owner, "gm-worker", PASSWORD, "Worker", ("farming",))
    with pytest.raises(ValueError, match="Manager-level"):
        assign(store, owner, "bad-gm", worker_id, company, farm_id, "GENERAL_MANAGER")


def test_tenant_assignment_must_match_farm_ownership(f_fixture):
    store, owner, service = f_fixture.store, f_fixture.owner, f_fixture.identity
    one = make_org(store, owner, "one", "One Ltd")
    two = make_org(store, owner, "two", "Two Ltd")
    farm_id = make_farm(store, owner, "one", one, "One Farm")
    worker_id = service.create_user(owner, "wrong-org-worker", PASSWORD, "Worker", ("farming",))
    with pytest.raises(ValueError, match="belongs to the selected organization"):
        assign(store, owner, "wrong-org", worker_id, two, farm_id)


def test_assignment_change_and_revocation_are_revision_checked(f_fixture):
    store, owner, service = f_fixture.store, f_fixture.owner, f_fixture.identity
    company = make_org(store, owner, "revision", "Revision Ltd")
    farm_id = make_farm(store, owner, "revision", company, "Revision Farm")
    manager_id = service.create_user(owner, "revision-manager", PASSWORD, "Manager", ("farming",))
    assignment_id = assign(store, owner, "revision", manager_id, company, farm_id, "SUPERVISOR")

    changed = tenancy.append(store, owner, {
        "event_id": "event-role-change",
        "operation": "change_user_farm_role",
        "assignment_id": assignment_id,
        "expected_revision": "event-assignment-revision",
        "role": "GENERAL_MANAGER",
        "reason": "Promoted",
    })
    assert changed["status"] == "RECORDED"
    with pytest.raises(ValueError, match="changed or is missing"):
        tenancy.append(store, owner, {
            "event_id": "event-stale-revoke",
            "operation": "revoke_user_farm",
            "assignment_id": assignment_id,
            "expected_revision": "event-assignment-revision",
            "reason": "Stale",
        })
    tenancy.append(store, owner, {
        "event_id": "event-valid-revoke",
        "operation": "revoke_user_farm",
        "assignment_id": assignment_id,
        "expected_revision": "event-role-change",
        "reason": "Access ended",
    })
    _, manager = service.login("revision-manager", PASSWORD)
    with pytest.raises(PermissionError, match="not assigned"):
        tenancy.resolve_farm(store, manager)


def test_installation_authorities_are_global_not_tenant_assignments(f_fixture):
    store, owner, service = f_fixture.store, f_fixture.owner, f_fixture.identity
    one = make_org(store, owner, "global-one", "Global One Ltd")
    two = make_org(store, owner, "global-two", "Global Two Ltd")
    one_farm = make_farm(store, owner, "global-one", one, "One Farm")
    two_farm = make_farm(store, owner, "global-two", two, "Two Farm")
    assert {item["farm_id"] for item in tenancy.visible_farms(store, owner)} == {one_farm, two_farm}

    admin_id = service.create_user(owner, "global-admin", PASSWORD, "Administrator", ("*",))
    _, admin = service.login("global-admin", PASSWORD)
    assert {item["farm_id"] for item in tenancy.visible_farms(store, admin)} == {one_farm, two_farm}
    with pytest.raises(PermissionError, match="global"):
        assign(store, owner, "bad-admin", admin_id, one, one_farm, "WORKER")
