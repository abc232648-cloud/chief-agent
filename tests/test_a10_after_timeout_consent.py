"""A10 boundary: device notification consent is not an authenticated Chief session."""
from datetime import timedelta

import pytest

from application.exposure import for_store
from capabilities.contracts import Mode,Node
from identity.contracts import AuthenticationRequired
from identity.service import IdentityService
from notifications.web_push import dispatch_pending,register_subscription
from operations.time_integrity import utc_now,utc_text
from tests.test_web_push import ENV,SUBSCRIPTION


def expire_interactive_session(store,principal):
    expired=utc_text(utc_now()-timedelta(minutes=11))
    with store._connect() as con:
        con.execute('UPDATE human_sessions SET last_seen=? WHERE id=?',(expired,principal.session_id))
    return expired


def register_after_timeout(store,principal):
    payload={**SUBSCRIPTION,'after_timeout':True}
    result=register_subscription(store,principal.id,payload,ENV,session_id=principal.session_id)
    assert result['after_timeout'] is True


def test_explicit_after_timeout_consent_stays_generic_without_renewing_auth(dashboard):
    store=dashboard.store
    principal=dashboard.credentials['principal']
    register_after_timeout(store,principal)
    expired=expire_interactive_session(store,principal)

    store.add_notification('PRIVATE TITLE','PRIVATE BODY','ACTION_REQUIRED',domain='jobs')
    sent=[]
    result=dispatch_pending(store,sender=lambda subscription,push,settings:sent.append(push),env=ENV)
    assert result['sent']==1 and len(sent)==1
    assert sent[0]['title']=='Chief update'
    assert sent[0]['body']=='Sign in to view your update.'
    assert 'PRIVATE TITLE' not in str(sent[0]) and 'PRIVATE BODY' not in str(sent[0])

    # Delivery consent cannot renew or recreate interactive authentication.
    with store._connect() as con:
        assert con.execute('SELECT last_seen FROM human_sessions WHERE id=?',(principal.session_id,)).fetchone()[0]==expired
    with pytest.raises(AuthenticationRequired):
        IdentityService(store).refresh(principal)

    # The holder can still revoke the expired session. Revocation terminates the
    # separate device-consent delivery path before the next external send.
    IdentityService(store).revoke_token(dashboard.credentials['raw'])
    store.add_notification('Second private title','Second private body','URGENT',domain='jobs')
    second=[]
    result=dispatch_pending(store,sender=lambda *args:second.append(args),env=ENV)
    assert result['sent']==0 and second==[]


@pytest.mark.parametrize('change',['revoked','disabled','role','scope','module'])
def test_after_timeout_consent_rechecks_current_authority_before_send(dashboard,change):
    store=dashboard.store
    principal=dashboard.credentials['principal']
    register_after_timeout(store,principal)
    expire_interactive_session(store,principal)

    if change=='revoked':
        IdentityService(store).revoke_token(dashboard.credentials['raw'])
    elif change=='module':
        exposure=for_store(store)
        controls=exposure.lifecycle.controls
        node=Node('component',exposure.lifecycle.manifests.runtime_agent_id('jobs'))
        controls.transition(controls.preview(node,Mode.DISABLED),actor='a10-fixture',reason='Synthetic acceptance',confirmed=True)
    else:
        with store._connect() as con:
            if change=='disabled':
                con.execute('UPDATE human_identities SET enabled=0 WHERE id=?',(principal.id,))
            elif change=='role':
                con.execute("UPDATE human_identities SET role='Worker' WHERE id=?",(principal.id,))
            else:
                con.execute('UPDATE human_identities SET domains=? WHERE id=?',('["farming"]',principal.id))

    store.add_notification('Private decision','Private text','ACTION_REQUIRED',domain='jobs')
    sent=[]
    result=dispatch_pending(store,sender=lambda *args:sent.append(args),env=ENV)
    assert result['sent']==0 and sent==[]
