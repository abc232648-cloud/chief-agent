from __future__ import annotations
import json
import re
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from typing import Any

SECRET_KEY_RE = re.compile(r"(?i)(password|passwd|passcode|otp|token|api[_-]?key|secret|authorization|cookie|session[_-]?id|cvv|card[_-]?number|bank[_-]?account)")
TRACKING_KEYS = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","gclid","fbclid","msclkid"}


def redact(value: Any) -> Any:
    """Recursively redact likely secrets before they can enter logs or persisted audit data."""
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if SECRET_KEY_RE.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Never persist obvious bearer/basic authorization material.
        value = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [REDACTED]", value)
        return value
    return value


def safe_json(value: Any) -> str:
    return json.dumps(redact(value), ensure_ascii=False, sort_keys=True)


def canonical_https_url(url: str) -> str:
    """Canonicalize an HTTPS URL without weakening the HTTPS-only rule."""
    p = urlparse(str(url).strip())
    if p.scheme.lower() != "https" or not p.hostname:
        raise ValueError("Only valid HTTPS URLs are accepted")
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING_KEYS]
    host = p.hostname.lower()
    netloc = host
    if p.port and not ((p.scheme.lower() == "https" and p.port == 443)):
        netloc += f":{p.port}"
    path = p.path or "/"
    return urlunparse(("https", netloc, path, "", urlencode(sorted(query)), ""))


def approved_host_matches(url: str, approved_hosts: list[str]) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return any(host == h.lower().rstrip(".") or host.endswith("." + h.lower().rstrip(".")) for h in approved_hosts if h)


def safe_selector(selector: str) -> bool:
    """Reject selectors containing obvious javascript/eval payloads."""
    s = str(selector or "").strip()
    if not s or len(s) > 1000:
        return False
    return not re.search(r"(?i)(javascript:|data:text/html|\beval\s*\(|<script|onerror\s*=|onload\s*=)", s)
