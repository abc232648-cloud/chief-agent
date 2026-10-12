from datetime import timedelta
import pytest
from notifications.web_push import register_subscription, dispatch_pending
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from tests.test_web_push import SUBSCRIPTION,ENV
from tests.test_identity_http import request
from tests.checkpoint_f_fixture import PASSWORD


def register(d,consent=True):
    p=d.credentials['principal']
    register_subscription(d.store,p.id,{**SUBSCRIPTION,'after_timeout':consent},ENV,session_id=p.session_id)
    d.store.add_notification('Private employer','Private application details','URGENT',domain='jobs')


def expire(d):
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET last_seen=?,expires_at=? WHERE id=?',(utc_text(utc_now()-timedelta(hours=9)),utc_text(utc_now()-timedelta(hours=1)),d.credentials['principal'].session_id))


def deliver(d):
    sent=[]
    result=dispatch_pending(d.store,sender=lambda sub,payload,settings:sent.append(payload),env=ENV)
    return result,sent


def test_opted_in_timeout_delivers_generic_without_restoring_session(dashboard):
    d=dashboard;register(d);expire(d)
    assert request(d,'/api/auth/session',raw=d.credentials['raw'])[0]==401
    result,sent=deliver(d);assert result['sent']==1
    assert sent[0]['title']=='Chief update' and sent[0]['body']=='Sign in to view your update.'
    assert 'Private' not in str(sent) and sent[0]['related_page']==''
    assert request(d,'/api/auth/session',raw=d.credentials['raw'])[0]==401


def test_old_or_declined_consent_does_not_survive_timeout(dashboard):
    d=dashboard;register(d,False);expire(d);assert deliver(d)[0]['sent']==0


def test_reconnect_preserves_pending_alert_and_device_specific_consent(dashboard):
    d=dashboard; p=d.credentials['principal']; register(d)
    other={**SUBSCRIPTION,'endpoint':SUBSCRIPTION['endpoint']+'-second'}
    result=register_subscription(d.store,p.id,other,ENV,session_id=p.session_id)
    assert result['after_timeout'] is False
    result=register_subscription(d.store,p.id,SUBSCRIPTION,ENV,session_id=p.session_id)
    assert result['after_timeout'] is True
    result,sent=deliver(d)
    assert result['sent']==1 and len(sent)==1


@pytest.mark.parametrize('change',['logout','scope','role','disabled','unsubscribe','module'])
def test_consent_revocation(dashboard,change):
    d=dashboard;p=d.credentials['principal'];register(d)
    if change=='logout':
        expire(d)
        assert request(d,'/api/auth/logout','POST',{},raw=d.credentials['raw'],csrf=False)[0]==403
        assert request(d,'/api/auth/logout','POST',{},raw=d.credentials['raw'])[0]==200
    elif change=='unsubscribe':
        from control.notifications import remove_push_subscription
        remove_push_subscription(d.store,p.id,SUBSCRIPTION['endpoint'])
    elif change=='module':
        from application.exposure import for_store
        from capabilities.contracts import Node,Mode
        gate=for_store(d.store);controls=gate.lifecycle.controls;node=Node('component',gate.lifecycle.manifests.runtime_agent_id('jobs'))
        controls.transition(controls.preview(node,Mode.DISABLED),actor='fixture',reason='test',confirmed=True)
    else:
        with d.store._connect() as con:
            if change=='scope':con.execute('UPDATE human_identities SET domains=? WHERE id=?',('["farming"]',p.id))
            elif change=='role':con.execute("UPDATE human_identities SET role='Worker' WHERE id=?",(p.id,))
            else:con.execute('UPDATE human_identities SET enabled=0 WHERE id=?',(p.id,))
    assert deliver(d)[0]['sent']==0


def test_browser_account_switch_revokes_previous_device_binding(dashboard):
    d=dashboard;p=d.credentials['principal'];register(d);expire(d)
    # A fresh Owner session provisions the synthetic second account.
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET last_seen=?,expires_at=? WHERE id=?',(utc_text(utc_now()),utc_text(utc_now()+timedelta(hours=1)),p.session_id))
    IdentityService(d.store).create_user(p,'other-owner',PASSWORD,'Owner',('jobs',))
    expire(d)
    assert request(d,'/api/auth/login','POST',{'username':'other-owner','password':PASSWORD},raw=d.credentials['raw'])[0]==200
    assert deliver(d)[0]['sent']==0
