import json
import pytest

from gateway.models import AIResponse
from skills.ai_job_analysis import analyze_job_with_ai
from skills.job_pipeline import process_job


class FakeGateway:
    def __init__(self, payload):
        self.payload = payload

    def generate(self, request):
        return AIResponse("qwen", "test-model", json.dumps(self.payload))


def good_analysis():
    return {
        "summary": "Junior SOC role",
        "role_fit": "good",
        "fit_reasons": ["Security role"],
        "missing_requirements": ["Not established"],
        "scam_concerns": [],
        "evidence_flags": ["UNKNOWN"],
        "recommended_action": "CONSIDER",
        "confidence": 0.8,
    }


def good_payload():
    return {
        "analysis": good_analysis(),
        "summary_text": """1. Summary\nJunior SOC role.\n2. Role fit\nGood.\n3. Fit reasons\nSecurity role.\n4. Missing requirements\nNot established.\n5. Scam concerns\nNone identified.\n6. Evidence flags\nUNKNOWN.\n7. Recommended action\nCONSIDER.\n8. Confidence\n0.8.""",
    }


def test_ai_analysis_returns_json_and_text():
    analysis, summary_text, response = analyze_job_with_ai(
        FakeGateway(good_payload()), {"title": "SOC Analyst"}, {"facts": []}
    )
    assert analysis["recommended_action"] == "CONSIDER"
    assert summary_text.startswith("1. Summary")
    assert "7. Recommended action" in summary_text
    assert "8. Confidence" in summary_text
    assert response.provider == "qwen"


def test_ai_analysis_is_attached_without_replacing_hard_rules():
    result = process_job(
        {"title": "Junior SOC Analyst", "description": "Monitor security alerts", "remote": True},
        {"remote_preferred": True, "exclude_it_support": True},
        gateway=FakeGateway(good_payload()),
        candidate_facts={"facts": []},
    )
    assert result["eligible"] is True
    assert result["ai_analysis_json"]["recommended_action"] == "CONSIDER"
    assert result["ai_summary_txt"].startswith("1. Summary")
    assert result["ai_provider"] == "qwen"


def test_confirmed_scam_overrides_ai_recommendation():
    payload = good_payload()
    payload["analysis"]["recommended_action"] = "CONSIDER"
    result = process_job(
        {"title": "Security Intern", "description": "Pay a fee before onboarding"},
        {"remote_preferred": True, "exclude_it_support": True},
        gateway=FakeGateway(payload),
    )
    assert result["eligible"] is False
    assert result["ai_analysis_json"]["recommended_action"] == "REJECT"


def test_malformed_ai_json_stops_analysis():
    class BadGateway:
        def generate(self, request):
            return AIResponse("qwen", "test-model", "not json")

    with pytest.raises(ValueError, match="valid JSON"):
        analyze_job_with_ai(BadGateway(), {"title": "SOC Analyst"}, {"facts": []})


def test_missing_summary_text_is_rejected():
    payload = good_payload()
    del payload["summary_text"]
    with pytest.raises(ValueError, match="missing keys"):
        analyze_job_with_ai(FakeGateway(payload), {"title": "SOC Analyst"}, {})
