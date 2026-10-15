"""Deferred multi-company boundaries: collisions, references and derived views."""
import base64
import json
from zoneinfo import ZoneInfo

import pytest

from application import farm_api
from domains.farming import (assistant, brief, clarifications, costing_policy,
    egg_loss_links, expense_allocations, journal, labour, live_ai, photos,
    planning, recipients, reconciliation, scope, setup, staff, timezone)
from tests.checkpoint_f_fixture import e_base, f_fixture
from tests.test_farm_tenant_finance import tenants, Handler
from tests.test_farm_tenant_lineage import _house
from tests.test_farm_labour import work
from tests.test_farm_staff import event
from tests.test_farm_photos import png
from tests.test_farm_planning import scenario
from tests.test_farm_journal import record
from tests.test_farm_costing_policy import assumptions


def test_planning_revisions_and_labour_are_farm_local(tenants):
    t = tenants
    for farm, actor, name in [(t.yf, t.ym, 'Yellow plan'), (t.gf, t.gm, 'Green plan')]:
        view = planning.overview(t.store, t.owner, farm_id=farm)
        payload = dict(scenario(name=name), event_id='same-plan-event',
            expected_revision=view['revision'], expected_evidence_revision=view['evidence_revision'])
        assert planning.save(t.store, t.owner, payload, farm_id=farm)['status'] == 'RECORDED'
        assert planning.save(t.store, t.owner, payload, farm_id=farm)['status'] == 'ALREADY_RECORDED'
        labour.append(t.store, actor, work(event_id='same-work-event', person=name))
    before = planning.overview(t.store, t.ym)
    journal.append(t.store, t.gm, record(event_id='green-feed-event'))
    assert planning.overview(t.store, t.ym) == before
    assert 'Green plan' not in json.dumps(before)
    assert 'Green plan' not in json.dumps(labour.overview(t.store, t.ym))
    labour.append(t.store, t.gm, work(event_id='green-only-work'))
    with pytest.raises(ValueError):
        labour.append(t.store, t.ym, work(corrects='green-only-work'))


def test_staff_photos_and_ai_context_never_cross_farms(tenants):
    t = tenants
    for actor, text in [(t.ym, 'Yellow private task'), (t.gm, 'Green private task')]:
        staff.append(t.store, actor, event(event_id='same-work-item', text=text))
        payload = dict(event_id='same-photo-event', work_id='same-work-item',
            image_base64=base64.b64encode(png()).decode())
        saved = photos.append(t.store, actor, payload)
        assert photos.append(t.store, actor, payload)['record'] == saved['record']
        assert photos.listing(t.store, actor, 'same-work-item') == [saved['record']]
        assert photos.image(t.store, actor, 'same-photo-event') == png()
    staff.append(t.store, t.gm, event(event_id='green-only-item', text='Green secret'))
    photos.append(t.store, t.gm, dict(payload, event_id='green-only-photo', work_id='green-only-item'))
    with pytest.raises(PermissionError): photos.image(t.store, t.ym, 'green-only-photo')
    with pytest.raises(PermissionError): photos.listing(t.store, t.ym, 'green-only-item')
    _, context = assistant.context(t.store, t.ym)
    assert 'Yellow private task' in json.dumps(context)
    assert 'Green' not in json.dumps(context)
    assert 'staff-green' not in json.dumps(staff.overview(t.store, t.ym))


def test_settings_recipients_and_timezones_are_independent(tenants, monkeypatch):
    t = tenants
    for farm, actor, zone in [(t.yf, t.ym, 'Europe/London'), (t.gf, t.gm, 'Asia/Tokyo')]:
        monkeypatch.setattr(timezone, 'host_zone', lambda z=zone: ZoneInfo(z))
        setup.append(t.store, t.owner, _house('same-house-event'), farm_id=farm)
        costing_policy.configure(t.store, t.owner, assumptions(event_id='same-cost-event'), farm_id=farm)
        brief.configure(t.store, t.owner, dict(event_id='same-schedule-event', expected_revision=None, requirements=[]), farm_id=farm)
        recipients.configure(t.store, t.owner, dict(event_id='same-recipients-event', expected_revision=None, manager_ids=[actor.id]), farm_id=farm)
    monkeypatch.setattr(timezone, 'host_zone', lambda: ZoneInfo('America/New_York'))
    for farm, actor, zone, other in [(t.yf, t.ym, 'Europe/London', t.gm), (t.gf, t.gm, 'Asia/Tokyo', t.ym)]:
        assert staff.overview(t.store, actor)['timezone'] == zone
        view = recipients.overview(t.store, t.owner, farm_id=farm)
        assert view['manager_ids'] == [actor.id]
        assert other.id not in {u['id'] for u in view['eligible']}
        with pytest.raises(ValueError):
            recipients.configure(t.store, t.owner, dict(event_id='bad-recipient-event', expected_revision=view['revision'], manager_ids=[other.id]), farm_id=farm)
    assert costing_policy.overview(t.store, t.ym)['policies'][0]['scope']['farm_id'] == t.yf


def test_new_api_routes_resolve_selection_and_deny_other_tenant(tenants):
    t = tenants
    paths = ['planning', 'labour', 'units', 'costing/policy', 'physical-counts',
             'notification-recipients', 'brief', 'staff', 'assistant',
             'expense-allocations', 'egg-loss-links', 'clarifications']
    for suffix in paths:
        path = '/api/farm/' + suffix
        h = Handler(path, t.yf)
        assert farm_api.dispatch(h, t.store, t.owner, path, 'GET', None)
        assert h.status == 200, suffix
        with pytest.raises(PermissionError):
            farm_api.dispatch(h, t.store, t.gm, path, 'GET', None)
    assert scope.current() is None


def test_history_limit_is_applied_after_farm_selection(tenants, monkeypatch):
    t = tenants
    journal.append(t.store, t.ym, record(event_id='yellow-record-one'))
    journal.append(t.store, t.gm, record(event_id='green-record-one'))
    journal.append(t.store, t.gm, record(event_id='green-record-two'))
    monkeypatch.setattr(journal, 'LIMIT', 1)
    with scope.bind(t.store, t.ym), t.store._connect() as con:
        assert len(journal.read_rows(con)) == 1
    with scope.bind(t.store, t.gm), t.store._connect() as con:
        with pytest.raises(ValueError, match='capacity'): journal.read_rows(con)


def test_nested_scope_cannot_switch_and_unscoped_provider_stays_closed(tenants):
    from domains.farming.report_snapshot import snapshot
    t = tenants
    with scope.bind(t.store, t.owner, t.yf):
        with pytest.raises(PermissionError): planning.overview(t.store, t.owner, farm_id=t.gf)
        with t.store._connect() as con:
            with pytest.raises(PermissionError): snapshot(con)
    with t.store._connect() as con:
        with pytest.raises(PermissionError): labour.rows(con)
    assert scope.current() is None


def test_cross_farm_links_cannot_grant_reference_authority(tenants):
    t = tenants
    from domains.farming import bookkeeping
    from tests.test_farm_bookkeeping import entry
    bookkeeping.append(t.store, t.gm, entry(event_id='green-expense-id', kind='EXPENSE_CLAIM'))
    with pytest.raises(ValueError):
        expense_allocations.append(t.store, t.owner, dict(event_id='bad-allocation-id', expense_id='green-expense-id',
            expected_revision=None, allocations=[], reason='Synthetic'), farm_id=t.yf)
    journal.append(t.store, t.gm, record(event_id='green-loss-event', kind='eggs_lost', quantity='1'))
    with pytest.raises(ValueError):
        egg_loss_links.append(t.store, t.owner, dict(event_id='bad-loss-link-id', loss_id='green-loss-event',
            expected_revision=None, allocations=[], reason='Synthetic'), farm_id=t.yf)
    labour.append(t.store, t.ym, work(event_id='yellow-work-item'))
    with pytest.raises(ValueError):
        labour.append(t.store, t.owner, dict(event_id='bad-expense-link', operation='LINK_EXPENSE',
            work_id='yellow-work-item', expense_id='green-expense-id', reason='Synthetic'), farm_id=t.yf)


def test_ai_configuration_read_requires_selected_farm(tenants):
    t = tenants
    # Synthetic records contain references only; no provider requests or secrets.
    with scope.bind(t.store, t.owner, t.gf), t.store._connect() as con:
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,0)",
            (live_ai.KIND, json.dumps(scope.stamp({'enabled': False, 'registration': 'green-private-reference'}))))
    with scope.bind(t.store, t.owner, t.yf):
        assert live_ai.configuration(t.store) is None
    with scope.bind(t.store, t.owner, t.gf):
        assert live_ai.configuration(t.store)['registration'] == 'green-private-reference'
    with pytest.raises(PermissionError): live_ai.configuration(t.store)


def test_reconciliation_and_clarification_references_are_local(tenants):
    t = tenants
    from tests.test_farm_reconciliation import count, decide
    for actor in (t.ym, t.gm):
        setup.append(t.store, actor, dict(_house('same-store-event', 'same-feed-store', 'Feed store'), entity_type='FEED_STORE'))
        journal.append(t.store, actor, record(event_id='same-feed-opening', entity_id='same-feed-store', kind='feed_opening', quantity='10'))
        reconciliation.append(t.store, actor, count({'entity_id':'same-feed-store'}, event_id='same-count-event'))
    journal.append(t.store, t.gm, record(event_id='green-feed-change', entity_id='same-feed-store'))
    assert reconciliation.overview(t.store, t.ym)['items'][0]['stale'] is False
    reconciliation.append(t.store, t.owner, decide({'event_id':'same-count-event'}), farm_id=t.yf)
    assert reconciliation.overview(t.store, t.gm)['items'][0]['state'] == 'PENDING'
    journal.append(t.store, t.gm, record(event_id='green-only-source', kind='feed_used', quantity='1'))
    choices = clarifications.overview(t.store, t.gm)['sources']
    selected = next(s for s in choices if s['source']['event_id'] == 'green-only-source' and s['template'] == 'feed')
    with pytest.raises(ValueError):
        clarifications.append(t.store, t.ym, dict(event_id='cross-farm-clarification', operation='create', template='feed', source=selected['source']))
    assert 'green-only-source' not in json.dumps(clarifications.overview(t.store, t.ym))


def test_scoped_notification_ids_do_not_collide(tenants):
    from application.exposure import for_store
    from domains.farming import tasks
    from tests.test_farm_tenant_lineage import _task
    t = tenants
    for_store(t.store)
    for actor in (t.ym, t.gm):
        assert tasks.append(t.store, actor, _task('same-notify-event', 'same-notify-task', actor.id))['status'] == 'RECORDED'
    with t.store._connect() as con:
        events = [json.loads(r[0])['event'] for r in con.execute("SELECT data_json FROM domain_records WHERE kind='chief_notification_event_v1'")]
    assert len(events) == 4
    assert len({e['event_id'] for e in events}) == 4
    audiences = [e['audience']['principal_ids'] for e in events if 'staff' in e['audience']['surfaces']]
    assert [t.ym.id] in audiences and [t.gm.id] in audiences


def test_revoked_assignment_does_not_survive_bound_scope(tenants):
    from domains import tenancy
    t = tenants
    with scope.bind(t.store, t.ym):
        tenancy.append(t.store, t.owner, dict(event_id='revoke-yellow-role', operation='revoke_user_farm',
            assignment_id='assignment-yellow', expected_revision='event-assign-yellow', reason='Synthetic revocation'))
        with t.store._connect() as con:
            with pytest.raises(PermissionError): setup.authorize(t.store, con, t.ym, 'read')


def test_old_scoped_history_preserves_recorded_installation_zone(tenants, monkeypatch):
    t = tenants
    monkeypatch.setattr(timezone, 'host_zone', lambda: ZoneInfo('Asia/Tokyo'))
    old = dict(version=1, timezone='Europe/London', basis='HOST_DETECTED', recorded_at='2026-01-01T00:00:00Z')
    with scope.bind(t.store, t.owner, t.yf), t.store._connect() as con:
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,0)", (timezone.KIND,json.dumps(old)))
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming','synthetic_history',?,0)", (json.dumps(scope.stamp({'test':True})),))
    with scope.bind(t.store, t.owner, t.yf), t.store._connect() as con:
        assert timezone.setting(con)['timezone'] == 'Europe/London'
        con.execute('BEGIN IMMEDIATE')
        timezone.pin(con)
        assert timezone.setting(con)['timezone'] == 'Europe/London'
    with scope.bind(t.store, t.owner, t.gf), t.store._connect() as con:
        assert timezone.setting(con)['timezone'] == 'Asia/Tokyo'


def test_conversion_and_allocation_versions_do_not_cross_farms(tenants):
    from domains.farming import bookkeeping, units
    from tests.test_farm_bookkeeping import entry
    t = tenants
    for farm, actor, factor in [(t.yf, t.ym, 25), (t.gf, t.gm, 50)]:
        costing_policy.configure(t.store, t.owner, assumptions(event_id='same-unit-version', kg_per_bag=str(factor)), farm_id=farm)
        assert units.overview(t.store, actor)['kg_per_bag'] == str(factor)
        journal.append(t.store, actor, record(event_id='same-bag-delivery', quantity=str(factor),
            conversion=dict(unit='bags', quantity='1', policy_revision='same-unit-version')))
        setup.append(t.store, actor, _house('same-house-create'))
        setup.append(t.store, actor, dict(_house('same-flock-create', 'same-flock-id', 'Flock'), entity_type='FLOCK', house_id='sharedhouse1'))
        bookkeeping.append(t.store, actor, entry(event_id='same-expense-id', kind='EXPENSE_CLAIM', amount_minor=factor*100))
        payload = dict(event_id='same-allocation-event', expense_id='same-expense-id', expected_revision=None,
            allocations=[dict(flock_id='same-flock-id', amount_minor=factor)], reason='Explicit shares')
        expense_allocations.append(t.store, t.owner, payload, farm_id=farm)
        assert expense_allocations.append(t.store, t.owner, payload, farm_id=farm)['status'] == 'ALREADY_RECORDED'
        assert expense_allocations.overview(t.store, actor)['items'][0]['unallocated_minor'] == factor*99
    with pytest.raises(ValueError, match='does not match'):
        journal.append(t.store, t.ym, record(event_id='wrong-bag-size', quantity='50',
            conversion=dict(unit='bags', quantity='1', policy_revision='same-unit-version')))
