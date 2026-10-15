import pytest

from domains import ownership
from tests.checkpoint_f_fixture import PASSWORD, e_base, f_fixture


def org(event, organization_id, name):
    return {
        "event_id": event,
        "operation": "create_organization",
        "organization_id": organization_id,
        "name": name,
    }


def farm(event, farm_id, organization_id, name):
    return {
        "event_id": event,
        "operation": "create_farm",
        "farm_id": farm_id,
        "organization_id": organization_id,
        "name": name,
    }


def test_one_chief_supports_multiple_companies_and_multiple_farms(f_fixture):
    store, owner = f_fixture.store, f_fixture.owner
    assert ownership.append(store, owner, org("e-org-yellow", "org-yellow", "Yellow Farm Ltd"))["status"] == "RECORDED"
    assert ownership.append(store, owner, org("e-org-green", "org-green", "Green Agriculture Ltd"))["status"] == "RECORDED"
    ownership.append(store, owner, farm("e-farm-lagos", "farm-lagos", "org-yellow", "Lagos Farm"))
    ownership.append(store, owner, farm("e-farm-ogun", "farm-ogun", "org-yellow", "Ogun Farm"))
    ownership.append(store, owner, farm("e-farm-green", "farm-green-main", "org-green", "Green Main Farm"))

    result = ownership.overview(store, owner)
    assert result["organization_count"] == 2
    assert result["farm_count"] == 3
    companies = {item["organization_id"]: item for item in result["organizations"]}
    assert {item["farm_id"] for item in companies["org-yellow"]["farms"]} == {"farm-lagos", "farm-ogun"}
    assert {item["farm_id"] for item in companies["org-green"]["farms"]} == {"farm-green-main"}


def test_renames_preserve_organization_and_farm_identity(f_fixture):
    store, owner = f_fixture.store, f_fixture.owner
    ownership.append(store, owner, org("e-org-create", "org-yellow", "Yellow Farm Ltd"))
    ownership.append(store, owner, farm("e-farm-create", "farm-main", "org-yellow", "Yellow Farm"))

    ownership.append(store, owner, {
        "event_id": "e-org-rename",
        "operation": "rename_organization",
        "organization_id": "org-yellow",
        "expected_revision": "e-org-create",
        "name": "Yellow Agro Industries",
        "reason": "Company renamed",
    })
    ownership.append(store, owner, {
        "event_id": "e-farm-rename",
        "operation": "rename_farm",
        "farm_id": "farm-main",
        "expected_revision": "e-farm-create",
        "name": "Yellow Poultry Farm",
        "reason": "Farm display name updated",
    })

    result = ownership.overview(store, owner)
    company = result["organizations"][0]
    assert company["organization_id"] == "org-yellow"
    assert company["name"] == "Yellow Agro Industries"
    assert company["revision"] == "e-org-rename"
    assert company["farms"][0]["farm_id"] == "farm-main"
    assert company["farms"][0]["organization_id"] == "org-yellow"
    assert company["farms"][0]["name"] == "Yellow Poultry Farm"
    assert company["farms"][0]["revision"] == "e-farm-rename"


def test_same_farm_name_can_exist_in_different_companies_but_not_same_company(f_fixture):
    store, owner = f_fixture.store, f_fixture.owner
    ownership.append(store, owner, org("e-o1", "org-one", "One Ltd"))
    ownership.append(store, owner, org("e-o2", "org-two", "Two Ltd"))
    ownership.append(store, owner, farm("e-f1", "farm-one", "org-one", "Main Farm"))
    ownership.append(store, owner, farm("e-f2", "farm-two", "org-two", "Main Farm"))
    with pytest.raises(ValueError, match="already exists in this organization"):
        ownership.append(store, owner, farm("e-f3", "farm-three", "org-one", "main farm"))


def test_duplicate_company_display_names_are_rejected_case_insensitively(f_fixture):
    store, owner = f_fixture.store, f_fixture.owner
    ownership.append(store, owner, org("e-o1", "org-one", "Yellow Farm Ltd"))
    with pytest.raises(ValueError, match="organization with that name"):
        ownership.append(store, owner, org("e-o2", "org-two", "yellow farm ltd"))


def test_stale_rename_is_rejected_without_overwriting_newer_name(f_fixture):
    store, owner = f_fixture.store, f_fixture.owner
    ownership.append(store, owner, org("e-create", "org-one", "One Ltd"))
    ownership.append(store, owner, {
        "event_id": "e-rename-1",
        "operation": "rename_organization",
        "organization_id": "org-one",
        "expected_revision": "e-create",
        "name": "One Agriculture Ltd",
        "reason": "First rename",
    })
    with pytest.raises(ValueError, match="changed or is missing"):
        ownership.append(store, owner, {
            "event_id": "e-rename-stale",
            "operation": "rename_organization",
            "organization_id": "org-one",
            "expected_revision": "e-create",
            "name": "Stale Name Ltd",
            "reason": "Stale edit",
        })
    assert ownership.overview(store, owner)["organizations"][0]["name"] == "One Agriculture Ltd"


def test_event_submission_is_idempotent_but_conflicts_fail_closed(f_fixture):
    store, owner = f_fixture.store, f_fixture.owner
    payload = org("e-create", "org-one", "One Ltd")
    assert ownership.append(store, owner, payload)["status"] == "RECORDED"
    assert ownership.append(store, owner, payload)["status"] == "ALREADY_RECORDED"
    with pytest.raises(ValueError, match="Submission identifier conflict"):
        ownership.append(store, owner, {**payload, "name": "Different Ltd"})


def test_manager_cannot_read_or_modify_cross_company_catalog(f_fixture):
    service, store, owner = f_fixture.identity, f_fixture.store, f_fixture.owner
    service.create_user(owner, "catalog-manager", PASSWORD, "Manager", ("farming",))
    _, manager = service.login("catalog-manager", PASSWORD)
    with pytest.raises(PermissionError):
        ownership.overview(store, manager)
    with pytest.raises(PermissionError):
        ownership.append(store, manager, org("e-denied", "org-denied", "Denied Ltd"))


def test_farm_requires_existing_company_and_names_reject_control_characters(f_fixture):
    store, owner = f_fixture.store, f_fixture.owner
    with pytest.raises(ValueError, match="existing organization"):
        ownership.append(store, owner, farm("e-orphan", "farm-orphan", "missing-org", "Orphan Farm"))
    with pytest.raises(ValueError, match="unsupported characters"):
        ownership.append(store, owner, org("e-bad", "org-bad", "Bad\nName"))