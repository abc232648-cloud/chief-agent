from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from browser.playwright_reader import BrowserReadError
from .action_gate import ActionRequest, PolicyGate, PolicyBlocked, ApprovalRequired
from database.store_extensions import add_audit

SENSITIVE_FIELD_WORDS = {
    "password", "passcode", "otp", "one-time", "verification", "cvv", "card",
    "credit", "debit", "bank", "routing", "account number", "security code",
    "social security", "national id", "passport", "tax id", "secret", "api key", "private key",
}
ALLOWED_FIELD_TYPES = {"text", "email", "tel", "url", "textarea"}

@dataclass(frozen=True)
class FormField:
    selector: str
    label: str
    input_type: str

class ApplicationExecutor:
    """Controlled browser mutation layer.

    It may fill a narrowly allow-listed set of non-secret fields only after an
    explicit approval. It never logs in, enters credentials/OTP/payment data,
    uploads files, clicks submit, or accepts offers.
    """
    def __init__(self, store, *, policy_gate: PolicyGate | None = None, browser_factory=None, candidate_fact_provider=None):
        self.store = store
        self.candidate_fact_provider = candidate_fact_provider or (lambda: store.candidate_facts(status="USER_CONFIRMED"))
        self.gate = policy_gate or PolicyGate()
        self.browser_factory = browser_factory

    @staticmethod
    def _is_safe_field(label: str, input_type: str) -> bool:
        low = label.strip().lower()
        if any(word in low for word in SENSITIVE_FIELD_WORDS):
            return False
        return input_type.lower() in ALLOWED_FIELD_TYPES

    def _confirmed_fact_value(self, fact_id: int, value: str) -> bool:
        rows = self.candidate_fact_provider()
        for row in rows:
            if int(row["id"]) == int(fact_id):
                # The worker may only reproduce a value that is literally
                # supported by a user-confirmed fact. No inference is allowed.
                expected = str(value).strip().casefold()
                fact = str(row['text']).strip().casefold()
                # A complete fact or its explicit labelled value is supported;
                # substrings could remove negations or inflate experience.
                labelled = fact.split(':', 1)[1].strip() if ':' in fact else None
                return bool(expected) and expected in (fact, labelled)
        return False

    def validate_fields(self, fields: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        for i, field in enumerate(fields):
            label = str(field.get("label", ""))
            input_type = str(field.get("input_type", "text"))
            if not label:
                errors.append(f"field[{i}] missing label")
            if not self._is_safe_field(label, input_type):
                errors.append(f"field[{i}] is not an allowed non-sensitive field: {label}")
            if "fact_id" not in field:
                errors.append(f"field[{i}] requires fact_id from a USER_CONFIRMED candidate fact")
            elif not self._confirmed_fact_value(int(field["fact_id"]), str(field.get("value", ""))):
                errors.append(f"field[{i}] value is not supported by the referenced USER_CONFIRMED fact")
        return errors

    def prepare(self, url: str, fields: list[dict[str, Any]], *, approved: bool = False, application_id: str | None = None) -> dict[str, Any]:
        request = ActionRequest("fill_application_form", {"url": url, "fields": fields})
        decision = self.gate.decide(request)
        if decision.decision.value == "BLOCK":
            raise PolicyBlocked(decision.reason)
        if decision.decision.value == "ASK" and not approved:
            raise ApprovalRequired(decision.reason)
        parsed = urlparse(url)
        from browser.site_access import SiteAccess
        access = SiteAccess(self.store)
        managed = access.for_url(url)
        if managed and managed['status'] != 'ACTIVE':
            return {'status': 'BLOCKED', 'reason': 'Website access is paused, revoked, or not authorized.'}
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            return {"status": "HTTP_QUARANTINED" if parsed.scheme.lower() == "http" else "FAILED", "url": url,
                    "reason": "Application pages must use HTTPS."}
        errors = self.validate_fields(fields)
        if errors:
            add_audit(self.store, "application", "Rejected unsafe form fields", status="BLOCKED", details="; ".join(errors), data={"url": url})
            return {"status": "BLOCKED", "url": url, "errors": errors}
        host = (parsed.hostname or "").lower()
        approved_sources = [r for r in self.store.sources() if str(r.get("verification_status", "")).upper() == "APPROVED"]
        approved_hosts = [(urlparse(str(r.get("url", ""))).hostname or "").lower() for r in approved_sources]
        if not any(host == h or host.endswith("." + h) for h in approved_hosts if h):
            return {"status": "REVIEW", "url": url, "reason": "Application domain is not an approved source. Source verification is required before browser mutation."}
        from worker.approval_context import revalidate as revalidate_approval
        revalidate_approval()
        if self.browser_factory:
            return self.browser_factory(url, fields)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserReadError("Playwright is not installed") from exc
        try:
            with sync_playwright() as p:
                from browser.request_policy import controlled_browser
                from .form_guard import fill_reviewed,install_writer
                def revalidate():
                    revalidate_approval()
                    if managed:access.assert_active(managed['domain'],managed['revision'])
                    if self.validate_fields(fields):raise ValueError('Candidate fact authority changed.')
                with controlled_browser(p,url,storage_state=access.state(managed['domain']) if managed else None,
                        revision_check=revalidate,store=self.store) as (context,policy):
                    writer=install_writer(context)
                    page = context.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                    fill_reviewed(page,context,policy,url,fields,self._is_safe_field,writer=writer,revalidate=revalidate)
                    filled = [str(field['label']) for field in fields]
                    # Deliberately no submit/click/navigation after filling.
                    add_audit(self.store, "application", "Filled approved non-sensitive application fields", status="FILLED", details=url, data={"fields": filled, "application_id": application_id})
                    if application_id:
                        self.store.add_application_snapshot({"application_id":application_id, "stage":"FORM_FILLED", "source_url":url, "form_fields":[{k: field.get(k) for k in ("label","input_type","value","selector","fact_id")} for field in fields]})
                        self.store.add_application_event(application_id, "FORM_FILLED", status="FILLED", details="Approved non-sensitive application fields were filled; final submission was not performed.", data={"fields": filled, "url": url})
                    return {"status": "FILLED", "url": url, "fields_filled": filled,
                            "submit": "NOT_PERFORMED"}
        except BrowserReadError:
            raise
        except Exception as exc:
            add_audit(self.store, "security", "Application preparation blocked", status="BLOCKED", data={'application_id':application_id,'reason_code':'FORM_OR_BROWSER_CHECK_FAILED'})
            return {"status": "BLOCKED", "reason": "The current page or fields could not be safely matched to the approved action. No submission was performed."}
