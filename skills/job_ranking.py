from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer",
}

SENIOR_TERMS = ("senior", "sr.", "sr ", "lead", "principal", "staff", "manager", "director", "head of")
ENTRY_TERMS = ("junior", "jr.", "jr ", "entry level", "entry-level", "graduate", "trainee", "intern", "apprentice")


def canonicalize_url(url: str) -> str:
    """Create a conservative URL identity without changing the destination path."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not ((parsed.scheme.lower() == "https" and port == 443) or (parsed.scheme.lower() == "http" and port == 80)):
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    query.sort()
    return urlunparse((parsed.scheme.lower(), host, path, "", urlencode(query), ""))


def _norm(value: Any) -> str:
    text = str(value or "").lower().strip()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def dedupe_key(job: dict[str, Any]) -> str:
    """Exact identity key. URL wins; otherwise title/company/location are used."""
    url = canonicalize_url(str(job.get("url", "")))
    if url:
        return f"url:{url}"
    return "job:{title}|{company}|{location}".format(
        title=_norm(job.get("title")),
        company=_norm(job.get("company")),
        location=_norm(job.get("location")),
    )


def rank_job(job: dict[str, Any], match: dict[str, Any] | None = None, scam: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rank an opportunity deterministically; AI does not control the final score."""
    match = match or {}
    scam = scam or {}
    fit = max(0.0, min(100.0, float(match.get("score", job.get("fit_score", 0)) or 0)))
    confidence = max(0.0, min(1.0, float(match.get("confidence", job.get("confidence", 0)) or 0)))
    scam_status = str(scam.get("status", job.get("scam_status", "NO_OBVIOUS_SCAM")))
    scam_factor = {
        "NO_OBVIOUS_SCAM": 1.0,
        "INSUFFICIENT_EVIDENCE": 0.55,
        "SUSPICIOUS": 0.35,
        "CONFIRMED_SCAM": 0.0,
    }.get(scam_status, 0.5)

    score = fit * 0.60 + confidence * 100 * 0.15 + scam_factor * 100 * 0.15
    reasons: list[str] = []
    if fit >= 70:
        reasons.append("Strong role fit")
    elif fit >= 40:
        reasons.append("Moderate role fit")
    else:
        reasons.append("Weak role fit")
    if job.get("remote") is True:
        score += 7
        reasons.append("Remote")
    if job.get("compensation") not in (None, ""):
        score += 3
        reasons.append("Compensation information present")

    title = _norm(job.get("title"))
    description = _norm(job.get("description"))
    role_text = f"{title} {description}"
    if any(term in role_text for term in ENTRY_TERMS):
        score += 5
        reasons.append("Entry/junior signal")
    if any(term in role_text for term in SENIOR_TERMS):
        score -= 10
        reasons.append("Senior-level signal")
    if scam_status == "SUSPICIOUS":
        reasons.append("Scam concerns reduce rank")
    elif scam_status == "CONFIRMED_SCAM":
        reasons.append("Confirmed scam")

    score = max(0.0, min(100.0, score))
    return {"rank_score": round(score, 2), "rank_reasons": reasons, "dedupe_key": dedupe_key(job), "canonical_url": canonicalize_url(str(job.get("url", "")))}
