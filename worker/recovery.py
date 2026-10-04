from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from database.store_extensions import add_audit

RETRYABLE = {"FAILED_BEFORE_SUBMIT", "NAVIGATION_FAILED", "FORM_PREPARATION_FAILED"}
AMBIGUOUS = {"SUBMISSION_UNKNOWN", "POST_SUBMIT_TIMEOUT", "CONNECTION_LOST_AFTER_CLICK"}

@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason: str
    attempt_no: int

class RecoveryManager:
    """Conservative recovery boundary. Never auto-retries an ambiguous final submission."""
    def __init__(self, store, *, max_retries: int = 2):
        self.store = store
        self.max_retries = max(0, int(max_retries))

    def begin(self, application_id: str, phase: str, form_hash: str = "") -> int:
        return self.store.add_submission_attempt(application_id, phase, "STARTED", form_hash=form_hash)

    def finish(self, attempt_id: int, outcome: str, *, details: str = "", data: dict[str, Any] | None = None) -> None:
        self.store.finish_submission_attempt(attempt_id, outcome, details=details, data=data or {})
        app = self.store.submission_attempt(attempt_id)
        if app:
            add_audit(self.store, "recovery", f"Submission attempt {attempt_id} finished", status=outcome,
                      details=details, data={"attempt_id": attempt_id, "application_id": app["application_id"], "outcome": outcome})

    def decide(self, application_id: str, outcome: str) -> RecoveryDecision:
        attempts = self.store.submission_attempts(application_id)
        n = len(attempts)
        if outcome in AMBIGUOUS:
            return RecoveryDecision("ASK", "Submission outcome is ambiguous; do not retry because the application may already have been submitted.", n)
        if outcome in RETRYABLE and n <= self.max_retries:
            return RecoveryDecision("RETRY", f"Safe pre-submit failure; retry {n} of {self.max_retries} allowed.", n)
        if outcome in RETRYABLE:
            return RecoveryDecision("ASK", f"Retry limit reached ({self.max_retries}); manual review required.", n)
        return RecoveryDecision("STOP", "No automatic recovery rule applies.", n)

    def mark_recovery(self, application_id: str, decision: RecoveryDecision, *, details: str = "") -> None:
        self.store.set_application_recovery(application_id, decision.action, decision.reason)
        add_audit(self.store, "recovery", f"Recovery decision: {decision.action}", status=decision.action,
                  details=details or decision.reason, data={"application_id": application_id, "attempt_no": decision.attempt_no})
