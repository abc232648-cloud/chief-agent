from __future__ import annotations

import json
from urllib.parse import urlparse
from gateway.models import AIRequest

SOURCE_DISCOVERY_PROMPT = '''You discover potentially useful job sources for a cybersecurity job search.
Return JSON only with exactly this shape:
{"sources":[{"name":"","url":"","kind":"platform|company_careers","reason":""}]}
Rules:
- Suggest only real, recognizable job platforms or company career sites you have reasonable basis to name.
- Prefer HTTPS URLs.
- Do not claim a source is legitimate merely because you recognize its name.
- Do not include credentials, login instructions, payments, or actions.
- LinkedIn and Upwork are important but are not the complete universe.
- Include a small useful set, not an enormous list.
- Return at most three concise sources per request, with a one-sentence reason each.
'''


def discover_sources(gateway, instruction: str, *, max_sources: int = 12) -> dict:
    response = gateway.generate(AIRequest(SOURCE_DISCOVERY_PROMPT, instruction, temperature=0.0, max_tokens=512))
    data = json.loads(response.text.strip().removeprefix("```json").removesuffix("```").strip())
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError("Source discovery returned invalid JSON")
    cleaned = []
    for item in data["sources"][:max_sources]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        kind = str(item.get("kind", "platform")).strip()
        reason = str(item.get("reason", "")).strip()
        if not name or not url or kind not in {"platform", "company_careers"}:
            continue
        cleaned.append({"name": name, "url": url, "kind": kind, "reason": reason})
    return {"sources": cleaned}


def verify_discovered_source(source: dict) -> dict:
    url = str(source.get("url", "")).strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https":
        return {**source, "protocol": parsed.scheme.upper() or "UNKNOWN", "verification_status": "HTTP_QUARANTINED", "confidence": 0.0,
                "notes": "HTTP/non-HTTPS source is quarantined and must not be visited."}
    if not host:
        return {**source, "protocol": "HTTPS", "verification_status": "REJECTED", "confidence": 0.0,
                "notes": "URL has no valid hostname."}
    # Discovery is deliberately not proof. Unknown sources remain REVIEW until a separate verification step establishes identity/legitimacy.
    return {**source, "protocol": "HTTPS", "verification_status": "REVIEW", "confidence": 0.35,
            "notes": "AI-discovered HTTPS source. Identity and legitimacy are not yet verified; no browser visit is permitted."}
