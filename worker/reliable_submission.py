from __future__ import annotations
from typing import Any
from .final_submission import FinalSubmissionExecutor
from .action_gate import ActionRequest, ApprovalRequired, PolicyBlocked
from policy.rules import Decision
from .recovery import RecoveryManager, RecoveryDecision
from database.store_extensions import add_audit

class ReliableFinalSubmissionExecutor(FinalSubmissionExecutor):
    """Adds attempt tracking and conservative recovery to the v23 executor."""
    def __init__(self, store, *, policy_gate=None, browser_factory=None, max_retries=2, candidate_fact_provider=None):
        super().__init__(store, policy_gate=policy_gate, browser_factory=browser_factory, candidate_fact_provider=candidate_fact_provider)
        self.recovery = RecoveryManager(store, max_retries=max_retries)

    def submit(self, application_id: str, url: str, submit_selector: str, *, approved=False, expected_form_hash=None) -> dict[str, Any]:
        # Approval is a hard boundary: do not create a submission attempt or
        # convert an approval refusal into a recovery/unknown outcome.
        gate_decision = self.gate.decide(ActionRequest("submit_application", {
            "application_id": application_id, "url": url, "submit_selector": submit_selector,
            "expected_form_hash": expected_form_hash or ""
        }))
        if gate_decision.decision == Decision.BLOCK:
            raise PolicyBlocked(gate_decision.reason)
        if gate_decision.decision == Decision.ASK and not approved:
            raise ApprovalRequired(gate_decision.reason)

        pre = self.preflight(application_id, url, expected_form_hash=expected_form_hash)
        if not pre.ok:
            return super().submit(application_id, url, submit_selector, approved=approved, expected_form_hash=expected_form_hash)
        attempt = self.recovery.begin(application_id, "FINAL_SUBMISSION", pre.form_hash)
        try:
            result = super().submit(application_id, url, submit_selector, approved=approved, expected_form_hash=expected_form_hash)
            status=str(result.get("status", "FAILED")).upper()
            if status == "SUBMITTED":
                self.recovery.finish(attempt, "SUBMITTED", details="Submission receipt recorded.", data={**result, "url": url, "submit_selector": submit_selector, "expected_form_hash": expected_form_hash or ""})
                self.recovery.mark_recovery(application_id, RecoveryDecision("STOP", "Application submitted successfully.", 1))
            elif status in {"REVIEW", "BLOCKED"}:
                self.recovery.finish(attempt, status, details=str(result.get("reason", "")), data={**result, "url": url, "submit_selector": submit_selector, "expected_form_hash": expected_form_hash or ""})
                self.recovery.mark_recovery(application_id, RecoveryDecision("ASK" if status == "REVIEW" else "STOP", str(result.get("reason", "")), 1))
            else:
                # A generic FAILED result is deliberately treated as ambiguous unless the
                # executor explicitly labels it as a pre-submit failure.
                outcome = str(result.get("outcome", "SUBMISSION_UNKNOWN")).upper()
                self.recovery.finish(attempt, outcome, details=str(result.get("reason", result.get("message", ""))), data={**result, "url": url, "submit_selector": submit_selector, "expected_form_hash": expected_form_hash or ""})
                decision = self.recovery.decide(application_id, outcome)
                self.recovery.mark_recovery(application_id, decision)
                result["recovery"] = {"action": decision.action, "reason": decision.reason}
            return result
        except (ApprovalRequired, PolicyBlocked):
            raise
        except Exception as exc:
            self.recovery.finish(attempt, "SUBMISSION_UNKNOWN", details=f"{type(exc).__name__}: {exc}", data={"url": url, "submit_selector": submit_selector, "expected_form_hash": expected_form_hash or ""})
            decision=self.recovery.decide(application_id, "SUBMISSION_UNKNOWN")
            self.recovery.mark_recovery(application_id, decision)
            add_audit(self.store, "recovery", "Submission exception classified as ambiguous", status="ASK", details=str(exc), data={"application_id": application_id, "attempt_id": attempt})
            return {"status":"REVIEW","application_id":application_id,"reason":"Submission outcome is unknown; do not retry automatically.","recovery":{"action":decision.action,"reason":decision.reason}}
