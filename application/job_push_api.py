"""Authenticated Job Agent PWA Web Push registration surface."""
from __future__ import annotations

from notifications.web_push import register_subscription,subscription_status,unregister_subscription


def dispatch(handler,store,principal,path,method,body):
    if not path.startswith('/api/job-push/'):
        return False
    if path=='/api/job-push/config' and method in {'GET','HEAD'}:
        handler.json(subscription_status(store,principal.id));return True
    if path=='/api/job-push/subscriptions' and method=='POST':
        handler.json(register_subscription(store,principal.id,body),201);return True
    if path=='/api/job-push/unsubscribe' and method=='POST':
        if not isinstance(body,dict) or set(body)!={'endpoint'}:
            raise ValueError('A Web Push endpoint is required.')
        handler.json(unregister_subscription(store,principal.id,body['endpoint']));return True
    handler.json({'status':'NOT_FOUND'},404);return True
