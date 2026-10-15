from domains.farming import sops
from tests.checkpoint_f_fixture import e_base, f_fixture
from tests.test_farm_tenant_lineage import _two_company_fixture


def _sop(event_id, title):
    return {
        "event_id": event_id,
        "operation": "PUBLISH",
        "sop_id": "brooder01",
        "version": "1.0",
        "title": title,
        "purpose": "Tenant-scoped field procedure.",
        "steps": ["Inspect the assigned Farm area."],
        "warnings": [],
    }


def test_same_sop_identity_can_exist_independently_in_unrelated_farms(f_fixture):
    store, _, _, _, yellow_farm, green_farm, yellow_manager, green_manager = _two_company_fixture(f_fixture)

    yellow = sops.publish(store, yellow_manager, _sop("yellowsop01", "Yellow Farm SOP"), farm_id=yellow_farm)
    green = sops.publish(store, green_manager, _sop("greensop001", "Green Farm SOP"), farm_id=green_farm)

    assert yellow["status"] == "RECORDED"
    assert green["status"] == "RECORDED"
    assert [item["title"] for item in sops.overview(store, yellow_manager, farm_id=yellow_farm)["sops"]] == ["Yellow Farm SOP"]
    assert [item["title"] for item in sops.overview(store, green_manager, farm_id=green_farm)["sops"]] == ["Green Farm SOP"]


def test_sop_catalog_rejects_cross_farm_selection(f_fixture):
    store, _, _, _, yellow_farm, green_farm, yellow_manager, _ = _two_company_fixture(f_fixture)
    sops.publish(store, yellow_manager, _sop("yellowsop02", "Yellow Only"), farm_id=yellow_farm)

    try:
        sops.overview(store, yellow_manager, farm_id=green_farm)
    except PermissionError as exc:
        assert "not authorized" in str(exc)
    else:
        raise AssertionError("Cross-farm SOP access must fail closed.")
