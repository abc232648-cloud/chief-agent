"""Authenticated Job PWA integration endpoints.

These routes only manage browser notification subscriptions. They never approve,
reject, submit, retry, or otherwise mutate Job Agent work.
"""
from __future__ import annotations


def _storage_ready(store) -> bool:
    with store._connect() as con:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_push_subscriptions'").fetchone() is not None


def _ensure_development_storage(store) -> bool:
    if _storage_ready(store):
        return True
    if getattr(store, 'operational', False):
        return False
    with store._connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS web_push_subscriptions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            human_id TEXT NOT NULL,
            domain TEXT NOT NULL CHECK(domain='jobs') DEFAULT 'jobs',
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute('CREATE INDEX IF NOT EXISTS idx_web_push_subscriptions_human_domain ON web_push_subscriptions(human_id,domain)')
    return True


def dispatch(handler, store, service, principal, path, method, body):
    if not path.startswith('/api/job-pwa/'):
        return False
    service.authorize(principal, 'work.read', 'jobs', sensitive=False)
    from notifications.web_push import public_config, remove_subscription, save_subscription, subscription_count

    ready = _ensure_development_storage(store)
    if path == '/api/job-pwa/push-config' and method in {'GET', 'HEAD'}:
        config = public_config()
        handler.json({
            **config,
            'storage_ready': ready,
            'subscribed': subscription_count(store, principal.id) if ready else 0,
        })
        return True

    if not ready:
        handler.json({'status': 'PUSH_STORAGE_NOT_PROVISIONED'}, 503)
        return True

    if path == '/api/job-pwa/push-subscriptions' and method == 'POST':
        handler.json(save_subscription(store, principal.id, body), 201)
        return True

    if path == '/api/job-pwa/push-unsubscribe' and method == 'POST':
        if not isinstance(body, dict) or set(body) != {'endpoint'}:
            raise ValueError('An exact Web Push endpoint is required.')
        handler.json(remove_subscription(store, principal.id, body['endpoint']))
        return True

    handler.json({'status': 'NOT_FOUND'}, 404)
    return True
