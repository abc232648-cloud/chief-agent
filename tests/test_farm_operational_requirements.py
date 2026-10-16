import uuid
from datetime import datetime, timezone
import pytest
from domains.farming import brief, journal, reports, setup
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_setup import entity
from tests.test_farm_staff import people
from tests.test_identity_http import request


def ident():
    return str(uuid.uuid4())


def make_entity(**changes):
    base = dict(event_id=str(uuid.uuid4()), operation='create_entity', entity_id=str(uuid.uuid4()),
                entity_type='FEED_STORE', name='Main store', opened_on='2025-01-01', house_id=None)
    base.update(changes)
    return base


def test_equipment_entity_setup_and_operational_reporting(dashboard):
    d = dashboard
    owner = d.credentials['principal']
    pump = make_entity(entity_type='EQUIPMENT', name='Borehole Pump 1')
    fridge = make_entity(entity_type='EQUIPMENT', name='Vaccine Fridge')
    assert setup.append(d.store, owner, pump)['status'] == 'RECORDED'
    assert setup.append(d.store, owner, fridge)['status'] == 'RECORDED'

    entities = setup.entities(d.store._connect().__enter__())
    assert entities[pump['entity_id']]['entity_type'] == 'EQUIPMENT'
    assert entities[fridge['entity_id']]['entity_type'] == 'EQUIPMENT'


def test_operational_report_categories_validation_and_lineage(dashboard):
    d = dashboard
    owner, uid, worker, _, other = people(d)

    # Equipment entity
    generator = make_entity(entity_type='EQUIPMENT', name='Backup Generator')
    setup.append(d.store, owner, generator)

    # 1. Equipment report
    eq_rep = dict(
        event_id=ident(), category='EQUIPMENT', task_id=None,
        entity_id=generator['entity_id'], location='Backup Generator',
        observed_at=utc_text(utc_now()),
        summary='Generator test run completed: oil pressure normal, diesel 80%.',
        quantity='80', unit='%', basis='MEASURED', severity='ROUTINE'
    )
    assert reports.append(d.store, worker, eq_rep)['status'] == 'RECEIVED'

    # 2. Vaccine storage temperature report
    fridge = make_entity(entity_type='EQUIPMENT', name='Cold Storage Fridge')
    setup.append(d.store, owner, fridge)
    vac_rep = dict(
        event_id=ident(), category='VACCINE_STORAGE', task_id=None,
        entity_id=fridge['entity_id'], location='Cold Storage Fridge',
        observed_at=utc_text(utc_now()),
        summary='Morning cold-chain check: fridge reading at 4.2°C.',
        quantity='4.2', unit='C', basis='MEASURED', severity='ROUTINE'
    )
    assert reports.append(d.store, worker, vac_rep)['status'] == 'RECEIVED'

    # 3. Water sanitation check
    water_rep = dict(
        event_id=ident(), category='WATER', task_id=None,
        entity_id=None, location='Main Water Tank',
        observed_at=utc_text(utc_now()),
        summary='Water chlorination test: residual chlorine 1.5 ppm, flow unobstructed.',
        quantity='1.5', unit='ppm', basis='MEASURED', severity='ROUTINE'
    )
    assert reports.append(d.store, worker, water_rep)['status'] == 'RECEIVED'

    # 4. Mortality disposal log
    flock_h = make_entity(entity_type='HOUSE', name='House A')
    setup.append(d.store, owner, flock_h)
    flock_ent = make_entity(entity_type='FLOCK', name='Flock Alpha', house_id=flock_h['entity_id'])
    setup.append(d.store, owner, flock_ent)

    disp_rep = dict(
        event_id=ident(), category='DISPOSAL', task_id=None,
        entity_id=flock_ent['entity_id'], location='Flock Alpha',
        observed_at=utc_text(utc_now()),
        summary='Deep burial of 3 culled birds conducted in designated biosecurity pit with lime.',
        quantity='3', unit='birds', basis='COUNTED', severity='ROUTINE'
    )
    assert reports.append(d.store, worker, disp_rep)['status'] == 'RECEIVED'

    # 5. Worker safety report
    safe_rep = dict(
        event_id=ident(), category='SAFETY', task_id=None,
        entity_id=None, location='Chemical Store',
        observed_at=utc_text(utc_now()),
        summary='Full PPE (respirator, gloves, goggles) verified before pen fogging.',
        quantity=None, unit=None, basis=None, severity='ROUTINE'
    )
    assert reports.append(d.store, worker, safe_rep)['status'] == 'RECEIVED'

    # Category filtering in overview
    eq_overview = reports.overview(d.store, worker, category='EQUIPMENT')
    assert eq_overview['total'] == 1
    assert eq_overview['reports'][0]['payload']['category'] == 'EQUIPMENT'

    vac_overview = reports.overview(d.store, worker, category='VACCINE_STORAGE')
    assert vac_overview['total'] == 1
    assert vac_overview['reports'][0]['payload']['category'] == 'VACCINE_STORAGE'

    all_overview = reports.overview(d.store, worker)
    assert all_overview['total'] == 5


def test_http_worker_reports_category_filtering(dashboard):
    d = dashboard
    owner, uid, worker, _, _ = people(d)
    raw = d.credentials['raw']

    # Submit water report
    rep = dict(
        event_id=ident(), category='WATER', task_id=None,
        entity_id=None, location='Borehole Well',
        observed_at=utc_text(utc_now()),
        summary='Borehole water pump operational and header tank filling.',
        quantity=None, unit=None, basis=None, severity='ROUTINE'
    )
    code, _, body = request(d, '/api/farm/worker-reports', method='POST', body=rep, raw=raw)
    assert code == 200 and body['status'] == 'RECEIVED'

    # Query with category filter
    code, _, body = request(d, '/api/farm/worker-reports?category=WATER', raw=raw)
    assert code == 200 and body['total'] >= 1
    assert all(r['payload']['category'] == 'WATER' for r in body['reports'])


def test_morning_operational_checklist_brief_integration(dashboard, monkeypatch):
    d = dashboard
    owner, uid, worker, _, other = people(d)

    # Register equipment
    pump = make_entity(entity_type='EQUIPMENT', name='Solar Water Pump')
    setup.append(d.store, owner, pump)

    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(brief, 'utc_now', lambda: now)

    # Schedule morning operational check for equipment
    sched_payload = {
        'event_id': ident(),
        'expected_revision': None,
        'requirements': [
            {'entity_id': pump['entity_id'], 'kind': 'equipment_checked', 'due_time': '10:00', 'assignee': uid}
        ]
    }
    assert brief.configure(d.store, owner, sched_payload)['effective_on'] == '2026-01-03'

    # On next day: before deadline, status is AWAITING
    monkeypatch.setattr(brief, 'utc_now', lambda: datetime(2026, 1, 3, 8, 0, tzinfo=timezone.utc))
    view = brief.overview(d.store, worker)
    assert len(view['reporting']) == 1
    assert view['reporting'][0]['status'] == 'AWAITING'

    # Worker submits matching equipment check report
    monkeypatch.setattr(reports, 'utc_now', lambda: datetime(2026, 1, 3, 8, 30, tzinfo=timezone.utc))
    rep = dict(
        event_id=ident(), category='EQUIPMENT', task_id=None,
        entity_id=pump['entity_id'], location=pump['name'],
        observed_at='2026-01-03T08:30:00Z',
        summary='Solar pump started cleanly; water pressure 3.2 bar.',
        quantity='3.2', unit='bar', basis='MEASURED', severity='ROUTINE'
    )
    reports.append(d.store, worker, rep)

    # Now brief overview marks check as REPORTED
    view_updated = brief.overview(d.store, worker)
    assert view_updated['reporting'][0]['status'] == 'REPORTED'
    assert view_updated['alerts'] == []
