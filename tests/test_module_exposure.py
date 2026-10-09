from dataclasses import replace

import pytest

from application.exposure import for_store
from capabilities.contracts import Mode, Node
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


def exposure(d):
    return for_store(d.store)


def owner(d):
    return IdentityService(d.store), d.credentials['principal']


def test_exposes_only_declared_authorized_interfaces(dashboard):
    d = dashboard
    service, principal = owner(d)
    result = exposure(d).describe(service, principal)
    assert {(r['agent_id'], r['kind']) for r in result} == {
        ('jobs', 'owner'), ('jobs', 'companion'), ('farming', 'owner')}
    service.create_user(principal, 'worker-a9', PASSWORD, 'Worker', ('farming',))
    raw, worker = service.login('worker-a9', PASSWORD)
    assert [(r['agent_id'], r['kind']) for r in exposure(d).describe(service, worker)] == [('farming', 'staff')]
    assert request(d, '/api/ui/job-feed', raw=raw)[0] == 403
    service.revoke(worker)
    assert exposure(d).describe(service, worker) == []


@pytest.mark.parametrize('mode', [Mode.DISABLED, Mode.MAINTENANCE])
@pytest.mark.parametrize('domain,paths', [
    ('jobs', ['/api/jobs', '/api/domains/jobs', '/api/ui/job-feed', '/api/ui/job-state']),
    ('farming', ['/api/farm/journal', '/api/farm/setup', '/api/domains/farming']),
])
def test_disable_hides_discovery_and_blocks_direct_routes(dashboard, mode, domain, paths):
    d = dashboard
    gate = exposure(d)
    controls = gate.lifecycle.controls
    node = Node('component', gate.lifecycle.manifests.runtime_agent_id(domain))
    preview = controls.preview(node, mode)
    controls.transition(preview, actor='a9-test', reason='Exposure regression', confirmed=True)
    for path in paths:
        assert request(d, path, raw=d.credentials['raw'])[0] == 403
    context = request(d, '/api/ui/context', raw=d.credentials['raw'])[2]
    assert domain not in [row['id'] for row in context['domains']]
    assert domain not in [row['agent_id'] for row in context['interfaces']]
    assert domain not in [row['id'] for row in request(d, '/api/domains', raw=d.credentials['raw'])[2]]
    # Existing management authority is still reachable for recovery.
    assert request(d, '/api/component-controls', raw=d.credentials['raw'])[0] == 200
    controls.transition(controls.preview(node, Mode.ENABLED), actor='a9-test', reason='Restore exposure', confirmed=True)
    assert request(d, paths[0], raw=d.credentials['raw'])[0] == 200


@pytest.mark.parametrize('failure', ['missing', 'runtime', 'replacement', 'interface', 'interface-identity', 'incompatible', 'legacy-disabled'])
def test_stale_missing_incompatible_and_disabled_fail_closed(dashboard, failure):
    d = dashboard
    gate = exposure(d)
    manifests = gate.lifecycle.manifests
    if failure == 'missing':
        del manifests._manifests['jobs']
    elif failure == 'runtime':
        del manifests.runtime.agents[manifests.runtime_agent_id('jobs')]
    elif failure == 'replacement':
        manifests._manifests['jobs'] = replace(manifests.get('jobs'))
    elif failure == 'interface':
        key = ('jobs', 'companion')
        gate.interfaces._entries[key] = replace(gate.interfaces._entries[key], module='forged')
    elif failure == 'interface-identity':
        key = ('jobs', 'owner')
        gate.interfaces._entries[key] = replace(gate.interfaces._entries[key], agent_id='farming')
        result = gate.describe(*owner(d))
        assert not any(row['kind'] == 'owner' and row['module'] == 'jobs' for row in result)
        assert not gate.available('jobs', *owner(d), kind='owner')
        return
    elif failure == 'incompatible':
        gate.lifecycle.compatibility.profile = replace(gate.lifecycle.compatibility.profile, companion_api='2')
    else:
        with d.store._connect() as con:
            con.execute("UPDATE agent_controls SET enabled=0 WHERE domain='jobs'")
    assert request(d, '/api/ui/job-feed', raw=d.credentials['raw'])[0] == 403
    assert not gate.available('jobs', *owner(d), kind='companion')


def test_browser_fields_cannot_create_exposure(dashboard):
    d = dashboard
    gate = exposure(d)
    gate.lifecycle.compatibility.profile = replace(gate.lifecycle.compatibility.profile, companion_api='2')
    assert request(d, '/api/ui/job-feed?enabled=true&compatible=true&role=Owner', raw=d.credentials['raw'])[0] == 403
    assert request(d, '/api/command', 'POST', {'command': 'test', 'enabled': True, 'compatible': True}, d.credentials['raw'])[0] == 403
    assert request(d, '/api/domains/missing', raw=d.credentials['raw'])[0] == 403
