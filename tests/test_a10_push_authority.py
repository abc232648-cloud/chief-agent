"""Real identity/lifecycle authority; transport is an explicit no-network spy."""
import pytest
from identity.service import IdentityService
from identity.context import human_context
from application.exposure import for_store
from capabilities.contracts import Node, Mode
from notifications.web_push import register_subscription, dispatch_pending
from tests.test_web_push import SUBSCRIPTION, ENV


@pytest.mark.parametrize('change', ['logout', 'disabled', 'role', 'scope', 'module'])
def test_push_rechecks_authority_before_external_delivery(dashboard, change):
    d = dashboard
    principal = d.credentials['principal']
    with human_context(principal):
        register_subscription(d.store, principal.id, SUBSCRIPTION, ENV)
    d.store.add_notification('Synthetic ASK', 'Synthetic private text', 'ACTION_REQUIRED', domain='jobs')
    if change == 'logout':
        IdentityService(d.store).revoke(principal)
    elif change == 'module':
        gate = for_store(d.store)
        controls = gate.lifecycle.controls
        node = Node('component', gate.lifecycle.manifests.runtime_agent_id('jobs'))
        controls.transition(controls.preview(node, Mode.DISABLED), actor='fixture', reason='Test', confirmed=True)
    else:
        with d.store._connect() as con:
            if change == 'disabled':
                con.execute('UPDATE human_identities SET enabled=0 WHERE id=?', (principal.id,))
            elif change == 'role':
                con.execute("UPDATE human_identities SET role='Worker' WHERE id=?", (principal.id,))
            else:
                con.execute('UPDATE human_identities SET domains=? WHERE id=?', ('["farming"]', principal.id))
    sent = []
    result = dispatch_pending(d.store, sender=lambda *args: sent.append(args), env=ENV)
    assert len(sent) == 0
    assert result['sent'] == 0


@pytest.mark.parametrize('binding', ['absent', 'unavailable'])
def test_push_fails_closed_without_composed_authority(dashboard, binding):
    d = dashboard
    principal = d.credentials['principal']
    with human_context(principal):
        register_subscription(d.store, principal.id, SUBSCRIPTION, ENV)
    d.store.add_notification('Synthetic ASK', 'Synthetic text', 'ACTION_REQUIRED', domain='jobs')
    from control.notifications import push_subscriptions
    before = push_subscriptions(d.store)
    if binding == 'absent':
        del d.store._module_exposure
    else:
        d.store._module_exposure = None
    with human_context(principal), pytest.raises(PermissionError, match='Module exposure is unavailable'):
        register_subscription(d.store, principal.id, SUBSCRIPTION, ENV)
    sent = []
    assert dispatch_pending(d.store, sender=lambda *args: sent.append(args), env=ENV)['sent'] == 0
    assert sent == []
    assert push_subscriptions(d.store) == before


def test_push_rechecks_revocation_between_deliveries(dashboard):
    d = dashboard
    principal = d.credentials['principal']
    with human_context(principal):
        register_subscription(d.store, principal.id, SUBSCRIPTION, ENV)
    for number in range(2):
        d.store.add_notification('Synthetic ASK', str(number), 'ACTION_REQUIRED', domain='jobs')
    sent = []
    def sender(*args):
        sent.append(args)
        IdentityService(d.store).revoke(principal)
    result = dispatch_pending(d.store, sender=sender, env=ENV)
    assert result['sent'] == 1
    assert len(sent) == 1
