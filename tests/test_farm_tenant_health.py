from domains.farming import health_records, setup
from operations.time_integrity import utc_now, utc_text
from tests.checkpoint_f_fixture import e_base, f_fixture
from tests.test_farm_tenant_lineage import _house, _two_company_fixture


def _flock(event_id):
    return {
        "event_id": event_id,
        "operation": "create_entity",
        "entity_id": "sharedflock1",
        "entity_type": "FLOCK",
        "name": "Layer Flock",
        "opened_on": None,
        "house_id": "sharedhouse1",
    }


def _health(event_id):
    return {
        "event_id": event_id,
        "kind": "OBSERVATION",
        "entity_id": "sharedflock1",
        "observed_at": utc_text(utc_now()),
        "summary": "Routine flock observation.",
        "product": "",
        "quantity": None,
        "unit": "",
        "administered_by": "",
        "batch": "",
        "expires_on": None,
        "source_reference": "",
        "instructions": "",
        "instruction_source": "",
        "corrects": None,
        "reason": "",
    }


def _seed_flock(store, principal, farm_id, suffix):
    setup.append(store, principal, _house(f"house-{suffix}"), farm_id=farm_id)
    setup.append(store, principal, _flock(f"flock-{suffix}"), farm_id=farm_id)


def test_health_history_isolated_for_same_flock_id_across_companies(f_fixture):
    store, _, _, _, yellow_farm, green_farm, yellow_manager, green_manager = _two_company_fixture(f_fixture)
    _seed_flock(store, yellow_manager, yellow_farm, "yellow01")
    _seed_flock(store, green_manager, green_farm, "green001")

    assert health_records.append(store, yellow_manager, _health("yellowhealth1"), farm_id=yellow_farm)["status"] == "RECORDED"
    assert health_records.append(store, green_manager, _health("greenhealth01"), farm_id=green_farm)["status"] == "RECORDED"

    yellow = health_records.overview(store, yellow_manager, farm_id=yellow_farm)
    green = health_records.overview(store, green_manager, farm_id=green_farm)
    assert [item["payload"]["event_id"] for item in yellow["items"]] == ["yellowhealth1"]
    assert [item["payload"]["event_id"] for item in green["items"]] == ["greenhealth01"]


def test_health_record_cannot_select_another_company_farm(f_fixture):
    store, _, _, _, yellow_farm, green_farm, yellow_manager, _ = _two_company_fixture(f_fixture)
    _seed_flock(store, yellow_manager, yellow_farm, "yellow02")
    try:
        health_records.overview(store, yellow_manager, farm_id=green_farm)
    except PermissionError as exc:
        assert "not authorized" in str(exc)
    else:
        raise AssertionError("Cross-farm health access must fail closed.")
