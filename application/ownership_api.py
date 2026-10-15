"""Authenticated Chief ownership and organization/farm tenancy APIs."""
from urllib.parse import urlsplit

from domains import ownership


def dispatch(handler, store, principal, path, method, body):
    parsed = urlsplit(handler.path)
    if parsed.path == "/api/tenancy":
        from .tenancy_api import dispatch as tenancy_dispatch
        return tenancy_dispatch(handler, store, principal, path, method, body)
    if parsed.path != "/api/ownership":
        return False
    if parsed.query:
        raise ValueError("Chief ownership does not accept query parameters.")
    if method in {"GET", "HEAD"}:
        handler.json(ownership.overview(store, principal))
    elif method == "POST":
        handler.json(ownership.append(store, principal, body))
    else:
        handler.json({"status": "METHOD_NOT_ALLOWED"}, 405)
    return True
