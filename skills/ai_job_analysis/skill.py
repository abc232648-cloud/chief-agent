from __future__ import annotations

import json
from typing import Any

from gateway.models import AIRequest, AIResponse

SYSTEM_PROMPT = """You analyze job opportunities for a cybersecurity candidate.
Return ONE JSON object with exactly these top-level keys: analysis, summary_text.

The analysis value must be an object with exactly these keys:
summary, role_fit, fit_reasons, missing_requirements, scam_concerns, evidence_flags, recommended_action, confidence

summary_text must be plain text containing these seven numbered sections, in this exact order:
1. Summary
2. Role fit
3. Fit reasons
4. Missing requirements
5. Scam concerns
6. Evidence flags
7. Recommended action
8. Confidence

Rules:
- Never invent candidate qualifications, experience, certifications, or skills.
- Treat missing candidate evidence as UNKNOWN, not as proof of possession.
- Do not call a job legitimate merely because no scam signal is visible.
- recommended_action must be one of: CONSIDER, REJECT, NEEDS_VERIFICATION.
- confidence must be a number from 0 to 1.
- Keep claims grounded in the supplied job and candidate evidence.
- summary_text must faithfully reflect the structured analysis; do not introduce new facts.
- Return JSON only. Do not wrap it in markdown fences.
- Be concise: one short sentence per summary section and at most two brief items per list.
"""

ANALYSIS_KEYS = {
    "summary", "role_fit", "fit_reasons", "missing_requirements",
    "scam_concerns", "evidence_flags", "recommended_action", "confidence",
}
RESPONSE_KEYS = {"analysis", "summary_text"}
VALID_ACTIONS = {"CONSIDER", "REJECT", "NEEDS_VERIFICATION"}
SUMMARY_HEADINGS = [
    "1. Summary",
    "2. Role fit",
    "3. Fit reasons",
    "4. Missing requirements",
    "5. Scam concerns",
    "6. Evidence flags",
    "7. Recommended action",
    "8. Confidence",
]


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _validate_analysis(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("AI analysis must be a JSON object")
    missing = ANALYSIS_KEYS - value.keys()
    extra = value.keys() - ANALYSIS_KEYS
    if missing:
        raise ValueError(f"AI analysis missing keys: {sorted(missing)}")
    if extra:
        raise ValueError(f"AI analysis contains unexpected keys: {sorted(extra)}")
    if value["recommended_action"] not in VALID_ACTIONS:
        raise ValueError("AI analysis contains an invalid recommended_action")
    try:
        confidence = float(value["confidence"])
    except (TypeError, ValueError) as exc:
        raise ValueError("AI analysis confidence must be numeric") from exc
    if not 0 <= confidence <= 1:
        raise ValueError("AI analysis confidence must be between 0 and 1")
    value["confidence"] = confidence
    return value


def _validate_summary_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("AI summary_text must be non-empty text")
    text = value.strip()
    positions = [text.find(heading) for heading in SUMMARY_HEADINGS]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("AI summary_text must contain the required numbered sections in order")
    return text


def _extract_response(text: str) -> tuple[dict[str, Any], str]:
    cleaned = _strip_code_fence(text)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("AI analysis response was not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("AI analysis response must be a JSON object")
    missing = RESPONSE_KEYS - value.keys()
    extra = value.keys() - RESPONSE_KEYS
    if missing:
        raise ValueError(f"AI response missing keys: {sorted(missing)}")
    if extra:
        raise ValueError(f"AI response contains unexpected keys: {sorted(extra)}")
    return _validate_analysis(value["analysis"]), _validate_summary_text(value["summary_text"])


def analyze_job_with_ai(
    gateway,
    job: dict[str, Any],
    candidate_facts: dict[str, Any],
) -> tuple[dict[str, Any], str, AIResponse]:
    """Ask the approved gateway for structured analysis plus a human-readable summary."""
    payload = json.dumps(
        {"job": job, "candidate_facts": candidate_facts},
        ensure_ascii=False,
        sort_keys=True,
    )
    response = gateway.generate(AIRequest(SYSTEM_PROMPT, payload, temperature=0.0, max_tokens=768))
    analysis, summary_text = _extract_response(response.text)
    return analysis, summary_text, response
