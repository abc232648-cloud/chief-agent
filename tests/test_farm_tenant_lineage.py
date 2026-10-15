from datetime import timedelta

import pytest

from application import farm_api
from domains import ownership, tenancy
from domains.farming import incidents, journal, reports, setup, tasks
from operations.time_integrity import utc_now, utc_text
from tests.checkpoint_f_fixture import PASSWORD, e_base, f_fixture


def _organization(store, owner, suffix, name):
    organization_id = f"org-{suffix}"
    ownership.append(store, owner, {
        "event_id": f"event-org-{suffix}",
        "operation": "create_organization",
        "organization_id": organization_id,
        "name": name,
    })
    return organization_id


def _farm(store, owner, suffix, organization_id, name):
    farm_id = f"farm-{suffix}"
    ownership.append(store, owner, {
        "event_id": f"event-farm-{suffix}",
        "operation": "create_farm",
        "farm_id": farm_id,
        "organization_id": organization_id,
        "name": name,
    })
    return farm_id


def _staff(store, owner, service, suffix, organization_id, farm_id, *, chief_role, farm_role):
    username = f"staff-{suffix}"
    human_id = service.create_user(owner, username, PASSWORD, chief_role, ("farming",))
    tenancy.append(store, owner, {
        "event_id": f"event-assign-{suffix}",
        "operation": "assign_user_farm",
        "assignment_id": f"assignment-{suffix}",
        "human_id": human_id,
        "organization_id": organization_id,
        "farm_id": farm_id,
        "role": farm_role,
        "reason": "Tenant lineage isolation test",
    })
    _, principal = service.login(username, PASSWORD)
    return human_id, principal


def _manager(store, owner, service, suffix, organization_id, farm_id):
    return _staff(
        store, owner, service, suffix, organization_id, farm_id,
        chief_role="Manager", farm_role="GENERAL_MANAGER",
    )[1]


def _worker(store, owner, service, suffix, organization_id, farm_id):
    return _staff(
        store, owner, service, suffix, organization_id, farm_id,
        chief_role="Worker", farm_role="WORKER",
    )


def _two_company_fixture(f_fixture):
    store, owner, service = f_fixture.store, f_fixture.owner, f_fixture.identity
    yellow_org = _organization(store, owner, "yellow", "Yellow Farm Ltd")
    green_org = _organization(store, owner, "green", "Green Agriculture Ltd")
    yellow_farm = _farm(store, owner, "yellow", yellow_org, "Yellow Main Farm")
    green_farm = _farm(store, owner, "green", green_org, "Green Main Farm")
    yellow_manager = _manager(store, owner, service, "yellow", yellow_org, yellow_farm)
    green_manager = _manager(store, owner, service, "green", green_org, green_farm)
    return store, owner, yellow_org, green_org, yellow_farm, green_farm, yellow_manager, green_manager


def _house(event_id, entity_id="sharedhouse1", name="Main House"):
    return {
        "event_id": event_id,
        "operation": "create_entity",
        "entity_id": entity_id,
        "entity_type": "HOUSE",
        "name": name,
        "opened_on": None,
        "house_id": None,
    }


def _opening(event_id, observed_at, *, quantity="100", corrects=None, reason=""):
    return {
        "event_id": event_id,
        "kind": "birds_opening",
        "location": "Main Pen",
        "quantity": quantity,
        "basis": "MEASURED",
        "observed_at": observed_at,
        "notes": "",
        "corrects": corrects,
        "reason": reason,
    }


def _task(event_id, task_id, assigned_to):
    return {
        "event_id": event_id,
        "operation": "CREATE",
        "task_id": task_id,
        "title": "Check drinkers",
        "instructions": "Inspect every drinker and report blockages.",
        "location": "Main House",
        "entity_id": None,
        "assigned_to": assigned_to,
        "supervisor_id": None,
        "due_at": utc_text(utc_now() + timedelta(hours=2)),
        "priority": "ROUTINE",
        "evidence_required": ["NOTE"],
        "sop_ref": None,
    }


def _incident(event_id):
    return {
        "event_id": event_id,
        "operation": "CREATE",
        "kind": "INCIDENT",
        "category": "Water",
        "location": "Main House",
        "observed_at": utc_text(utc_now()),
        "summary": "Water line pressure dropped.",
        "severity": "IMPORTANT",
        "immediate_risk": False,
    }


def _report(event_id):
    return {
        "event_id": event_id,
        "category": "OBSERVATION",
        "task_id": None,
        "entity_id": None,
        "location": "Main House",
        "observed_at": utc_text(utc_now()),
        "summary": "Routine visual inspection completed.",
        "quantity": None,
        "unit": None,
        "basis": None,
        "severity": "ROUTINE",
    }


def test_setup_entities_are_isolated_even_when_ids_and_names_collide(f_fixture):
    store, _, _, _, yellow_farm, green_farm, yellow_manager, green_manager = _two_company_fixture(f_fixture)

    yellow = setup.append(store, yellow_manager, _house("yellowhouseevent"), farm_id=yellow_farm)
    green = setup.append(store, green_manager, _house("greenhouseevent"), farm_id=green_farm)

    assert yellow["record"]["scope"] == {"organization_id": "org-yellow", "farm_id": yellow_farm}
    assert green["record"]["scope"] == {"organization_id": "org-green", "farm_id": green_farm}
    assert [item["entity_id"] for item in setup.overview(store, yellow_manager, farm_id=yellow_farm)["entities"]] == ["sharedhouse1"]
    assert [item["entity_id"] for item in setup.overview(store, green_manager, farm_id=green_farm)["entities"]] == ["sharedhouse1"]

    with pytest.raises(PermissionError, match="not authorized"):
        setup.overview(store, yellow_manager, farm_id=green_farm)


def test_journal_records_and_corrections_cannot_cross_company_boundary(f_fixture):
    store, _, _, _, yellow_farm, green_farm, yellow_manager, green_manager = _two_company_fixture(f_fixture)
    observed = utc_text(utc_now())

    yellow = journal.append(store, yellow_manager, _opening("yellowopen01", observed), farm_id=yellow_farm)
    assert yellow["record"]["scope"] == {"organization_id": "org-yellow", "farm_id": yellow_farm}

    yellow_view = journal.overview(store, yellow_manager, farm_id=yellow_farm)
    green_view = journal.overview(store, green_manager, farm_id=green_farm)
    assert [row["payload"]["event_id"] for row in yellow_view["records"]] == ["yellowopen01"]
    assert green_view["records"] == []

    with pytest.raises(ValueError, match="Correction target is missing"):
        journal.append(
            store,
            green_manager,
            _opening("greencorrect1", observed, quantity="90", corrects="yellowopen01", reason="Attempt cross-farm correction"),
            farm_id=green_farm,
        )
    with pytest.raises(PermissionError, match="not authorized"):
        journal.overview(store, green_manager, farm_id=yellow_farm)


def test_tasks_reject_cross_farm_assignees_and_hide_other_company_tasks(f_fixture, monkeypatch):
    store, owner, yellow_org, _, yellow_farm, green_farm, yellow_manager, green_manager = _two_company_fixture(f_fixture)
    yellow_worker_id, _ = _worker(store, owner, f_fixture.identity, "yellow-worker", yellow_org, yellow_farm)
    monkeypatch.setattr("domains.farming.notification_events.publish", lambda *args, **kwargs: None)

    created = tasks.append(store, yellow_manager, _task("yellowtaskevent", "yellowtask01", yellow_worker_id), farm_id=yellow_farm)
    assert created["status"] == "RECORDED"
    assert tasks.overview(store, green_manager, farm_id=green_farm)["tasks"] == []
    with pytest.raises(FileNotFoundError, match="Task not found"):
        tasks.append(store, green_manager, {
            "event_id": "greenstart01",
            "operation": "START",
            "task_id": "yellowtask01",
            "expected_revision": "yellowtaskevent",
        }, farm_id=green_farm)
    with pytest.raises(PermissionError, match="selected Farm"):
        tasks.append(store, green_manager, _task("greentaskevent", "greentask001", yellow_worker_id), farm_id=green_farm)


def test_incidents_and_reports_are_isolated_by_farm(f_fixture, monkeypatch):
    store, _, _, _, yellow_farm, green_farm, yellow_manager, green_manager = _two_company_fixture(f_fixture)
    monkeypatch.setattr("domains.farming.notification_events.publish", lambda *args, **kwargs: None)

    incident = incidents.append(store, yellow_manager, _incident("yellowincident1"), farm_id=yellow_farm)
    report = reports.append(store, yellow_manager, _report("yellowreport01"), farm_id=yellow_farm)
    assert incident["status"] == "RECORDED"
    assert report["status"] == "RECEIVED"
    assert incidents.overview(store, green_manager, farm_id=green_farm)["incidents"] == []
    assert reports.overview(store, green_manager, farm_id=green_farm)["reports"] == []
    with pytest.raises(FileNotFoundError, match="Incident not found"):
        incidents.append(store, green_manager, {
            "event_id": "greenescalate1",
            "operation": "ESCALATE",
            "incident_id": "yellowincident1",
            "expected_revision": "yellowincident1",
            "reason": "Attempt cross-farm escalation",
        }, farm_id=green_farm)


def test_global_owner_must_select_farm_when_multiple_exist(f_fixture):
    store, owner, _, _, yellow_farm, _, _, _ = _two_company_fixture(f_fixture)
    with pytest.raises(ValueError, match="Select a Farm"):
        setup.overview(store, owner)
    assert setup.overview(store, owner, farm_id=yellow_farm)["entities"] == []


def test_legacy_assign_role_is_blocked_once_tenant_farms_exist(f_fixture):
    store, owner, _, _, yellow_farm, _, yellow_manager, _ = _two_company_fixture(f_fixture)
    worker_id = f_fixture.identity.create_user(owner, "tenant-worker", PASSWORD, "Worker", ("farming",))
    with pytest.raises(PermissionError, match="managed through Chief tenancy"):
        setup.append(store, yellow_manager, {
            "event_id": "legacyroleevent",
            "operation": "assign_role",
            "human_id": worker_id,
            "role": "WORKER",
            "reason": "Must use tenancy ledger",
        }, farm_id=yellow_farm)


def test_unconverted_farm_api_fails_closed_in_tenant_mode(monkeypatch):
    calls = []

    class Handler:
        path = "/api/farm/finance"
        headers = {}

        def json(self, payload, status=200):
            calls.append((status, payload))

    monkeypatch.setattr(farm_api, "require_farm_runtime", lambda: None)
    monkeypatch.setattr("domains.farming.scope.tenant_mode", lambda store: True)

    assert farm_api.dispatch(Handler(), object(), object(), "/api/farm/finance", "GET", None) is True
    assert calls == [(503, {
        "status": "TENANT_SCOPE_PENDING",
        "reason": "This Farm endpoint is blocked until its records are Farm-scoped.",
    })]


def test_legacy_no_catalog_records_remain_unscoped(f_fixture):
    store, owner = f_fixture.store, f_fixture.owner
    result = setup.append(store, owner, _house("legacyhouseevent"))
    assert "scope" not in result["record"]
    assert setup.overview(store, owner)["entities"][0]["entity_id"] == "sharedhouse1"
