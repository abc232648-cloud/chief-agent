import json
import pytest
import threading
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from test_dashboard_runtime import request
from application.composition import default_registry
from control.agents import AgentControls
from database.store import Store


def test_first_opening_domain_selection_unbiased_and_persisted(dashboard):
    d = dashboard
    # Initially domain_selection is None
    code, _, res = request(d, '/api/domains/selection')
    assert code == 200
    assert res.get('domain_selection') is None

    # Overview also exposes domain_selection
    code, _, overview = request(d, '/api/chief')
    assert code == 200
    assert overview.get('domain_selection') is None

    # Invalid selection returns a documented client error (400), not 500
    code, _, err = request(d, '/api/domains/selection', 'POST', {'selection': 'invalid'})
    assert code == 400, f'Expected 400 for invalid selection, got {code}'
    assert 'reason' in err

    # Choose Job only
    code, _, res = request(d, '/api/domains/selection', 'POST', {'selection': 'job_only'})
    assert code == 200
    assert res['status'] == 'CONFIGURED'
    assert res['domain_selection'] == 'job_only'

    # Verify persisted in control_state and reflected in overview
    code, _, res = request(d, '/api/domains/selection')
    assert code == 200
    assert res['domain_selection'] == 'job_only'

    code, _, overview = request(d, '/api/chief')
    assert overview['domain_selection'] == 'job_only'
    agents = {a['id']: a for a in overview['agents']}
    assert bool(agents['jobs']['enabled']) is True
    assert bool(agents['farming']['enabled']) is False

    # Choose Both
    code, _, res = request(d, '/api/domains/selection', 'POST', {'selection': 'both'})
    assert code == 200
    assert res['domain_selection'] == 'both'
    code, _, overview = request(d, '/api/chief')
    agents = {a['id']: a for a in overview['agents']}
    assert bool(agents['jobs']['enabled']) is True
    assert bool(agents['farming']['enabled']) is True


def test_streamlined_agent_control_running_toggle_requires_no_reauth(dashboard):
    from application.auth_routes import requirement
    d = dashboard

    # Toggling running: True is NOT sensitive
    perm, dom, res, sensitive = requirement('POST', '/api/agent-controls/jobs', {'running': True}, d.store)
    assert perm == 'controls.manage'
    assert sensitive is False

    # Toggling running: False is NOT sensitive
    perm, dom, res, sensitive = requirement('POST', '/api/agent-controls/jobs', {'running': False}, d.store)
    assert perm == 'safety.pause'
    assert sensitive is False

    # Modifying autostart IS sensitive
    perm, dom, res, sensitive = requirement('POST', '/api/agent-controls/jobs', {'autostart': False}, d.store)
    assert sensitive is True

    # Modifying enabled IS sensitive
    perm, dom, res, sensitive = requirement('POST', '/api/agent-controls/jobs', {'enabled': False}, d.store)
    assert sensitive is True


def test_mixed_field_emergency_authority_denied(dashboard):
    """Emergency pause authority must never grant enablement or management fields."""
    from application.auth_routes import requirement
    d = dashboard

    # Mixed request: running=False + enabled=True must NOT get safety.pause
    perm, dom, res, sensitive = requirement(
        'POST', '/api/agent-controls/jobs',
        {'running': False, 'enabled': True}, d.store,
    )
    assert perm == 'controls.manage', 'Mixed fields must require controls.manage, not safety.pause'
    assert sensitive is True, 'Mixed fields including enabled must be sensitive'

    # Mixed request: running=False + autostart=True
    perm, _, _, sensitive = requirement(
        'POST', '/api/agent-controls/jobs',
        {'running': False, 'autostart': True}, d.store,
    )
    assert perm == 'controls.manage'
    assert sensitive is True

    # Mixed request: running=False + enabled=True + autostart=True
    perm, _, _, sensitive = requirement(
        'POST', '/api/agent-controls/jobs',
        {'running': False, 'enabled': True, 'autostart': True}, d.store,
    )
    assert perm == 'controls.manage'
    assert sensitive is True

    # Unknown fields are rejected outright
    perm, _, _, _ = requirement(
        'POST', '/api/agent-controls/jobs',
        {'running': False, 'unknown_field': True}, d.store,
    )
    assert perm == 'UNSUPPORTED'

    # Non-boolean values are rejected
    perm, _, _, _ = requirement(
        'POST', '/api/agent-controls/jobs',
        {'running': 'false'}, d.store,
    )
    assert perm == 'UNSUPPORTED'


def test_idempotent_domain_selection_returns_unchanged(dashboard):
    """Repeating an identical completed selection is safely idempotent."""
    d = dashboard

    # First selection
    code, _, res = request(d, '/api/domains/selection', 'POST', {'selection': 'farm_only'})
    assert code == 200
    assert res['status'] == 'CONFIGURED'

    # Repeat the same selection — must return UNCHANGED without restarting agents
    code, _, res = request(d, '/api/domains/selection', 'POST', {'selection': 'farm_only'})
    assert code == 200
    assert res['status'] == 'UNCHANGED'
    assert res['domain_selection'] == 'farm_only'

    # Verify agent state was not altered by the repeat
    code, _, overview = request(d, '/api/chief')
    agents = {a['id']: a for a in overview['agents']}
    assert bool(agents['farming']['enabled']) is True
    assert bool(agents['jobs']['enabled']) is False


def test_paused_state_preserved_on_repeat_selection(dashboard):
    """Idempotent repeat must not silently restart paused agents."""
    d = dashboard

    # Configure both domains
    code, _, _ = request(d, '/api/domains/selection', 'POST', {'selection': 'both'})
    assert code == 200

    # Pause jobs agent
    code, _, _ = request(d, '/api/agent-controls/jobs', 'POST', {'running': False})
    assert code == 200

    # Verify jobs is paused
    code, _, overview = request(d, '/api/chief')
    jobs = next(a for a in overview['agents'] if a['id'] == 'jobs')
    assert bool(jobs['running']) is False

    # Repeat same 'both' selection — should be UNCHANGED
    code, _, res = request(d, '/api/domains/selection', 'POST', {'selection': 'both'})
    assert code == 200
    assert res['status'] == 'UNCHANGED'

    # Verify jobs is STILL paused (not silently restarted)
    code, _, overview = request(d, '/api/chief')
    jobs = next(a for a in overview['agents'] if a['id'] == 'jobs')
    assert bool(jobs['running']) is False, 'Idempotent repeat must not restart a paused agent'


def test_transaction_rollback_on_audit_failure(dashboard):
    """If audit insertion fails, the entire domain selection rolls back."""
    d = dashboard

    # Confirm initial state is None
    code, _, res = request(d, '/api/domains/selection')
    assert code == 200
    assert res['domain_selection'] is None

    # Inject a failure in add_audit
    original_add_audit = None
    import database.store_extensions as ext
    original_add_audit = ext.add_audit

    def broken_audit(store, category, action, **kwargs):
        if category == 'domains':
            raise RuntimeError('Injected audit failure')
        return original_add_audit(store, category, action, **kwargs)

    try:
        ext.add_audit = broken_audit
        # Also patch it in control.api since it imports at module level
        import control.domain_selection as api_mod
        api_mod.add_audit = broken_audit

        code, _, res = request(d, '/api/domains/selection', 'POST', {'selection': 'job_only'})
        assert code == 500, f'Expected 500 on audit failure, got {code}'
    finally:
        ext.add_audit = original_add_audit
        api_mod.add_audit = original_add_audit

    # Verify the domain selection was NOT partially applied
    code, _, res = request(d, '/api/domains/selection')
    assert code == 200
    assert res['domain_selection'] is None, 'Transaction must have rolled back; selection should remain None'


def test_concurrent_domain_selection_no_deadlock(dashboard):
    """Two parallel domain selection requests must not deadlock."""
    d = dashboard
    results = {}
    errors = {}

    def select(name, selection):
        try:
            code, _, res = request(d, '/api/domains/selection', 'POST', {'selection': selection})
            results[name] = (code, res)
        except Exception as exc:
            errors[name] = exc

    t1 = threading.Thread(target=select, args=('a', 'job_only'))
    t2 = threading.Thread(target=select, args=('b', 'job_only'))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive(), 'Thread a is still alive — probable deadlock'
    assert not t2.is_alive(), 'Thread b is still alive — probable deadlock'
    assert not errors, f'Unexpected errors: {errors}'

    # Both should succeed; one CONFIGURED, one UNCHANGED (order may vary)
    statuses = {results['a'][1]['status'], results['b'][1]['status']}
    assert 'CONFIGURED' in statuses, f'Expected at least one CONFIGURED, got {statuses}'
    # The other should be either CONFIGURED or UNCHANGED
    assert statuses == {'CONFIGURED', 'UNCHANGED'}, f'Unexpected statuses: {statuses}'
    assert all(code == 200 for code, _ in results.values())

    # Final state should be consistent
    code, _, res = request(d, '/api/domains/selection')
    assert code == 200
    assert res['domain_selection'] == 'job_only'


def test_merged_job_agent_layout_elements():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'dashboard.html').read_text(encoding='utf-8')

    # Job Agent unified elements
    assert 'id="jobOverview"' in html
    assert 'id="jobAgentControlsCard"' in html
    assert 'id="jobsCount"' in html
    assert 'id="apps"' in html
    assert 'id="actionsCount"' in html
    assert 'id="notifs"' in html
    assert 'id="workerStatus"' in html
    assert 'id="cmd"' in html
    assert 'id="commands"' in html
    assert 'id="jobWorkspaceButtons"' in html
    assert 'id="jobRecentActivity"' in html

    # Unbiased Domain Selection dialog
    assert 'id="domainSelectionDialog"' in html
    assert 'id="domainSelectionForm"' in html
    assert 'value="job_only"' in html
    assert 'value="farm_only"' in html
    assert 'value="both"' in html
    assert 'id="confirmDomainSelectionBtn"' in html

    # Verify no radio is pre-checked (unbiased onboarding requirement)
    assert 'checked' not in html[html.find('id="domainSelectionForm"'):html.find('</form>')]


def snapshot(store):
    with store._connect() as con:
        return {table: [tuple(r) for r in con.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                for table in ('agent_controls', 'control_state', 'audit_log', 'domain_records')}


@pytest.mark.parametrize('role,domains', [('Manager',['jobs']), ('Worker',['jobs']), ('Administrator',['jobs'])])
def test_http_domain_selection_denies_non_global_authority(dashboard, role, domains):
    from identity.service import IdentityService
    from tests.checkpoint_f_fixture import PASSWORD
    from tests.test_identity_http import request as http
    d=dashboard; service=IdentityService(d.store)
    service.create_user(d.credentials['principal'], 'limited', PASSWORD, role, domains)
    raw, _ = service.login('limited', PASSWORD)
    before=snapshot(d.store)
    assert http(d, '/api/domains/selection', 'POST', {'selection':'both'}, raw=raw)[0] == 403
    assert snapshot(d.store)==before


def test_http_emergency_pause_cannot_change_privileged_controls(dashboard):
    from identity.service import IdentityService
    from tests.checkpoint_f_fixture import PASSWORD
    from tests.test_identity_http import request as http
    d=dashboard; service=IdentityService(d.store);owner=d.credentials['principal']
    uid=service.create_user(owner, 'worker-pause', PASSWORD, 'Worker', ['jobs'])
    service.grant(owner, uid, 'jobs', emergency=True)
    raw, _=service.login('worker-pause', PASSWORD)
    before=snapshot(d.store)
    for body in ({'running':False,'enabled':True}, {'running':False,'autostart':True}, {'running':True}):
        assert http(d,'/api/agent-controls/jobs','POST',body,raw=raw)[0]==403
    assert snapshot(d.store)==before
    assert http(d,'/api/agent-controls/farming','POST',{'running':False},raw=raw)[0]==403
    assert http(d,'/api/agent-controls/jobs','POST',{'running':False},raw=raw)[0]==200


def test_initial_selection_then_sensitive_change_and_routine_controls(dashboard):
    from datetime import timedelta
    from operations.time_integrity import utc_now, utc_text
    from tests.test_identity_http import request as http
    d=dashboard;raw=d.credentials['raw']
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET reauth_at=? WHERE id=?',
                    (utc_text(utc_now()-timedelta(minutes=6)), d.credentials['principal'].session_id))
    assert http(d,'/api/domains/selection','POST',{'selection':'both'},raw=raw)[0]==200
    assert http(d,'/api/agent-controls/jobs','POST',{'running':False},raw=raw)[0]==200
    before=snapshot(d.store)
    assert http(d,'/api/domains/selection','POST',{'selection':'both'},raw=raw)[0]==200
    assert http(d,'/api/domains/selection','POST',{'selection':'farm_only'},raw=raw)[0]==428
    assert http(d,'/api/agent-controls/jobs','POST',{'enabled':False},raw=raw)[0]==428
    assert snapshot(d.store)==before
    assert http(d,'/api/agent-controls/jobs','POST',{'running':True},raw=raw)[0]==200


@pytest.mark.parametrize('body',[{}, {'selection':'both','enabled':True}, {'selection':None}, {'selection':['both']}])
def test_selection_invalid_payload_is_side_effect_free(dashboard, body):
    before=snapshot(dashboard.store)
    assert request(dashboard,'/api/domains/selection','POST',body)[0]==400
    assert snapshot(dashboard.store)==before


def test_selection_http_origin_csrf_and_identity(dashboard):
    from tests.test_identity_http import request as http
    d=dashboard;body={'selection':'both'};before=snapshot(d.store)
    assert http(d,'/api/domains/selection','POST',body)[0]==401
    assert http(d,'/api/domains/selection','POST',body,raw=d.credentials['raw'],csrf=False)[0]==403
    assert http(d,'/api/domains/selection','POST',body,raw=d.credentials['raw'],extra={'Origin':'https://untrusted.invalid'})[0]==403
    assert snapshot(d.store)==before
    assert http(d,'/api/domains/selection','POST',body,raw=d.credentials['raw'])[0]==200
    with d.store._connect() as con:
        row=con.execute("SELECT actor,data_json FROM audit_log WHERE category='domains' ORDER BY id DESC LIMIT 1").fetchone()
    assert row['actor']==d.credentials['principal'].id
    assert json.loads(row['data_json'])['human_session_id']=='[REDACTED]'
    assert json.loads(row['data_json'])['human_id']==d.credentials['principal'].id


def test_late_history_failure_rolls_back_every_write(dashboard, monkeypatch):
    from control import domain_selection
    before=snapshot(dashboard.store)
    original=domain_selection.json.dumps
    def fail_history(*args,**kwargs):raise RuntimeError('synthetic history failure')
    # Patch only this module's serializer, not the shared json module used by audit.
    from types import SimpleNamespace
    monkeypatch.setattr(domain_selection,'json',SimpleNamespace(dumps=fail_history))
    assert request(dashboard,'/api/domains/selection','POST',{'selection':'job_only'})[0]==500
    assert snapshot(dashboard.store)==before


def test_farm_unconfigured_is_not_reported_ready(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    monkeypatch.setenv('CHIEF_SERVICE_CONFIGURED','1')
    monkeypatch.setenv('CHIEF_FARM_PRODUCTION','DISABLED')
    from application.ui_api import overview
    data=overview(dashboard.store, {'domains':[{'id':'farming'}], 'global_permissions':{'audit.read':True}})
    farm=next(a for a in data['agents'] if a['id']=='farming')
    assert farm['status']=='SETUP_REQUIRED'
    assert 'installation configuration' in farm['setup_message']
