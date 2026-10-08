import uuid

import pytest

from domains.farming import sops, tasks
from tests.test_farm_tasks import create_task, staff
from tests.test_identity_http import request


def ident():
    return str(uuid.uuid4())


def publication(**changes):
    payload = dict(
        event_id=ident(), operation='PUBLISH', sop_id='poultry-water-check', version='1.0.0',
        title='Poultry water-line check',
        purpose='Verify that birds have safe and continuous access to water.',
        steps=['Inspect the header tank.', 'Walk the drinker line.', 'Report leaks or pressure loss.'],
        warnings=['Do not enter an unsafe electrical or flooded area.'],
    )
    payload.update(changes)
    return payload


def test_published_sop_versions_are_immutable_and_idempotent(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, _, _, _, _ = staff(d)
    payload = publication()
    first = sops.publish(d.store, manager, payload)
    assert first['status'] == 'RECORDED'
    assert first['sop_ref'] == 'poultry-water-check@1.0.0'
    assert sops.publish(d.store, manager, payload)['status'] == 'ALREADY_RECORDED'
    with pytest.raises(ValueError, match='immutable'):
        sops.publish(d.store, manager, {**payload, 'event_id': ident(), 'title': 'Changed title'})
    second = publication(event_id=ident(), version='1.1.0', title='Poultry water-line check — revised')
    assert sops.publish(d.store, manager, second)['status'] == 'RECORDED'
    catalog = sops.overview(d.store, manager)
    assert catalog['total'] == 2
    assert {item['version'] for item in catalog['sops']} == {'1.0.0', '1.1.0'}


def test_assigned_sops_follow_authoritative_task_scope(dashboard):
    d = dashboard
    _, worker_id, _, supervisor_id, _, manager, _, worker, other, _, supervisor = staff(d)
    pub = publication(); sops.publish(d.store, manager, pub)
    ref = sops.reference(pub['sop_id'], pub['version'])
    task = create_task(worker_id, supervisor_id, sop_ref=ref)
    tasks.append(d.store, manager, task)

    worker_view = sops.assigned(d.store, worker)
    assert worker_view['total'] == 1
    assert worker_view['sops'][0] == {
        'sop_id': pub['sop_id'], 'version': pub['version'], 'title': pub['title'],
        'purpose': pub['purpose'], 'steps': pub['steps'], 'warnings': pub['warnings'],
        'updated_at': worker_view['sops'][0]['updated_at'],
    }
    assert sops.assigned(d.store, other)['sops'] == []
    assert sops.assigned(d.store, supervisor)['total'] == 1
    assert sops.assigned(d.store, manager)['total'] == 1


def test_assigned_sops_never_substitute_unversioned_or_missing_content(dashboard):
    d = dashboard
    _, worker_id, _, _, _, manager, _, worker, _, _, _ = staff(d)
    pub = publication(); sops.publish(d.store, manager, pub)
    tasks.append(d.store, manager, create_task(worker_id, sop_ref=pub['sop_id']))
    result = sops.assigned(d.store, worker)
    assert result['sops'] == [] and result['total'] == 0
    assert 'unavailable or unversioned' in result['notice']

    # A correctly pinned but unpublished version is also unavailable; Chief does
    # not silently replace it with the published 1.0.0 version.
    tasks.append(d.store, manager, create_task(worker_id, sop_ref=pub['sop_id'] + '@9.9.9'))
    result = sops.assigned(d.store, worker)
    assert result['sops'] == []
    assert 'unavailable or unversioned' in result['notice']


def test_sop_http_contract_matches_staff_pwa(dashboard):
    d = dashboard
    _, worker_id, _, supervisor_id, manager_raw, manager, worker_raw, _, _, _, _ = staff(d)
    pub = publication()
    code, _, receipt = request(d, '/api/farm/sops', 'POST', pub, manager_raw)
    assert code == 200 and receipt['status'] == 'RECORDED'
    ref = receipt['sop_ref']
    tasks.append(d.store, manager, create_task(worker_id, supervisor_id, sop_ref=ref))

    code, _, body = request(d, '/api/farm/sops/assigned', raw=worker_raw)
    assert code == 200 and body['total'] == 1
    item = body['sops'][0]
    assert set(item) == {'sop_id', 'version', 'title', 'purpose', 'steps', 'warnings', 'updated_at'}
    assert item['sop_id'] == pub['sop_id'] and item['version'] == pub['version']

    # Staff's assigned endpoint is read-only.
    assert request(d, '/api/farm/sops/assigned', 'POST', {}, worker_raw)[0] == 405
