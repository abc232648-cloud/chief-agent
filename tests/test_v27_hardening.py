import json
from security_hardening import redact, canonical_https_url, approved_host_matches, safe_selector
from e2e_simulator import run_all


def test_secret_redaction_recursive():
    out = redact({"password":"abc", "nested":{"api_key":"xyz"}, "text":"Authorization: Bearer abcdef"})
    assert out["password"] == "[REDACTED]"
    assert out["nested"]["api_key"] == "[REDACTED]"
    assert "[REDACTED]" in out["text"]


def test_https_canonicalization_removes_tracking():
    assert canonical_https_url("HTTPS://Example.COM/path?utm_source=x&a=2") == "https://example.com/path?a=2"


def test_http_rejected():
    try:
        canonical_https_url("http://example.com")
        assert False
    except ValueError:
        pass


def test_subdomain_matching_is_bounded():
    assert approved_host_matches("https://apply.example.com/x", ["example.com"])
    assert not approved_host_matches("https://example.com.evil.test/x", ["example.com"])


def test_selector_rejects_script_payload():
    assert not safe_selector("javascript:alert(1)")
    assert safe_selector("#submit-button")


def test_end_to_end_scenarios(tmp_path):
    result = run_all(tmp_path / "e2e.db")
    assert result["scenarios"]["unconfirmed_cv_claim"]["result"]["status"] == "BLOCKED"
    assert result["scenarios"]["page_never_loads"]["safe_to_retry"] is True
    assert result["scenarios"]["ambiguous_submission"]["safe_to_retry"] is False
    audit_blob = json.dumps(result["audit"])
    assert "super-secret" not in audit_blob
    assert "do-not-log" not in audit_blob
