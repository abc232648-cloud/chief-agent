from __future__ import annotations

from typing import Any

SECURITY_TERMS = {
    "cybersecurity", "cyber security", "soc", "security analyst", "security operations",
    "cloud security", "information security", "iam", "siem", "wazuh", "incident response",
    "penetration testing", "penetration tester", "vulnerability", "network security",
}
EXCLUDED_TERMS = {"it support", "help desk", "service desk", "desktop support", "technical support"}


def _text(job: dict[str, Any]) -> str:
    return f"{job.get('title', '')} {job.get('description', '')}".lower()


def match_candidate(job: dict[str, Any], preferences: dict[str, Any]) -> dict[str, Any]:
    text = _text(job)
    if preferences.get("exclude_it_support", True) and any(t in text for t in EXCLUDED_TERMS):
        return {"eligible": False, "score": 0, "confidence": 1.0, "reasons": ["Excluded IT-support role"]}

    score = 0
    reasons: list[str] = []
    hits = [term for term in SECURITY_TERMS if term in text]
    if hits:
        score += min(60, 15 * len(set(hits)))
        reasons.append("Security-related role")
    else:
        reasons.append("No clear security-role signal")

    if preferences.get("remote_preferred", True):
        if job.get("remote") is True:
            score += 25
            reasons.append("Remote preferred")
        else:
            reasons.append("Not confirmed remote")

    if job.get("compensation") not in (None, ""):
        score += 10
        reasons.append("Compensation information present")

    score = min(score, 100)
    confidence = 0.85 if hits else 0.45
    return {"eligible": True, "score": score, "confidence": confidence, "reasons": reasons}
