import json

from domains.farming import tasks
from tests.checkpoint_f_fixture import e_base, f_fixture
from tests.test_farm_tenant_lineage import _staff, _task, _two_company_fixture


def test_task_notification_staff_audience_does_not_cross_company_boundary(f_fixture):
    store, owner, yellow_org, green_org, yellow_farm, green_farm, yellow_manager, green_manager = _two_company_fixture(f_fixture)
    yellow_worker_id, _ = _staff(
        store, owner, f_fixture.identity, "yellow-notify-worker", yellow_org, yellow_farm,
        chief_role="Worker", farm_role="WORKER",
    )
    green_worker_id, _ = _staff(
        store, owner, f_fixture.identity, "green-notify-worker", green_org, green_farm,
        chief_role="Worker", farm_role="WORKER",
    )

    result = tasks.append(
        store,
        yellow_manager,
        _task("yellownotifyevent", "yellownotifytask", yellow_worker_id),
        farm_id=yellow_farm,
    )
    assert result["status"] == "RECORDED"

    with store._connect() as con:
        wrappers = [
            json.loads(row[0])
            for row in con.execute(
                "SELECT data_json FROM domain_records WHERE kind='chief_notification_event_v1' ORDER BY id"
            )
        ]

    staff_events = [
        wrapper["event"]
        for wrapper in wrappers
        if wrapper["event"].get("object_id") == "yellownotifytask"
        and "staff" in wrapper["event"]["audience"]["surfaces"]
    ]
    assert len(staff_events) == 1
    recipients = set(staff_events[0]["audience"]["principal_ids"])
    assert yellow_worker_id in recipients
    assert yellow_manager.id in recipients
    assert green_worker_id not in recipients
    assert green_manager.id not in recipients
