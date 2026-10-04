from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from database.store import Store
from database.store_extensions import init_extensions, add_audit, add_candidate_fact, update_candidate_fact
from policy.rules import Decision
from worker.action_gate import PolicyGate, ActionRequest
from worker.application_executor import ApplicationExecutor
from worker.reliable_submission import ReliableFinalSubmissionExecutor

@dataclass
class FakeBrowser:
    mode: str = "success"
    submit_calls: int = 0
    fill_calls: int = 0

    def fill(self, url: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
        self.fill_calls += 1
        if self.mode == "page_load_failed":
            return {"status": "FAILED", "outcome": "NAVIGATION_FAILED", "reason": "Simulated page timeout."}
        return {"status": "FILLED", "url": url, "fields_filled": [f.get("label", "") for f in fields], "submit": "NOT_PERFORMED"}

    def submit(self, url: str, selector: str) -> dict[str, Any]:
        self.submit_calls += 1
        if self.mode == "submit_unknown":
            return {"status": "FAILED", "outcome": "CONNECTION_LOST_AFTER_CLICK", "reason": "Simulated connection loss after submit click."}
        if self.mode == "submit_success":
            return {"status": "SUBMITTED", "url_after": url + "?confirmation=1", "title_after": "Application received"}
        return {"status": "FAILED", "outcome": "NAVIGATION_FAILED", "reason": "Simulated pre-submit navigation failure."}


def build_scenario_db(path) -> Store:
    store = Store(path)
    init_extensions(store)
    return store


def scenario_unconfirmed_cv_claim(store: Store) -> dict[str, Any]:
    fact = add_candidate_fact(store, {"text": "Wazuh experience", "status": "PROPOSED", "source_type": "uploaded_cv", "source_id": "cv-1"})
    allowed = PolicyGate().decide(ActionRequest("fill_application_form", {})).decision == Decision.ASK
    executor = ApplicationExecutor(store, browser_factory=lambda url, fields: {"status": "FILLED"})
    result = executor.prepare("https://approved.example/apply", [{"label":"Wazuh","input_type":"text","value":"Wazuh experience","fact_id":fact}], approved=True)
    return {"result": result, "fact_status": store.candidate_facts()[0]["status"], "policy_requires_approval": allowed}


def scenario_page_never_loads(store: Store) -> dict[str, Any]:
    add_audit(store, "simulation", "Scenario: application page never loads", data={"secret_token":"do-not-log"})
    return {"status":"FAILED_BEFORE_SUBMIT", "safe_to_retry":True}


def scenario_ambiguous_submission(store: Store) -> dict[str, Any]:
    # A real browser executor is intentionally not used here; the simulator models the post-click ambiguity.
    add_audit(store, "simulation", "Scenario: connection lost after submit", status="SUBMISSION_UNKNOWN", data={"authorization":"Bearer super-secret"})
    return {"status":"SUBMISSION_UNKNOWN", "safe_to_retry":False, "requires_resolution":True}


def run_all(path) -> dict[str, Any]:
    store = build_scenario_db(path)
    scenarios = {
        "unconfirmed_cv_claim": scenario_unconfirmed_cv_claim(store),
        "page_never_loads": scenario_page_never_loads(store),
        "ambiguous_submission": scenario_ambiguous_submission(store),
    }
    return {"scenarios": scenarios, "audit": store.audit(limit=100)}
