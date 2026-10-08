from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

MAX_SUBSCRIPTIONS = 8
MAX_STATE_BYTES = 128 * 1024
STATE_FILE = 'job-web-push-subscriptions.json'
ATTENTION_SEVERITIES = frozenset({'ACTION_REQUIRED', 'URGENT'})
PUSH_TIMEOUT_SECONDS = 3
MAX_PUSH_WORKERS = 4


def _environment(env=None):
    from control.settings import environment
    return environment(env)


def _private_key(env=None) -> str:
    from private_secrets.service import resolve_configured
    return resolve_configured(
        _environment(env),
        'CHIEF_WEB_PUSH_PRIVATE_KEY',
        consumer='system.webpush',
        domain='system',
    ).strip()


def _valid_subject(subject: str) -> bool:
    return subject.startswith('mailto:') or subject.startswith('https://')


def client_config(env=None) -> dict[str, Any]:
    """Return only browser-safe Web Push configuration.

    The public key is exposed only when the corresponding private key and valid
    VAPID contact subject are also available. Production private keys therefore
    still have to pass Chief's secret-reference boundary.
    """
    e = _environment(env)
    public_key = str(e.get('CHIEF_WEB_PUSH_PUBLIC_KEY', '')).strip()
    subject = str(e.get('CHIEF_WEB_PUSH_SUBJECT', '')).strip()
    try:
        private_ready = bool(_private_key(e))
    except Exception:
        private_ready = False
    enabled = bool(public_key and _valid_subject(subject) and private_ready)
    return {
        'enabled': enabled,
        'public_key': public_key if enabled else '',
        'attention_severities': sorted(ATTENTION_SEVERITIES),
    }


def _state_path(store) -> Path:
    return Path(store.path).resolve().parent / STATE_FILE


def _validate_subscription(payload: dict[str, Any]) -> dict[str, str]:
    if set(payload) - {'endpoint', 'keys'}:
        raise ValueError('Push subscription contains unsupported fields.')
    endpoint = str(payload.get('endpoint', '')).strip()
    keys = payload.get('keys')
    if not endpoint.startswith('https://') or len(endpoint) > 4096:
        raise ValueError('Push subscription endpoint must be HTTPS.')
    if not isinstance(keys, dict) or set(keys) != {'p256dh', 'auth'}:
        raise ValueError('Push subscription keys are incomplete.')
    p256dh = str(keys.get('p256dh', '')).strip()
    auth = str(keys.get('auth', '')).strip()
    if not 20 <= len(p256dh) <= 512 or not 8 <= len(auth) <= 256:
        raise ValueError('Push subscription keys are invalid.')
    return {'endpoint': endpoint, 'p256dh': p256dh, 'auth': auth}


def _load(store) -> list[dict[str, str]]:
    path = _state_path(store)
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError('Push subscription state is invalid.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list) or len(data) > MAX_SUBSCRIPTIONS:
        raise ValueError('Push subscription state is invalid.')
    result = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError('Push subscription state is invalid.')
        result.append(_validate_subscription({'endpoint': item.get('endpoint'), 'keys': {'p256dh': item.get('p256dh'), 'auth': item.get('auth')}}))
    return result


def _write(store, subscriptions: list[dict[str, str]]) -> None:
    path = _state_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    if path.is_symlink() or tmp.is_symlink():
        raise ValueError('Push subscription state path is unsafe.')
    encoded = json.dumps(subscriptions, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError('Push subscription state is too large.')
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != 'nt':
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        finally:
            raise


def subscribe(store, payload: dict[str, Any]) -> dict[str, Any]:
    if not client_config()['enabled']:
        raise ValueError('Web Push is not configured on this Chief instance.')
    item = _validate_subscription(payload)
    subscriptions = _load(store)
    subscriptions = [existing for existing in subscriptions if existing['endpoint'] != item['endpoint']]
    subscriptions.append(item)
    if len(subscriptions) > MAX_SUBSCRIPTIONS:
        subscriptions = subscriptions[-MAX_SUBSCRIPTIONS:]
    _write(store, subscriptions)
    return {'status': 'SUBSCRIBED', 'subscription_id': hashlib.sha256(item['endpoint'].encode()).hexdigest()[:16]}


def unsubscribe(store, endpoint: str) -> dict[str, str]:
    endpoint = str(endpoint or '').strip()
    if not endpoint.startswith('https://') or len(endpoint) > 4096:
        raise ValueError('A valid HTTPS push endpoint is required.')
    current = _load(store)
    remaining = [item for item in current if item['endpoint'] != endpoint]
    if len(remaining) != len(current):
        _write(store, remaining)
    return {'status': 'UNSUBSCRIBED'}


def _wire_subscription(item: dict[str, str]) -> dict[str, Any]:
    return {'endpoint': item['endpoint'], 'keys': {'p256dh': item['p256dh'], 'auth': item['auth']}}


def _send_one(webpush, item: dict[str, str], payload: str, private_key: str, subject: str) -> tuple[str, bool, bool]:
    try:
        webpush(
            subscription_info=_wire_subscription(item),
            data=payload,
            vapid_private_key=private_key,
            vapid_claims={'sub': subject},
            ttl=300,
            timeout=PUSH_TIMEOUT_SECONDS,
        )
        return item['endpoint'], True, False
    except Exception as exc:
        response = getattr(exc, 'response', None)
        return item['endpoint'], False, getattr(response, 'status_code', None) in {404, 410}


def deliver_job_attention(store, *, notification_id: int, title: str, body: str, severity: str, related_page: str = '') -> dict[str, int]:
    """Best-effort bounded Web Push; never raises into the caller's job workflow."""
    severity = str(severity or '').upper()
    if severity not in ATTENTION_SEVERITIES:
        return {'attempted': 0, 'sent': 0, 'removed': 0}
    config = client_config()
    if not config['enabled']:
        return {'attempted': 0, 'sent': 0, 'removed': 0}
    try:
        subscriptions = _load(store)
        private_key = _private_key()
        subject = str(_environment().get('CHIEF_WEB_PUSH_SUBJECT', '')).strip()
        from pywebpush import webpush
    except Exception:
        return {'attempted': 0, 'sent': 0, 'removed': 0}
    if not subscriptions:
        return {'attempted': 0, 'sent': 0, 'removed': 0}

    payload = json.dumps({
        'id': notification_id,
        'title': str(title)[:240],
        'body': str(body)[:2000],
        'severity': severity,
        'related_page': str(related_page or '')[:120],
    }, separators=(',', ':'))
    sent = 0
    dead = set()
    workers = min(MAX_PUSH_WORKERS, len(subscriptions))
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='chief-webpush') as pool:
            futures = [pool.submit(_send_one, webpush, item, payload, private_key, subject) for item in subscriptions]
            for future in as_completed(futures):
                endpoint, delivered, expired = future.result()
                sent += int(delivered)
                if expired:
                    dead.add(endpoint)
    except Exception:
        # Delivery must remain observational. Command execution and recorded
        # notification state stay authoritative even if the sender fails.
        pass
    if dead:
        try:
            _write(store, [item for item in subscriptions if item['endpoint'] not in dead])
        except Exception:
            pass
    return {'attempted': len(subscriptions), 'sent': sent, 'removed': len(dead)}
