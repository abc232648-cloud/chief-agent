import pytest
from worker.action_gate import ActionRequest, ApprovalRequired, PolicyBlocked, PolicyGate


def test_low_risk_action_reaches_executor():
    calls = []
    result = PolicyGate().execute(
        ActionRequest("read_job_listing", {"url": "example"}),
        lambda payload: calls.append(payload) or "ok",
    )
    assert result == "ok"
    assert calls == [{"url": "example"}]


def test_high_impact_action_requires_approval():
    calls = []
    with pytest.raises(ApprovalRequired, match="ASK"):
        PolicyGate().execute(
            ActionRequest("submit_application", {"job_id": "123"}),
            lambda payload: calls.append(payload),
        )
    assert calls == []


def test_high_impact_action_executes_only_when_approved():
    calls = []
    PolicyGate().execute(
        ActionRequest("submit_application", {"job_id": "123"}),
        lambda payload: calls.append(payload),
        approved=True,
    )
    assert calls == [{"job_id": "123"}]


@pytest.mark.parametrize("action", [
    "pay_money",
    "enter_password",
    "enter_otp",
    "secret_disclosure",
    "credential_disclosure",
    "fabricate_experience",
])
def test_forbidden_actions_never_reach_executor(action):
    calls = []
    with pytest.raises(PolicyBlocked, match="BLOCK"):
        PolicyGate().execute(
            ActionRequest(action, {"anything": "value"}),
            lambda payload: calls.append(payload),
            approved=True,
        )
    assert calls == []
