from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from policy.rules import Decision
from .action_gate import ActionRequest, PolicyBlocked, ApprovalRequired, PolicyGate
from database.store_extensions import add_audit

DANGEROUS_SUBMIT_WORDS = {
    "delete", "remove", "withdraw", "cancel", "terminate", "close account",
    "accept offer", "accept job", "purchase", "pay", "checkout", "subscribe",
}
SUBMIT_WORDS = {"submit", "apply", "application", "send application", "finish"}

@dataclass(frozen=True)
class SubmissionPreflight:
    ok: bool
    reason: str
    application_id: str
    form_hash: str = ""


def canonical_form_hash(fields: list[dict[str, Any]]) -> str:
    safe = []
    for f in fields:
        safe.append({k: f.get(k) for k in ("label", "input_type", "value", "selector", "fact_id")})
    blob = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class FinalSubmissionExecutor:
    """Final-submit boundary. Submission is only possible after explicit approval.

    This layer is intentionally narrow: it submits an already prepared application
    at an approved HTTPS source, records a receipt, and refuses to bypass login,
    CAPTCHA, payment, OTP, credential, or destructive/offer actions.
    """
    def __init__(self, store, *, policy_gate: PolicyGate | None = None, browser_factory=None, candidate_fact_provider=None):
        self.store = store
        self.candidate_fact_provider = candidate_fact_provider or (lambda: store.candidate_facts(status="USER_CONFIRMED"))
        self.gate = policy_gate or PolicyGate()
        self.browser_factory = browser_factory

    def preflight(self, application_id: str, url: str, *, expected_form_hash: str | None = None) -> SubmissionPreflight:
        app = self.store.application_detail(application_id)
        if not app:
            return SubmissionPreflight(False, "Application record does not exist.", application_id)
        if app.get('status') in ('SUBMITTING', 'SUBMISSION_UNKNOWN'):
            return SubmissionPreflight(False, 'A prior submission may have reached the website. Verify it manually before any retry.', application_id)
        from browser.site_access import SiteAccess
        managed = SiteAccess(self.store).for_url(url)
        if managed and managed['status'] != 'ACTIVE':
            return SubmissionPreflight(False, 'Website access is not active.', application_id)
        if str(app.get("status", "")).upper() == "SUBMITTED" or app.get("submitted_at"):
            return SubmissionPreflight(False, "Application is already marked submitted; duplicate submission is blocked.", application_id)
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            return SubmissionPreflight(False, "Final submission requires an HTTPS URL.", application_id)
        host = (parsed.hostname or "").lower()
        approved = []
        for source in self.store.sources():
            if str(source.get("verification_status", "")).upper() != "APPROVED":
                continue
            h = (urlparse(str(source.get("url", ""))).hostname or "").lower()
            if h and (host == h or host.endswith("." + h)):
                approved.append(h)
        if not approved:
            return SubmissionPreflight(False, "Submission domain is not an approved source.", application_id)
        snapshots = app.get("snapshots", [])
        filled = next((s for s in snapshots if str(s.get("stage", "")).upper() == "FORM_FILLED"), None)
        if not filled:
            return SubmissionPreflight(False, "No FORM_FILLED application snapshot exists.", application_id)
        try:
            fields = json.loads(filled.get("form_fields_json") or "[]")
        except json.JSONDecodeError:
            return SubmissionPreflight(False, "FORM_FILLED snapshot has invalid field data.", application_id)
        from .application_executor import ApplicationExecutor
        if not fields or ApplicationExecutor(self.store, candidate_fact_provider=self.candidate_fact_provider).validate_fields(fields):
            return SubmissionPreflight(False, 'Reviewed form fields are empty or no longer supported by confirmed facts.', application_id)
        if filled.get('source_url') != url:
            return SubmissionPreflight(False, 'Submission URL differs from the reviewed form URL.', application_id)
        actual_hash = canonical_form_hash(fields)
        if expected_form_hash and expected_form_hash != actual_hash:
            return SubmissionPreflight(False, "The final form no longer matches the approved rendered application snapshot.", application_id, actual_hash)
        return SubmissionPreflight(True, "Final application is ready for explicit submission.", application_id, actual_hash)

    def _validate_submit_control(self, text: str, tag: str, input_type: str) -> tuple[bool, str]:
        low = " ".join(str(text or "").lower().split())
        if any(w in low for w in DANGEROUS_SUBMIT_WORDS):
            return False, "Submit control text appears destructive, transactional, or offer-related."
        if tag.lower() not in {"button", "input"}:
            return False, "Final submission control must be a button or input control."
        if tag.lower() == "input" and input_type.lower() not in {"submit", "button"}:
            return False, "Input control is not a submit/button control."
        if not any(w in low for w in SUBMIT_WORDS):
            return False, "Submit control text does not clearly indicate application submission."
        return True, "OK"

    def submit(self, application_id: str, url: str, submit_selector: str, *, approved: bool = False,
               expected_form_hash: str | None = None) -> dict[str, Any]:
        request = ActionRequest("submit_application", {
            "application_id": application_id, "url": url, "submit_selector": submit_selector,
            "expected_form_hash": expected_form_hash or ""
        })
        decision = self.gate.decide(request)
        if decision.decision == Decision.BLOCK:
            raise PolicyBlocked(decision.reason)
        if decision.decision == Decision.ASK and not approved:
            raise ApprovalRequired(decision.reason)

        pre = self.preflight(application_id, url, expected_form_hash=expected_form_hash)
        if not pre.ok:
            add_audit(self.store, "application", "Final submission preflight blocked", status="BLOCKED", details=pre.reason,
                      data={"application_id": application_id, "url": url, "form_hash": pre.form_hash})
            return {"status": "BLOCKED", "reason": pre.reason, "application_id": application_id, "form_hash": pre.form_hash}

        if not submit_selector:
            return {"status": "BLOCKED", "reason": "A precise submit selector is required.", "application_id": application_id}

        from worker.approval_context import revalidate as revalidate_approval
        revalidate_approval()
        clicked = False
        try:
            if self.browser_factory:
                result = self.browser_factory(url, submit_selector)
            else:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    from browser.site_access import SiteAccess
                    from browser.request_policy import controlled_browser
                    from .form_guard import fill_reviewed,install_writer
                    access = SiteAccess(self.store)
                    managed = access.for_url(url)
                    def revalidate():
                        revalidate_approval()
                        if managed:access.assert_active(managed['domain'],managed['revision'])
                        if not self.preflight(application_id,url,expected_form_hash=expected_form_hash).ok:
                            raise ValueError('Reviewed application authority changed.')
                    with controlled_browser(p,url,storage_state=access.state(managed['domain']) if managed else None,
                            revision_check=(lambda:access.assert_active(managed['domain'],managed['revision'])) if managed else None,
                            store=self.store) as (context,network_policy):
                        writer=install_writer(context)
                        page = context.new_page()
                        page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                        if urlparse(page.url).hostname != urlparse(url).hostname:
                            return {'status':'BLOCKED','reason':'Application redirected to a different domain.'}
                        # Never proceed through authentication or verification challenges.
                        sensitive = page.locator('input[type="password"], input[name*="otp" i], input[name*="verification" i]')
                        if sensitive.count() > 0:
                            return {"status": "REVIEW", "reason": "Authentication/verification fields are present; worker will not bypass them.", "application_id": application_id}
                        control = page.locator(submit_selector)
                        if control.count() != 1:
                            return {"status": "FAILED", "reason": "Submit control was not found.", "application_id": application_id}
                        control=control.element_handle()
                        tag = control.evaluate("el => el.tagName")
                        input_type = control.get_attribute("type") or ""
                        text = control.inner_text() if str(tag).upper() == "BUTTON" else (control.get_attribute("value") or "")
                        ok, reason = self._validate_submit_control(text, tag, input_type)
                        if not ok:
                            return {"status": "BLOCKED", "reason": reason, "application_id": application_id}
                        submit_identity=(tag,input_type,text,control.get_attribute('formaction'))
                        fields = self.store.application_form_fields(application_id)
                        unreviewed = page.locator('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]), textarea, select').evaluate_all(
                            '(nodes, selectors) => nodes.some(n => !selectors.some(s => n.matches(s)))', [str(f.get('selector') or '') for f in fields])
                        if unreviewed:
                            return {'status':'BLOCKED','reason':'The page contains form controls absent from the reviewed snapshot.'}
                        from .application_executor import ApplicationExecutor
                        fill_reviewed(page,context,network_policy,url,fields,ApplicationExecutor._is_safe_field,writer=writer,revalidate=revalidate)
                        if any(page.locator(str(f['selector'])).input_value() != str(f['value']) for f in fields):
                            return {'status':'BLOCKED','reason':'Rendered form values differ from the reviewed application.'}
                        confirmations = ('Your application has been submitted', 'Application successfully submitted', 'We have received your application')
                        before = page.locator('body').inner_text()
                        if any(text in before for text in confirmations):
                            return {'status':'REVIEW','reason':'Page already shows a submission confirmation; check for an existing application.'}
                        if managed:
                            access.assert_active(managed['domain'], managed['revision'])
                        current=page.locator(submit_selector)
                        if (current.count()!=1 or not control.evaluate('(e,other)=>e===other',current.element_handle())
                                or not control.is_visible() or not control.is_enabled()
                                or (control.evaluate('el=>el.tagName'),control.get_attribute('type') or '',
                                    control.inner_text() if str(tag).upper()=='BUTTON' else control.get_attribute('value') or '',
                                    control.get_attribute('formaction'))!=submit_identity):
                            return {'status':'BLOCKED','reason':'The submit control changed after inspection.'}
                        revalidate()
                        # Persist intent before the click, so a crash cannot make retry look safe.
                        with self.store._connect() as con:
                            changed = con.execute("UPDATE applications SET status='SUBMITTING' WHERE id=? AND status NOT IN ('SUBMITTING','SUBMISSION_UNKNOWN','SUBMITTED')", (application_id,)).rowcount
                        if changed != 1:
                            return {'status':'BLOCKED','reason':'Another submission is already in progress or unresolved.'}
                        network_policy.phase='SUBMIT';context.set_offline(False)
                        clicked = True
                        control.click()
                        page.wait_for_load_state("domcontentloaded", timeout=15_000)
                        confirmation = ''
                        for _ in range(20):
                            after = page.locator('body').inner_text()
                            confirmation = next((text for text in confirmations if text in after), '')
                            if confirmation: break
                            page.wait_for_timeout(250)
                        if not confirmation or urlparse(page.url).hostname != urlparse(url).hostname:
                            with self.store._connect() as con:
                                con.execute("UPDATE applications SET status='SUBMISSION_UNKNOWN' WHERE id=?", (application_id,))
                            return {'status':'REVIEW','outcome':'SUBMISSION_UNKNOWN','reason':'Clicked, but no explicit on-site confirmation was found. Do not retry without checking the website.'}
                        result = {"status": "SUBMITTED", "url_after": page.url, "title_after": page.title(), 'confirmation': confirmation}

            if str(result.get("status", "")).upper() != "SUBMITTED":
                add_audit(self.store, "application", "Final submission did not complete", status=str(result.get("status", "FAILED")),
                          details=str(result.get("reason", "")), data={"application_id": application_id, "url": url})
                return result

            now = datetime.now(timezone.utc).isoformat()
            receipt = {
                "submitted_at": now,
                "url": url,
                "form_hash": pre.form_hash,
                "result": result,
            }
            self.store.mark_application_submitted(application_id, now, receipt)
            self.store.add_application_snapshot({
                "application_id": application_id,
                "stage": "SUBMITTED",
                "source_url": url,
                "form_fields": self.store.application_form_fields(application_id),
            })
            self.store.add_application_event(application_id, "SUBMITTED", status="SUBMITTED",
                                              details="Application submitted after explicit user approval and final preflight.", data=receipt)
            add_audit(self.store, "application", "Final application submitted", status="SUBMITTED", details=url,
                      data={"application_id": application_id, "form_hash": pre.form_hash, "receipt": receipt})
            return {"status": "SUBMITTED", "application_id": application_id, "receipt": receipt}
        except Exception as exc:
            if clicked:
                with self.store._connect() as con:
                    con.execute("UPDATE applications SET status='SUBMISSION_UNKNOWN' WHERE id=?", (application_id,))
                return {'status':'REVIEW','outcome':'SUBMISSION_UNKNOWN','application_id':application_id,'reason':'The browser failed after submission began. Check the website; automatic retry is blocked.'}
            add_audit(self.store, "security", "Final application submission blocked", status="BLOCKED",
                      data={"application_id": application_id, "reason_code":"FORM_OR_BROWSER_CHECK_FAILED"})
            return {"status": "BLOCKED", "application_id": application_id, "message": "The approved page or form could not be safely verified; no submit click was performed."}
