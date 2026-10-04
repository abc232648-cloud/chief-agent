from policy.rules import Decision, evaluate_action, evidence_is_true, is_forbidden, valid_evidence_state

def test_payment_forbidden(): assert is_forbidden("pay_money")
def test_password_forbidden(): assert is_forbidden("enter_password")
def test_secret_disclosure_forbidden(): assert is_forbidden("secret_disclosure")
def test_credential_disclosure_forbidden(): assert is_forbidden("credential_disclosure")
def test_unknown_state_is_valid_label(): assert valid_evidence_state("UNKNOWN")
def test_true_is_not_evidence_state(): assert not valid_evidence_state("TRUE")
def test_only_verified_evidence_is_truth():
    assert evidence_is_true("KNOWN")
    assert evidence_is_true("VERIFIED")
    assert not evidence_is_true("INFERRED")
    assert not evidence_is_true("UNKNOWN")
def test_policy_allows_low_risk(): assert evaluate_action("read_job_listing").decision == Decision.ALLOW
def test_policy_asks_for_high_impact(): assert evaluate_action("submit_application").decision == Decision.ASK
def test_policy_blocks_forbidden(): assert evaluate_action("enter_password").decision == Decision.BLOCK
def test_policy_asks_unknown(): assert evaluate_action("some_future_action").decision == Decision.ASK
