"""Standards-based Web Push delivery for Job Agent ASK/STOP notifications.

Subscription capability URLs and browser keys remain in Chief's private state. The
public PWA API exposes only configuration/readiness and registration counts.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

from control.notifications import (
    advance_push_cursor,
    push_subscriptions,
    remove_push_endpoint,
    remove_push_subscription,
    upsert_push_subscription,
)
from operations.time_integrity import utc_now, utc_text

ATTENTION_SEVERITIES=('ACTION_REQUIRED','URGENT')
PUSH_TIMEOUT_SECONDS=3
MAX_DELIVERY_ATTEMPTS=8
PER_SUBSCRIPTION_BATCH=4
_ALLOWED_PUSH_HOSTS={
    'fcm.googleapis.com',
    'push.services.mozilla.com',
    'updates.push.services.mozilla.com',
    'web.push.apple.com',
}
_KEY_RE=re.compile(r'^[A-Za-z0-9_-]{16,512}={0,2}$')


def _environment(env=None):
    return dict(os.environ if env is None else env)


def _allowed_host(host,env):
    host=host.lower().rstrip('.')
    configured={item.strip().lower().rstrip('.') for item in env.get('WEB_PUSH_ALLOWED_HOSTS','').split(',') if item.strip()}
    return host in _ALLOWED_PUSH_HOSTS or host in configured or host.endswith('.notify.windows.com')


def validate_endpoint(endpoint,env=None):
    if not isinstance(endpoint,str) or not 1<=len(endpoint)<=2048:
        raise ValueError('Invalid Web Push endpoint.')
    parsed=urlparse(endpoint)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Invalid Web Push endpoint.')
    try:port=parsed.port
    except ValueError:raise ValueError('Invalid Web Push endpoint.') from None
    if port not in (None,443) or not _allowed_host(parsed.hostname,_environment(env)):
        raise ValueError('Unsupported Web Push provider endpoint.')
    return endpoint


def _browser_key(value,label):
    if not isinstance(value,str) or not _KEY_RE.fullmatch(value):
        raise ValueError(f'Invalid Web Push {label} key.')
    return value


def normalize_subscription(payload,env=None):
    if not isinstance(payload,dict) or set(payload)-{'endpoint','expirationTime','keys'}:
        raise ValueError('Invalid Web Push subscription.')
    keys=payload.get('keys')
    if not isinstance(keys,dict) or set(keys)!={'p256dh','auth'}:
        raise ValueError('Invalid Web Push subscription keys.')
    endpoint=validate_endpoint(payload.get('endpoint'),env)
    return {
        'endpoint':endpoint,
        'keys':{
            'p256dh':_browser_key(keys.get('p256dh'),'p256dh'),
            'auth':_browser_key(keys.get('auth'),'auth'),
        },
    }


def public_configuration(env=None):
    e=_environment(env)
    public=str(e.get('WEB_PUSH_VAPID_PUBLIC_KEY','')).strip()
    subject=str(e.get('WEB_PUSH_VAPID_SUBJECT','')).strip()
    production=str(e.get('CHIEF_INSTANCE_MODE',os.environ.get('CHIEF_INSTANCE_MODE',''))).lower()=='production'
    private_ready=bool(e.get('WEB_PUSH_VAPID_PRIVATE_KEY_REF') or (not production and e.get('WEB_PUSH_VAPID_PRIVATE_KEY')))
    public_valid=bool(public and _KEY_RE.fullmatch(public))
    subject_valid=subject.startswith('mailto:') or subject.startswith('https://')
    return {
        'configured':bool(public_valid and subject_valid and private_ready),
        'public_key':public if public_valid and subject_valid and private_ready else '',
    }


def _private_settings(env=None):
    e=_environment(env);public=public_configuration(e)
    if not public['configured']:return None
    from private_secrets.service import resolve_configured
    try:
        private=resolve_configured(e,'WEB_PUSH_VAPID_PRIVATE_KEY',consumer='jobs.web-push',domain='jobs')
    except Exception:
        return None
    if not private:return None
    return {'public_key':public['public_key'],'private_key':private,'subject':str(e['WEB_PUSH_VAPID_SUBJECT']).strip()}


def latest_attention_id(store):
    from control.notifications import initialize
    initialize(store)
    with store._connect() as con:
        row=con.execute("SELECT COALESCE(MAX(id),0) FROM notifications WHERE domain='jobs' AND severity IN ('ACTION_REQUIRED','URGENT')").fetchone()
    return int(row[0])


def register_subscription(store,principal_id,payload,env=None):
    if not isinstance(principal_id,str) or not principal_id:raise ValueError('Authenticated identity is required.')
    if not public_configuration(env)['configured']:raise ValueError('Background Web Push is not configured on Chief.')
    subscription=normalize_subscription(payload,env)
    now=utc_text(utc_now())
    count=upsert_push_subscription(store,{
        'principal_id':principal_id,
        'endpoint':subscription['endpoint'],
        'keys':subscription['keys'],
        'cursor':latest_attention_id(store),
        'created_at':now,
        'updated_at':now,
    })
    return {'status':'REGISTERED','subscription_count':count}


def unregister_subscription(store,principal_id,endpoint,env=None):
    endpoint=validate_endpoint(endpoint,env)
    removed=remove_push_subscription(store,principal_id,endpoint)
    return {'status':'REMOVED' if removed else 'NOT_REGISTERED'}


def subscription_status(store,principal_id,env=None):
    config=public_configuration(env)
    rows=push_subscriptions(store,principal_id)
    return {**config,'subscribed':bool(rows),'subscription_count':len(rows)}


def _default_sender(subscription,payload,settings):
    from pywebpush import webpush
    return webpush(
        subscription_info={'endpoint':subscription['endpoint'],'keys':subscription['keys']},
        data=json.dumps(payload,separators=(',',':'),ensure_ascii=False),
        vapid_private_key=settings['private_key'],
        vapid_claims={'sub':settings['subject']},
        ttl=300,
        timeout=PUSH_TIMEOUT_SECONDS,
    )


def _pending_for(store,cursor,limit=PER_SUBSCRIPTION_BATCH):
    from control.notifications import initialize
    initialize(store)
    with store._connect() as con:
        return [dict(row) for row in con.execute(
            "SELECT id,title,body,severity,related_page FROM notifications WHERE id>? AND domain='jobs' AND severity IN ('ACTION_REQUIRED','URGENT') ORDER BY id ASC LIMIT ?",
            (int(cursor),int(limit)),
        )]


def dispatch_pending(store,*,sender=None,env=None,limit_per_subscription=PER_SUBSCRIPTION_BATCH,max_attempts=MAX_DELIVERY_ATTEMPTS):
    """Retry durable Job PWA attention notifications with bounded at-least-once delivery.

    The subscription cursor advances only after a successful provider send. Stable
    notification tags let browsers collapse the rare duplicate caused by a process
    crash after provider acceptance but before cursor persistence. A bounded attempt
    budget keeps a degraded push provider from monopolizing Chief's notification loop.
    """
    settings=_private_settings(env)
    if not settings:return {'status':'NOT_CONFIGURED','sent':0,'failed':0,'expired':0}
    send=sender or _default_sender
    try:
        per_subscription=max(1,min(int(limit_per_subscription),20))
        budget=max(1,min(int(max_attempts),50))
    except (TypeError,ValueError):
        raise ValueError('Invalid Web Push delivery limits.') from None
    sent=failed=expired=attempted=0
    for subscription in push_subscriptions(store):
        if attempted>=budget:break
        endpoint=subscription.get('endpoint','');principal=subscription.get('principal_id','')
        if not endpoint or not principal:continue
        remaining=min(per_subscription,budget-attempted)
        for item in _pending_for(store,subscription.get('cursor',0),remaining):
            if attempted>=budget:break
            attempted+=1
            payload={
                'title':str(item['title'])[:160],
                'body':str(item['body'])[:500],
                'severity':item['severity'],
                'tag':f"chief-job-{item['id']}",
                'url':'/jobs/',
                'related_page':str(item.get('related_page') or '')[:120],
            }
            try:
                send(subscription,payload,settings)
            except Exception as exc:
                status=getattr(getattr(exc,'response',None),'status_code',None)
                if status in {404,410}:
                    remove_push_endpoint(store,endpoint);expired+=1
                else:failed+=1
                break
            advance_push_cursor(store,principal,endpoint,item['id']);subscription['cursor']=item['id'];sent+=1
    return {'status':'DELIVERED' if sent else 'IDLE','sent':sent,'failed':failed,'expired':expired}
