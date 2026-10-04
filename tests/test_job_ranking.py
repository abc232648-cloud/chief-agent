from skills.job_ranking import canonicalize_url, dedupe_key, rank_job


def test_tracking_parameters_do_not_change_url_identity():
    a = canonicalize_url("https://example.com/jobs/123?utm_source=x&b=2&a=1")
    b = canonicalize_url("https://EXAMPLE.com/jobs/123?a=1&b=2")
    assert a == b == "https://example.com/jobs/123?a=1&b=2"


def test_rank_rewards_remote_junior_security_role():
    out = rank_job(
        {"title": "Junior SOC Analyst", "description": "security monitoring", "remote": True, "compensation": "$50k"},
        {"score": 70, "confidence": 0.85},
        {"status": "NO_OBVIOUS_SCAM"},
    )
    assert out["rank_score"] > 75
    assert "Remote" in out["rank_reasons"]
    assert "Entry/junior signal" in out["rank_reasons"]


def test_suspicious_job_ranks_below_clean_equivalent():
    base = {"title": "SOC Analyst", "description": "security", "remote": True}
    clean = rank_job(base, {"score": 70, "confidence": .8}, {"status": "NO_OBVIOUS_SCAM"})
    suspicious = rank_job(base, {"score": 70, "confidence": .8}, {"status": "SUSPICIOUS"})
    assert suspicious["rank_score"] < clean["rank_score"]


def test_dedupe_key_uses_url_when_available():
    assert dedupe_key({"url": "https://example.com/jobs/1", "title": "A"}) == "url:https://example.com/jobs/1"
