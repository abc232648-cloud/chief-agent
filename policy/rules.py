from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

FORBIDDEN_ACTIONS = frozenset({
    "pay_money", "financial_commitment", "enter_otp", "enter_password",
    "secret_disclosure", "credential_disclosure", "fabricate_qualification",
    "fabricate_experience", "fabricate_skill", "claim_unverified_certification",
    "claim_unverified_employment", "circumvent_security",
})

EVIDENCE_STATES = frozenset({"KNOWN", "VERIFIED", "INFERRED", "UNKNOWN"})
TRUTHFUL_EVIDENCE_STATES = frozenset({"KNOWN", "VERIFIED"})
LOW_RISK_ACTIONS = frozenset({
    "discover_jobs", "discover_sources", "read_job_listing", "normalize_job", "screen_scam", "match_candidate",
    "draft_cv", "draft_cover_letter", "save_application_draft", "log_application", "verify_source",
})
HIGH_IMPACT_ACTIONS = frozenset({"fill_application_form", "submit_application", "accept_job_offer", "send_message_to_employer"})

class Decision(str, Enum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    BLOCK = "BLOCK"

@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str

def is_forbidden(action: str) -> bool:
    return action in FORBIDDEN_ACTIONS

def valid_evidence_state(state: str) -> bool:
    return state in EVIDENCE_STATES

def evidence_is_true(state: str) -> bool:
    """Only KNOWN/VERIFIED evidence may be treated as a truthful fact."""
    return state in TRUTHFUL_EVIDENCE_STATES

def evaluate_action(action: str) -> PolicyDecision:
    if is_forbidden(action):
        return PolicyDecision(Decision.BLOCK, "Forbidden action")
    if action in LOW_RISK_ACTIONS:
        return PolicyDecision(Decision.ALLOW, "Low-risk action")
    if action in HIGH_IMPACT_ACTIONS:
        return PolicyDecision(Decision.ASK, "High-impact action requires user approval")
    return PolicyDecision(Decision.ASK, "Unknown action requires user approval")
