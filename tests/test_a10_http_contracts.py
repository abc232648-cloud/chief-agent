"""Consumer-facing contracts exercised through Chief's real authenticated HTTP boundary."""
import json

import pytest

from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


@pytest.mark.parametrize('role,domains,interfaces,allowed,denied', [
    ('Owner', ('*',), {('jobs', 'owner'), ('jobs', 'companion'), ('farming', 'owner')},
     ['/api/ui/job-feed', '/api/farm/tasks', '/api/ui/evidence/jobs'], []),
    ('Administrator', ('jobs',), {('jobs', 'owner'), ('jobs', 'companion')},
     ['/api/ui/job-feed', '/api/ui/evidence/jobs'], ['/api/farm/tasks', '/api/ui/evidence/farming']),
    ('Owner', ('farming',), {('farming', 'owner')},
     ['/api/farm/tasks', '/api/ui/evidence/farming'], ['/api/ui/job-feed', '/api/jobs']),
    ('Manager', ('farming',), {('farming', 'staff')},
     ['/api/farm/tasks'], ['/api/ui/job-feed', '/api/ui/evidence/farming', '/api/component-controls']),
    ('Worker', ('farming',), {('farming', 'staff')},
     ['/api/farm/tasks'], ['/api/ui/job-feed', '/api/ui/evidence/farming', '/api/component-controls']),
    # Legacy Job access is not a declaration of a Job Staff PWA.
    ('Worker', ('jobs',), set(), ['/api/jobs'], ['/api/ui/job-feed', '/api/farm/tasks']),
])
def test_discovery_and_direct_api_role_scope_matrix(dashboard, role, domains, interfaces, allowed, denied):
    d = dashboard
    service = IdentityService(d.store)
    service.create_user(d.credentials['principal'], 'consumer', PASSWORD, role, domains)
    raw, _ = service.login('consumer', PASSWORD)
    code, headers, context = request(d, '/api/ui/context', raw=raw)
    assert code == 200 and headers['Cache-Control'] == 'no-store'
    assert set(context) == {'role', 'domains', 'pages', 'global_permissions', 'user_management', 'authority_note', 'interfaces'}
    assert context['role'] == role
    assert {(item['agent_id'], item['kind']) for item in context['interfaces']} == interfaces
    assert {item['id'] for item in context['domains']} == ({'jobs', 'farming'} if '*' in domains else set(domains))
    for item in context['interfaces']:
        assert set(item) == {'agent_id', 'kind', 'module', 'roles'}
        assert isinstance(item['roles'], list) and role in item['roles']
    for path in allowed:
        code, headers, _ = request(d, path, raw=raw)
        assert code == 200, path
        assert headers['Cache-Control'] == 'no-store'
    for path in denied:
        code, headers, body = request(d, path, raw=raw)
        assert code == 403, path
        assert headers['Cache-Control'] == 'no-store'
        assert set(body) == {'status', 'reason'}


def test_companion_http_schema_and_cross_domain_privacy(dashboard):
    d = dashboard
    d.store.add_notification('Visible Job notice', 'Review requested.', 'ACTION_REQUIRED', domain='jobs', related_page='actions')
    d.store.add_notification('FARM_PRIVATE_MARKER', 'FARM_PRIVATE_MARKER', 'URGENT', domain='farming')
    command = d.store.queue_command('RAW_COMMAND_MARKER')
    d.store.update_command(command, 'COMPLETED', 'RAW_RESULT_MARKER')
    code, headers, data = request(d, '/api/ui/job-feed', raw=d.credentials['raw'])
    assert code == 200 and headers['Cache-Control'] == 'no-store'
    assert set(data) == {'events', 'counts', 'job_counts', 'limit', 'privacy'}
    assert set(data['counts']) == {'unread', 'action_required', 'urgent'}
    assert set(data['job_counts']) == {'jobs', 'applications', 'actions'}
    assert 1 <= data['limit'] <= 50 and 1 <= len(data['events']) <= data['limit']
    for item in data['events']:
        assert set(item) == {'id', 'occurred_at', 'source', 'severity', 'title', 'summary', 'status', 'related_page', 'unread'}
        assert item['source'] in {'notification', 'command', 'application'}
        assert type(item['unread']) is bool
    serialized = json.dumps(data)
    assert all(marker not in serialized for marker in ('FARM_PRIVATE_MARKER', 'RAW_COMMAND_MARKER', 'RAW_RESULT_MARKER'))
    code, headers, body = request(d, '/api/ui/job-feed', 'HEAD', raw=d.credentials['raw'])
    assert code == 200 and body == b'' and headers['Cache-Control'] == 'no-store'

from dataclasses import replace
from application.exposure import for_store
from capabilities.contracts import Mode, Node
from tests.test_farm_tasks import create_task, event

SURFACES = ('/api/ui/context', '/api/ui/evidence/jobs', '/api/farm/tasks', '/api/ui/job-feed')

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


