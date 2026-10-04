# Job Agent v25

v13 builds on v12 without removing the existing architecture.

## New in v13
- Local SQLite state store with no database server or cloud dependency.
- Persistent records for jobs, applications, actions/approvals, notifications, sources, commands, reports, and worker status.
- Dashboard pages now read live state from SQLite instead of placeholder counts.
- Dashboard command center stores commands with IDs and worker status.
- Dashboard approval/rejection controls update pending action records; they do not execute computer actions or bypass the Policy Gate.
- Configured sources are seeded into the local source registry as `REVIEW`, not automatically trusted.
- Optional Linux desktop notifications via `notify-send`, with a safe no-op fallback if unavailable.

## Safety boundary
The dashboard is a control surface, not a policy bypass. High-impact actions remain subject to the Policy Gate and user approval. AI output is not treated as authoritative candidate fact storage.

## Run locally
```bash
python dashboard_app.py
```
Then open `http://127.0.0.1:8765/` in a browser.

The SQLite file is `database/worker.db` and is intentionally local.

## Tests
```bash
pytest -q
```

## v14 command center

The dashboard command center now feeds a real worker pipeline. Run the dashboard and worker separately:

```bash
python dashboard_app.py
python -m worker.runner
```

Commands are persisted in SQLite, interpreted by the Qwen-first/Mistral-free-fallback gateway, converted into a strict JSON action plan, and checked by the Policy Gate before execution. High-impact actions become approval requests instead of executing.

## Cross-platform desktop notifications

`notifications/desktop.py` now supports:
- Linux: `notify-send` when installed.
- Windows 10/11: built-in PowerShell Windows toast notifications, with no Python notification package required.

If a desktop notification mechanism is unavailable, the dashboard/database notification remains the fallback.

## v15 — live read-only browser discovery
The worker now performs actual read-only browser discovery from approved HTTPS targets. Dashboard commands can execute `discover_jobs`, `read_job_listing`, and `verify_source` through the existing Policy Gate. Page text is treated as untrusted data and is converted into structured jobs by the configured free AI gateway. HTTP targets are quarantined without visiting; unknown HTTPS domains are placed in REVIEW and are not visited. Extracted jobs are normalized, scam-screened, matched, AI-analyzed, and persisted to SQLite.

## v16 — source discovery and verification boundary
The command center can now ask the AI to discover additional job platforms and direct company career sites. LinkedIn and Upwork remain important but are not treated as the complete universe. AI-discovered sources are persisted in the source registry.

Discovery is **not** legitimacy proof:
- HTTPS discovered source -> `REVIEW` until identity/legitimacy is independently verified.
- HTTP/non-HTTPS -> `HTTP_QUARANTINED` and never visited.
- Only approved sources can reach the browser worker.

This keeps the browser worker from treating an AI suggestion as permission to visit an unknown site.

## v17 — deterministic job ranking and deduplication
v17 adds a conservative ranking/identity layer. Job URLs are canonicalized to remove common tracking parameters, and exact duplicate identities are detected before a job is treated as a new opportunity. Duplicate records are retained for auditability and marked `DUPLICATE` rather than silently deleted.

Ranking is deterministic and AI-independent. It considers role fit, matching confidence, scam-screen status, remote preference, compensation presence, and junior/entry versus senior-level signals. AI analysis may explain a job but does not control the final ranking score.

Existing v13-v16 SQLite databases are migrated in place with the new job fields.

## v18 — truthful application drafting
- Adds schema-validated CV and cover-letter generation.
- Candidate claims are restricted to KNOWN/VERIFIED evidence.
- Drafts are stored in SQLite and under `candidate/application_history/drafts/`.
- Draft generation is low-risk; submission remains an approval-gated high-impact action.

## v19 additions
- Local reusable CV library with role/application-set variants and historical CV records.
- Explicit profile-link registry with enable/disable/remove controls and HTTPS quarantine for HTTP links.
- Periodic monitoring task definitions for job-market refresh and authorized profile refresh.
- Full-system audit log separate from the dashboard's normal job/activity views; daily TXT audit can be generated and sent through the existing email/TXT reporting channels.
- Candidate CV facts remain governed by verified candidate evidence; uploaded CVs do not automatically make claims true.

### v19 operating model
- **CV Library:** upload reusable CVs and label them by role type and variant. The worker can select the closest active variant for an application. Historical records remain visible; removal is explicit.
- **Profile Links:** add, disable, or remove links that the user explicitly authorizes for periodic checking. HTTPS is required for automatic access; links are not treated as credentials.
- **Periodic monitoring:** the worker seeds daily job-market refresh and weekly profile-refresh tasks. These create normal dashboard commands, so they use the same AI → Policy Gate → worker path as manual commands.
- **Full-system audit:** a separate audit stream records AI, command, policy, worker, browser, candidate, profile, monitoring, notification, and error activity. The daily full audit is written as TXT and can be emailed when SMTP settings are configured.

## v20 — Candidate fact governance

A personally uploaded CV is evidence, not an automatic source of truth. Claims found in an uploaded CV remain `PROPOSED` until the user explicitly confirms them. Confirmed candidate facts remain authoritative until the user explicitly revokes/corrects them; deleting or replacing a CV does not silently remove a confirmed fact.

Candidate fact statuses:
- `PROPOSED` — extracted/suggested, not safe to claim.
- `USER_CONFIRMED` — explicitly confirmed by the user and safe for candidate claims.
- `REVOKED` — explicitly withdrawn by the user.

The dashboard has a Candidate Facts control page and records user changes in the whole-system audit log. Application drafting can use trusted `KNOWN`/`VERIFIED` facts already present in the candidate fact store and explicit `USER_CONFIRMED` facts, but an uploaded CV alone cannot promote a claim into either category.

## v21 — Controlled application execution

v21 adds a deliberately narrow browser-mutation layer for application forms.

- `fill_application_form` is a high-impact action and requires explicit approval.
- Only HTTPS application pages are eligible.
- Only non-sensitive `text`, `email`, `tel`, `url`, and `textarea` fields may be filled.
- Passwords, OTPs, payment/bank/card/security-code fields, uploads, login, clicks, and submission are not performed.
- Each filled value must reference a `USER_CONFIRMED` candidate fact and literally match text supported by that fact.
- The dashboard's **Approve & Execute** path re-checks the Policy Gate before mutation.
- Final application submission remains a separate safety stage; v21 does not submit applications.

## v21 — Controlled application execution

v21 adds the first browser-mutation layer for job applications, but deliberately stops short of final submission.

### Safety boundary
- `fill_application_form` is a high-impact action and therefore requires explicit user approval.
- The dashboard approval button is **Approve & Execute** and re-checks the Policy Gate before mutation.
- Only HTTPS pages on explicitly approved source domains may be mutated.
- Only non-sensitive `text`, `email`, `tel`, `url`, and `textarea` fields are eligible.
- Passwords, OTPs, payment/bank/card/security-code fields, credentials, uploads, login, clicks, and final submission are not performed.
- Every value must reference a `USER_CONFIRMED` candidate fact and literally match that fact's text. AI inference and unconfirmed CV claims cannot supply form values.
- The executor deliberately performs no submit/click after filling.

Final application submission remains a separate safety stage and is not implemented in v21.


## v22 Application Archive

The dashboard now includes a dedicated Application Archive page. Each application can retain historical snapshots of the CV variant used, the generated CV content, cover letter, safe form fields, source URL, and application events. Later final-submission execution will use the same record so the exact submitted structure can be audited without changing historical CV provenance.

## v23 — Final submission boundary

Final submission is now implemented as a separate, explicit high-impact stage. It requires an approved `submit_application` action and re-runs policy/preflight immediately before clicking a submission control. The preflight requires an existing `FORM_FILLED` snapshot, an approved HTTPS source, and an optional exact form hash that must still match. Applications already marked `SUBMITTED` are idempotently blocked from duplicate submission.

The browser submit control must be a button/input whose visible label clearly indicates application submission; destructive, transactional, offer-acceptance, account-management, and similar controls are rejected. Authentication/OTP/password challenges are never bypassed. Submission creates a receipt, immutable-style historical snapshot/event, and whole-system audit entry.

## v24 — Recovery, retries, and duplicate-submission protection

v24 adds a conservative recovery layer around final application submission.

- Every final-submission attempt is persisted in SQLite.
- Pre-submit failures may be retried, with a bounded retry limit.
- Ambiguous outcomes (for example, a connection loss after the submit click) are **never automatically retried** because the application may already have been submitted.
- Applications expose `recovery_status` and `recovery_reason`.
- Recovery decisions are written to the whole-system audit log.
- The existing v23 final-submission gate remains in force.

Use `worker.reliable_submission.ReliableFinalSubmissionExecutor` when recovery-aware submission is desired.

### User-requested retry control
Failed application records now expose a recovery panel in the Application Archive. For a known, bounded pre-submit failure, the dashboard offers **Ask worker to retry application**. Ambiguous outcomes such as `SUBMISSION_UNKNOWN` never expose an automatic retry control; they remain `ASK` until resolved. Retry requests preserve the original URL, submit selector, and form hash context and are fully audited.


## v25 — Autonomous scheduling and continuous operation
- Adds a dependency-free local scheduler with configurable polling and Africa/Lagos as the default timezone.
- Daily job-market refresh and periodic authorized-profile refresh remain normal SQLite commands and therefore use the same AI → Policy Gate → worker pipeline.
- The worker can continuously process both queued commands and user-approved high-impact actions.
- Approved actions are atomically claimed so two worker processes cannot execute the same action concurrently.
- Crashed `EXECUTING` actions are returned to `APPROVED` on startup rather than silently treated as complete.
- Daily full-system audit delivery is scheduled locally and persisted as a report so restarting the worker cannot send duplicate daily audits.
- Scheduler failures are audited and do not terminate the worker loop.

### v25 run
```bash
python -m worker.runner
```
The worker remains local and free of a cloud scheduler. Use `WORKER_POLL_SECONDS`, `SCHEDULER_POLL_SECONDS`, `JOB_WORKER_TIMEZONE`, `FULL_AUDIT_HOUR`, and `FULL_AUDIT_MINUTE` to tune operation.

## v26 — Email delivery configuration boundary
Email delivery is provider-neutral. SMTP is a transport protocol, not a mailbox/service by itself. The user chooses the email provider/account and supplies its SMTP settings. Supported presets are `custom_smtp`, `gmail`, `outlook`, and `yahoo`; custom SMTP allows any compatible provider.

`SMTP_PASSWORD` is read only from the process environment and is never returned by the dashboard or stored in SQLite. `EMAIL_RECIPIENT` is deliberately not hard-coded because the destination mailbox must be chosen by the user.

The local TXT/full-audit delivery remains independent of email, so the worker can still produce the complete daily audit even when email is not configured or temporarily unavailable.

## v27 — End-to-end simulation & security hardening

v27 adds a controlled end-to-end simulation harness plus security hardening:
- recursive audit secret redaction;
- HTTPS URL canonicalization and bounded host matching;
- selector safety checks;
- SQLite WAL/foreign-key/busy-timeout settings;
- deterministic failure scenarios for unconfirmed candidate facts, page-load failure, and ambiguous submission;
- regression tests proving ambiguous submissions are never classified as safe retries.

The simulator uses fake browser behavior and does **not** contact real employers.

## v28 — Controlled end-to-end rehearsal
v28 adds a non-network end-to-end rehearsal suite over the v27 system. It exercises the critical chain with fake providers/browser behavior rather than contacting real employers.

The rehearsal verifies:
- policy blocks for money/secrets and approval requirements for submission;
- HTTP quarantine and unverified HTTPS source review;
- job normalization/matching/scam screening/ranking boundaries;
- Qwen → Mistral free-only fallback and hard stop when both free routes fail;
- uploaded-CV claims remain unusable until explicitly `USER_CONFIRMED`;
- controlled non-sensitive form filling;
- application archive `FORM_FILLED` provenance and exact form hash;
- final submission approval boundary, receipt recording, and duplicate-submit blocking;
- ambiguous post-click outcomes remain `ASK` and are never retried automatically;
- safe pre-submit failures produce bounded retry decisions;
- recursive audit secret redaction;
- notification-channel failure does not prevent local delivery;
- daily audit TXT remains available when email is unconfigured;
- crashed `EXECUTING` actions are restored to `APPROVED` on worker startup;
- final form-hash mismatch blocks browser submission.

Run:
```bash
python e2e_rehearsal.py
pytest -q
```
