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
