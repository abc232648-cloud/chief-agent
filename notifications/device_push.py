"""Owner/Staff device delivery: durable consent, generic text, no session grants."""
import os
from control.notifications import push_subscriptions, upsert_push_subscription, remove_push_subscription, remove_push_endpoint, advance_push_cursor
from notifications.bus import NotificationBus, EVENT_RECORD_KIND
from notifications import web_push
from operations.time_integrity import utc_now, utc_text


def configuration(env=None):
    e = dict(os.environ if env is None else env)
    mapped = {**e, **{key: e.get('CHIEF_' + key, '') for key in ('WEB_PUSH_VAPID_PUBLIC_KEY', 'WEB_PUSH_VAPID_PRIVATE_KEY', 'WEB_PUSH_VAPID_PRIVATE_KEY_REF', 'WEB_PUSH_VAPID_SUBJECT')}}
    return web_push.public_configuration(mapped)


def settings(env=None):
    e = dict(os.environ if env is None else env)
    public = configuration(e)
    if not public['configured']: return None
    from private_secrets.service import resolve_configured
    try:
        private = resolve_configured(e, 'CHIEF_WEB_PUSH_VAPID_PRIVATE_KEY', consumer='chief.device-push', domain='system')
    except Exception:
        return None
    if not private: return None
    return {**public, 'private_key': private, 'subject': e['CHIEF_WEB_PUSH_VAPID_SUBJECT']}


def access(store, human_id, session_id, surface, expired=False):
    resolver = getattr(store, '_device_notification_context', None)
    if resolver is None: raise PermissionError('Notification authority unavailable.')
    return resolver(human_id, session_id, surface, expired=expired)


def register(store, principal, surface, payload, env=None):
    ctx, stamp = access(store, principal.id, principal.session_id, surface)
    if not isinstance(payload, dict): raise ValueError('Invalid notification subscription.')
    p = dict(payload); consent = p.pop('after_timeout', None)
    if consent is not None and type(consent) is not bool: raise ValueError('Explicit notification consent required.')
    subscription = web_push.normalize_subscription(p, env)
    if not configuration(env)['configured']: raise ValueError('Device notification delivery is not configured on this installation.')
    previous = next((r for r in push_subscriptions(store, principal.id) if r.get('endpoint') == subscription['endpoint'] and r.get('surface') == surface), {})
    if consent is None:
        consent = False
        try:
            _, old_stamp = access(store, principal.id, previous.get('session_id'), surface, expired=True)
            consent = previous.get('consent_version') == 1 and old_stamp == previous.get('scope_stamp') == stamp
        except (PermissionError, ValueError): pass
    with store._connect() as con:
        latest = con.execute('SELECT COALESCE(MAX(id),0) FROM domain_records WHERE kind=?', (EVENT_RECORD_KIND,)).fetchone()[0]
    now = utc_text(utc_now())
    count = upsert_push_subscription(store, {**subscription, 'principal_id': principal.id, 'session_id': principal.session_id,
        'surface': surface, 'scope_stamp': stamp, 'consent_version': 1 if consent else 0,
        'cursor': previous.get('cursor', latest), 'created_at': now, 'updated_at': now})
    from identity.service import IdentityService
    IdentityService(store).event(principal, 'DEVICE_NOTIFICATION_CONSENT', 'farming' if surface == 'staff' else None, surface, 'ENABLED' if consent else 'NOT_ENABLED')
    return {'status': 'REGISTERED', 'after_timeout': bool(consent), 'subscription_count': count}


def projected(store, row, ctx):
    bus = NotificationBus(store, store._module_exposure.interfaces)
    event, _ = bus._decode_record(row)
    if not bus.visible(event, ctx): return None
    if not store._device_notification_visible(event, ctx): return None
    return bus.project(event, ctx)


def feed(store, principal, surface):
    ctx, _ = access(store, principal.id, principal.session_id, surface)
    with store._connect() as con:
        rows = con.execute('SELECT id,domain,data_json FROM domain_records WHERE kind=? ORDER BY id DESC LIMIT 1000', (EVENT_RECORD_KIND,)).fetchall()
    items = []
    for row in rows:
        try: item = projected(store, row, ctx)
        except (PermissionError, ValueError): continue
        if item: items.append(item)
        if len(items) == 100: break
    return {'items': items, 'availability': 'LIVE', 'notice': 'Recent authorized notifications; not an approval or complete audit history.'}


def dispatch_pending(store, *, sender=None, env=None, max_attempts=8):
    private = settings(env)
    if not private: return {'status': 'NOT_CONFIGURED', 'sent': 0, 'failed': 0}
    send = sender or web_push._default_sender
    sent = failed = attempted = 0
    for sub in push_subscriptions(store):
        if sub.get('surface') not in {'owner', 'staff'}: continue
        if sub.get('consent_version') != 1: continue
        with store._connect() as con:
            rows = con.execute('SELECT id,domain,data_json FROM domain_records WHERE kind=? AND id>? ORDER BY id LIMIT 100', (EVENT_RECORD_KIND, sub.get('cursor', 0))).fetchall()
        for row in rows:
            if attempted >= min(max_attempts, 50): break
            try:
                # Re-read the binding on every attempt; logout, switch and unsubscribe win.
                current = next((r for r in push_subscriptions(store, sub['principal_id']) if r.get('endpoint') == sub['endpoint']), None)
                if current != sub: break
                ctx, stamp = access(store, sub['principal_id'], sub.get('session_id'), sub['surface'], expired=sub.get('consent_version') == 1)
                if stamp != sub.get('scope_stamp'): break
            except (PermissionError, ValueError): break
            try: item = projected(store, row, ctx)
            except (PermissionError, ValueError): item = None
            if item and item['severity'] in web_push.ATTENTION_SEVERITIES:
                attempted += 1
                payload = {'title': 'Chief update' if sub['surface'] == 'owner' else 'Farm Agent update',
                    'body': 'Sign in to view your update.', 'url': '/' + sub['surface'] + '/',
                    'tag': 'chief-device-' + str(row['id'])}
                try: send(sub, payload, private)
                except Exception as exc:
                    if getattr(getattr(exc, 'response', None), 'status_code', None) in {404, 410}:
                        remove_push_endpoint(store, sub['endpoint'])
                    failed += 1; break
                sent += 1
            advance_push_cursor(store, sub['principal_id'], sub['endpoint'], row['id']); sub['cursor'] = row['id']
    return {'status': 'DELIVERED' if sent else 'IDLE', 'sent': sent, 'failed': failed}


def route(handler, store, principal, path, method, body):
    if not path.startswith('/api/device-notifications/'): return False
    parts = path.strip('/').split('/')
    if len(parts) != 4 or parts[2] not in {'owner', 'staff'}:
        raise ValueError('Unknown notification route.')
    surface, action = parts[2:]
    access(store, principal.id, principal.session_id, surface)
    if action == 'status' and method in {'GET', 'HEAD'}: handler.json(configuration())
    elif action == 'feed' and method in {'GET', 'HEAD'}: handler.json(feed(store, principal, surface))
    elif action == 'subscription' and method == 'POST': handler.json(register(store, principal, surface, body))
    elif action == 'unsubscribe' and method == 'POST':
        if not isinstance(body, dict) or set(body) != {'endpoint'}: raise ValueError('A device endpoint is required.')
        endpoint = web_push.validate_endpoint(body['endpoint'])
        rows = push_subscriptions(store, principal.id)
        if any(r.get('endpoint') == endpoint and r.get('surface') == surface for r in rows):
            remove_push_subscription(store, principal.id, endpoint)
        handler.json({'status': 'REMOVED'})
    else: handler.json({'status': 'NOT_FOUND'}, 404)
    return True
