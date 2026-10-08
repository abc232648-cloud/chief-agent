"""Standards-based Web Push for the Job Agent PWA.

Push is opt-in and fail-closed: no VAPID configuration means no network delivery.
Only sanitized notification text is sent; application payloads never enter Web Push.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

PUSH_SEVERITIES = {'ACTION_REQUIRED', 'URGENT'}


def config() -> dict:
    public_key = os.getenv('CHIEF_WEB_PUSH_VAPID_PUBLIC_KEY', '').strip()
    private_key = os.getenv('CHIEF_WEB_PUSH_VAPID_PRIVATE_KEY', '').strip()
    subject = os.getenv('CHIEF_WEB_PUSH_VAPID_SUBJECT', '').strip()
    return {
        'enabled': bool(public_key and private_key and subject),
        'public_key': public_key,
        'subject': subject,
        '_private_key': private_key,
    }


def public_config() -> dict:
    value = config()
    return {'enabled': value['enabled'], 'public_key': value['public_key'] if value['enabled'] else ''}


def _valid_endpoint(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme == 'https' and bool(parsed.netloc) and len(value) <= 2048


def validate_subscription(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {'endpoint', 'keys'}:
        raise ValueError('Invalid Web Push subscription.')
    endpoint = payload.get('endpoint')
    keys = payload.get('keys')
    if not isinstance(endpoint, str) or not _valid_endpoint(endpoint) or not isinstance(keys, dict) or set(keys) != {'p256dh', 'auth'}:
        raise ValueError('Invalid Web Push subscription.')
    p256dh, auth = keys.get('p256dh'), keys.get('auth')
    if not isinstance(p256dh, str) or not isinstance(auth, str) or not (20 <= len(p256dh) <= 512) or not (8 <= len(auth) <= 256):
        raise ValueError('Invalid Web Push subscription keys.')
    return {'endpoint': endpoint, 'p256dh': p256dh, 'auth': auth}


def save_subscription(store, human_id: str, payload: dict) -> dict:
    value = validate_subscription(payload)
    with store._connect() as con:
        con.execute(
            """INSERT INTO web_push_subscriptions(human_id,domain,endpoint,p256dh,auth,updated_at)
               VALUES(?,'jobs',?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(endpoint) DO UPDATE SET
                 human_id=excluded.human_id,domain='jobs',p256dh=excluded.p256dh,
                 auth=excluded.auth,updated_at=CURRENT_TIMESTAMP""",
            (human_id, value['endpoint'], value['p256dh'], value['auth']),
        )
    return {'status': 'SUBSCRIBED'}


def remove_subscription(store, human_id: str, endpoint: str) -> dict:
    if not isinstance(endpoint, str) or not _valid_endpoint(endpoint):
        raise ValueError('Invalid Web Push endpoint.')
    with store._connect() as con:
        con.execute("DELETE FROM web_push_subscriptions WHERE human_id=? AND domain='jobs' AND endpoint=?", (human_id, endpoint))
    return {'status': 'UNSUBSCRIBED'}


def subscription_count(store, human_id: str) -> int:
    with store._connect() as con:
        return int(con.execute("SELECT COUNT(*) FROM web_push_subscriptions WHERE human_id=? AND domain='jobs'", (human_id,)).fetchone()[0])


def _drop_endpoint(store, endpoint: str) -> None:
    with store._connect() as con:
        con.execute("DELETE FROM web_push_subscriptions WHERE domain='jobs' AND endpoint=?", (endpoint,))


def send_job_push(store, title: str, body: str, severity: str, related_page: str = '') -> dict:
    """Best-effort delivery. Failures never block the Job Agent or notification ledger."""
    severity = str(severity or 'INFO').upper()
    if severity not in PUSH_SEVERITIES:
        return {'status': 'SKIPPED', 'reason': 'severity'}
    settings = config()
    if not settings['enabled']:
        return {'status': 'SKIPPED', 'reason': 'not_configured'}
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return {'status': 'SKIPPED', 'reason': 'dependency_unavailable'}

    with store._connect() as con:
        rows = [dict(row) for row in con.execute(
            "SELECT endpoint,p256dh,auth FROM web_push_subscriptions WHERE domain='jobs' ORDER BY id"
        )]
    payload = json.dumps({
        'title': str(title)[:160],
        'body': str(body)[:500],
        'severity': severity,
        'url': '/#/',
        'related_page': str(related_page or '')[:80],
    }, ensure_ascii=False, separators=(',', ':'))
    delivered = failed = 0
    for row in rows:
        try:
            webpush(
                subscription_info={'endpoint': row['endpoint'], 'keys': {'p256dh': row['p256dh'], 'auth': row['auth']}},
                data=payload,
                vapid_private_key=settings['_private_key'],
                vapid_claims={'sub': settings['subject']},
                timeout=8,
            )
            delivered += 1
        except WebPushException as exc:
            failed += 1
            response = getattr(exc, 'response', None)
            if getattr(response, 'status_code', None) in {404, 410}:
                _drop_endpoint(store, row['endpoint'])
        except Exception:
            failed += 1
    return {'status': 'SENT' if delivered else 'NO_DELIVERY', 'delivered': delivered, 'failed': failed}
