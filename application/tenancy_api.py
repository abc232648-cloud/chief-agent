"""Authenticated Chief organization/farm tenancy API."""
from urllib.parse import urlsplit

from domains import tenancy


def dispatch(handler, store, principal, path, method, body):
    parsed = urlsplit(handler.path)
    if parsed.path != "/api/tenancy":
        return False
    if parsed.query:
        raise ValueError("Chief tenancy does not accept query parameters.")
    if method in {"GET", "HEAD"}:
        handler.json(tenancy.overview(store, principal))
    elif method == "POST":
        handler.json(tenancy.append(store, principal, body))
    else:
        handler.json({"status": "METHOD_NOT_ALLOWED"}, 405)
    return True
