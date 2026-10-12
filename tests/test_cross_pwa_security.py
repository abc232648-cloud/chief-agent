"""Adversarial multi-surface requests; a PWA label is never an authority grant."""
from dataclasses import replace

import pytest

from application.exposure import for_store
from capabilities.contracts import Mode, Node
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_tasks import create_task, event
from tests.test_identity_http import request

SURFACES = ('/api/ui/context', '/api/ui/evidence/jobs', '/api/farm/tasks', '/api/ui/job-feed')


def test_all_surfaces_reject_missing_revoked_and_disabled_identity(dashboard):
    d = dashboard
    service = IdentityService(d.store)
    identity = service.create_user(d.credentials['principal'], 'revocable', PASSWORD, 'Owner', ('*',))
    raw, principal = service.login('revocable', PASSWORD)
    for path in SURFACES:
        assert request(d, path)[0] == 401
        assert request(d, path, raw=raw)[0] == 200
    service.revoke(principal)
    for path in SURFACES:
        assert request(d, path, raw=raw)[0] == 401
    raw, _ = service.login('revocable', PASSWORD)
    service.disable_user(d.credentials['principal'], identity)
    for path in SURFACES:
        assert request(d, path, raw=raw)[0] == 401


@pytest.mark.parametrize('change', [
    {'csrf': False}, {'origin': False},
    {'extra': {'Origin': 'https://untrusted.invalid'}},
    {'extra': {'Sec-Fetch-Site': 'cross-site'}},
])
def test_cross_pwa_mutations_keep_csrf_origin_and_no_side_effects(dashboard, change):
    d = dashboard
    raw = d.credentials['raw']
    service = IdentityService(d.store)
    worker = service.create_user(d.credentials['principal'], 'assignee', PASSWORD, 'Worker', ('farming',))
    task = create_task(worker)
    with d.store._connect() as con:
        before_commands = con.execute('SELECT count(*) FROM commands').fetchone()[0]
        before_records = con.execute('SELECT count(*) FROM domain_records').fetchone()[0]
    for path, body in [('/api/farm/tasks', task), ('/api/command', {'command': 'Synthetic request'})]:
        assert request(d, path, 'POST', body, raw, **change)[0] == 403
    with d.store._connect() as con:
        assert con.execute('SELECT count(*) FROM commands').fetchone()[0] == before_commands
        assert con.execute('SELECT count(*) FROM domain_records').fetchone()[0] == before_records


@pytest.mark.parametrize('failure', ['disabled', 'incompatible', 'missing', 'stale'])
def test_cached_discovery_cannot_authorize_requests_after_module_change(dashboard, failure):
    d = dashboard
    raw = d.credentials['raw']
    gate = for_store(d.store)
    cached = request(d, '/api/ui/context', raw=raw)[2]
    assert {row['agent_id'] for row in cached['interfaces']} == {'jobs', 'farming'}
    if failure == 'disabled':
        for domain in ('jobs', 'farming'):
            node = Node('component', gate.lifecycle.manifests.runtime_agent_id(domain))
            controls = gate.lifecycle.controls
            controls.transition(controls.preview(node, Mode.DISABLED), actor='a10-test', reason='Revoke exposure', confirmed=True)
    elif failure == 'incompatible':
        gate.lifecycle.compatibility.profile = replace(gate.lifecycle.compatibility.profile, chief_version='2.0.0')
    else:
        for domain in ('jobs', 'farming'):
            if failure == 'missing':
                del gate.lifecycle.manifests._manifests[domain]
            else:
                gate.lifecycle.manifests._manifests[domain] = replace(gate.lifecycle.manifests.get(domain))
    forged = {'X-Chief-Role': 'Owner', 'X-Chief-Interface': 'owner', 'X-Module-Enabled': 'true'}
    for path in SURFACES[1:]:
        for method in ('GET', 'HEAD'):
            assert request(d, path + '?compatible=true&enabled=true', method, raw=raw, extra=forged)[0] == 403
    current = request(d, '/api/ui/context', raw=raw)[2]
    assert current['interfaces'] == [] and current['domains'] == []
    assert request(d, '/api/component-controls', raw=raw)[0] == 200


def test_staff_object_isolation_replay_and_stale_revision_over_http(dashboard):
    d = dashboard
    service = IdentityService(d.store)
    owner = d.credentials['principal']
    worker_id = service.create_user(owner, 'assigned', PASSWORD, 'Worker', ('farming',))
    service.create_user(owner, 'outsider', PASSWORD, 'Worker', ('farming',))
    worker_raw, _ = service.login('assigned', PASSWORD)
    other_raw, _ = service.login('outsider', PASSWORD)
    task = create_task(worker_id, title='ASSIGNED_PRIVATE_MARKER')
    assert request(d, '/api/farm/tasks', 'POST', task, d.credentials['raw'])[0] == 200
    code, _, result = request(d, '/api/farm/tasks', raw=other_raw)
    assert code == 200 and result['tasks'] == []
    assigned = request(d, '/api/farm/tasks', raw=worker_raw)[2]
    assert set(assigned) == {'tasks', 'total', 'next_offset', 'farm_role', 'can_create', 'notice'}
    assert assigned['total'] == 1 and assigned['farm_role'] == 'WORKER'
    assert assigned['can_create'] is False and assigned['next_offset'] is None
    capabilities = assigned['tasks'][0]['capabilities']
    assert set(capabilities) == {'can_acknowledge', 'can_start', 'can_submit', 'can_verify',
                                 'can_request_correction', 'can_escalate', 'can_reassign', 'can_cancel'}
    assert all(type(value) is bool for value in capabilities.values())
    ack = event('ACKNOWLEDGE', task['task_id'], task['event_id'])
    assert request(d, '/api/farm/tasks', 'POST', ack, other_raw)[0] == 403
    code, _, result = request(d, '/api/farm/tasks', 'POST', ack, worker_raw)
    assert code == 200 and result['status'] == 'RECORDED'
    assert request(d, '/api/farm/tasks', 'POST', ack, worker_raw)[2]['status'] == 'ALREADY_RECORDED'
    stale = event('START', task['task_id'], task['event_id'])
    assert request(d, '/api/farm/tasks', 'POST', stale, worker_raw)[0] == 400
    # Owning a Staff task does not grant installation or Companion authority.
    assert request(d, '/api/ui/job-feed', raw=worker_raw)[0] == 403
    assert request(d, '/api/model-controls', raw=worker_raw)[0] == 403
    current = request(d, '/api/farm/tasks', raw=worker_raw)[2]['tasks'][0]
    assert current['revision'] == ack['event_id'] and current['status'] == 'ACKNOWLEDGED'


def test_current_identity_overrides_cached_role_scope_and_client_claims(dashboard):
    d = dashboard
    service = IdentityService(d.store)
    identity = service.create_user(d.credentials['principal'], 'downgraded', PASSWORD, 'Owner', ('*',))
    raw, _ = service.login('downgraded', PASSWORD)
    assert request(d, '/api/ui/job-feed', raw=raw)[0] == 200
    with d.store._connect() as con:
        con.execute('UPDATE human_identities SET role=?,domains=? WHERE id=?',
                    ('Worker', '["farming"]', identity))
    claims = {'X-Chief-Role': 'Owner', 'X-Chief-Domains': '*', 'X-Chief-Interface': 'owner'}
    for path in ('/api/ui/job-feed', '/api/ui/evidence/farming', '/api/component-controls'):
        assert request(d, path, raw=raw, extra=claims)[0] == 403
    code, _, context = request(d, '/api/ui/context', raw=raw, extra=claims)
    assert code == 200 and context['role'] == 'Worker'
    assert {(row['agent_id'], row['kind']) for row in context['interfaces']} == {('farming', 'staff')}
    assert request(d, '/api/farm/tasks', raw=raw)[0] == 200
