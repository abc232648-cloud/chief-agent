from types import SimpleNamespace

import pytest

from control.notifications import preferences,push_subscriptions
from database.store import Store
from notifications.web_push import (
    dispatch_pending,
    normalize_subscription,
    register_subscription,
    unregister_subscription,
)

ENDPOINT='https://web.push.apple.com/Q-chief-test'
SUBSCRIPTION={
    'endpoint':ENDPOINT,
    'expirationTime':None,
    'keys':{'p256dh':'A'*87,'auth':'B'*22},
}
ENV={
    'CHIEF_INSTANCE_MODE':'preview',
    'WEB_PUSH_VAPID_PUBLIC_KEY':'C'*87,
    'WEB_PUSH_VAPID_PRIVATE_KEY':'development-private-key',
    'WEB_PUSH_VAPID_SUBJECT':'mailto:owner@example.invalid',
}


def store(tmp_path):
    return Store(tmp_path/'worker.db')


def test_subscription_validation_rejects_ssrf_and_unknown_push_hosts():
    for endpoint in ('https://127.0.0.1/push','https://localhost/push','https://evil.example/push','http://web.push.apple.com/push'):
        with pytest.raises(ValueError):
            normalize_subscription({**SUBSCRIPTION,'endpoint':endpoint},ENV)


def test_registration_starts_at_current_attention_cursor_and_preferences_stay_private(tmp_path):
    state=store(tmp_path)
    state.add_notification('Old ASK','Do not replay this','ACTION_REQUIRED',domain='jobs')
    old_id=state.notifications(1)[0]['id']

    result=register_subscription(state,'owner-1',SUBSCRIPTION,ENV)
    assert result=={'status':'REGISTERED','subscription_count':1}
    private=push_subscriptions(state,'owner-1')
    assert private[0]['endpoint']==ENDPOINT
    assert private[0]['cursor']==old_id

    public=preferences(state)
    serialized=str(public)
    assert ENDPOINT not in serialized
    assert SUBSCRIPTION['keys']['p256dh'] not in serialized
    assert SUBSCRIPTION['keys']['auth'] not in serialized


def test_dispatch_sends_only_new_job_attention_and_advances_durable_cursor(tmp_path):
    state=store(tmp_path)
    register_subscription(state,'owner-1',SUBSCRIPTION,ENV)
    state.add_notification('Quiet telemetry','No push','INFO',domain='jobs')
    state.add_notification('Other domain','No push','URGENT',domain='farming')
    state.add_notification('Approval required','Review this application','ACTION_REQUIRED',domain='jobs')
    state.add_notification('STOP','Identity document requested','URGENT',domain='jobs')

    delivered=[]
    def sender(subscription,payload,settings):
        delivered.append((subscription['endpoint'],payload,settings['subject']))

    first=dispatch_pending(state,sender=sender,env=ENV)
    assert first=={'status':'DELIVERED','sent':2,'failed':0,'expired':0}
    assert [item[1]['severity'] for item in delivered]==['ACTION_REQUIRED','URGENT']
    assert all(item[1]['url']=='/jobs/' for item in delivered)
    assert delivered[-1][1]['tag'].startswith('chief-job-')

    cursor=push_subscriptions(state,'owner-1')[0]['cursor']
    assert cursor==state.notifications(1)[0]['id']
    assert dispatch_pending(state,sender=sender,env=ENV)=={'status':'IDLE','sent':0,'failed':0,'expired':0}
    assert len(delivered)==2


def test_transient_provider_failure_keeps_cursor_for_retry(tmp_path):
    state=store(tmp_path)
    register_subscription(state,'owner-1',SUBSCRIPTION,ENV)
    state.add_notification('Approval required','Retry me','ACTION_REQUIRED',domain='jobs')
    before=push_subscriptions(state,'owner-1')[0]['cursor']

    class TemporaryFailure(RuntimeError):
        response=SimpleNamespace(status_code=503)

    def fail(*_):raise TemporaryFailure()
    result=dispatch_pending(state,sender=fail,env=ENV)
    assert result=={'status':'IDLE','sent':0,'failed':1,'expired':0}
    assert push_subscriptions(state,'owner-1')[0]['cursor']==before

    delivered=[]
    dispatch_pending(state,sender=lambda *args:delivered.append(args),env=ENV)
    assert len(delivered)==1
    assert push_subscriptions(state,'owner-1')[0]['cursor']>before


def test_expired_provider_subscription_is_removed(tmp_path):
    state=store(tmp_path)
    register_subscription(state,'owner-1',SUBSCRIPTION,ENV)
    state.add_notification('STOP','Expired endpoint test','URGENT',domain='jobs')

    class Gone(RuntimeError):
        response=SimpleNamespace(status_code=410)

    result=dispatch_pending(state,sender=lambda *_:(_ for _ in ()).throw(Gone()),env=ENV)
    assert result=={'status':'IDLE','sent':0,'failed':0,'expired':1}
    assert push_subscriptions(state,'owner-1')==[]


def test_unsubscribe_is_identity_scoped(tmp_path):
    state=store(tmp_path)
    register_subscription(state,'owner-1',SUBSCRIPTION,ENV)
    assert unregister_subscription(state,'other-user',ENDPOINT,ENV)=={'status':'NOT_REGISTERED'}
    assert push_subscriptions(state,'owner-1')
    assert unregister_subscription(state,'owner-1',ENDPOINT,ENV)=={'status':'REMOVED'}
    assert push_subscriptions(state,'owner-1')==[]
