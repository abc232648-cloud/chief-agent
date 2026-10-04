from skills.job_pipeline import process_job
from skills.evidence_check import can_claim

PREFS = {"remote_preferred": True, "exclude_it_support": True}


def test_security_remote_job_is_ranked():
    result = process_job({
        "title": "Junior SOC Analyst",
        "company": "Example",
        "description": "Monitor security alerts and investigate incidents.",
        "remote": True,
        "source": "example",
    }, PREFS)
    assert result["eligible"] is True
    assert result["match"]["score"] >= 25
    assert result["scam"]["status"] == "NO_OBVIOUS_SCAM"


def test_it_support_is_excluded():
    result = process_job({"title": "IT Support Technician", "description": "Help desk"}, PREFS)
    assert result["eligible"] is False
    assert result["match"]["score"] == 0


def test_money_request_is_confirmed_scam():
    result = process_job({"title": "Security Intern", "description": "Pay a fee before onboarding"}, PREFS)
    assert result["eligible"] is False
    assert result["scam"]["status"] == "CONFIRMED_SCAM"


def test_unknown_evidence_cannot_be_claimed():
    assert can_claim("KNOWN") is True
    assert can_claim("VERIFIED") is True
    assert can_claim("INFERRED") is False
    assert can_claim("UNKNOWN") is False
    assert can_claim("not-a-state") is False
