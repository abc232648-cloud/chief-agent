from __future__ import annotations

from typing import Any

from skills.job_discovery import accept_raw_listing
from skills.job_normalization import normalize_job
from skills.scam_screening.skill import screen_job
from skills.candidate_matching import match_candidate
from skills.ai_job_analysis import analyze_job_with_ai
from skills.job_ranking import rank_job


def process_job(
    raw_job: dict[str, Any],
    preferences: dict[str, Any],
    *,
    gateway=None,
    candidate_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = accept_raw_listing(raw_job)
    job = normalize_job(raw)
    scam = screen_job(job)
    match = match_candidate(job, preferences)
    eligible = match["eligible"] and scam["status"] not in {"CONFIRMED_SCAM"}

    ranking = rank_job(job, match, scam)
    result = {"job": job, "scam": scam, "match": match, "ranking": ranking, "eligible": eligible}

    if gateway is not None:
        analysis, summary_text, response = analyze_job_with_ai(gateway, job, candidate_facts or {})
        # AI can enrich reasoning, but cannot override deterministic safety gates.
        if scam["status"] == "CONFIRMED_SCAM":
            analysis["recommended_action"] = "REJECT"
        if not match["eligible"]:
            analysis["recommended_action"] = "REJECT"
        result["ai_analysis_json"] = analysis
        result["ai_summary_txt"] = summary_text
        # Backward-compatible alias for callers using the previous field.
        result["ai_analysis"] = analysis
        result["ai_provider"] = response.provider
        result["ai_model"] = response.model
        result["ai_fallback_used"] = response.fallback_used

    return result
