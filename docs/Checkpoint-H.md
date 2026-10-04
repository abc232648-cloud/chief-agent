# Checkpoint H — Chief UI / PWA foundation integration

H changes presentation and its authenticated, read-only projections. No schema migration, business-engine migration, real/Folio database operation, runtime selection or installation is included. G remains the software rollback baseline. Stop after H; I / Step 13 requires separate approval.

## Navigation and maturity

The separated dashboard uses Main, Domains, Work & Control and System groups. Groups collapse independently; opening one domain closes the other domain's navigation. Existing Job page IDs, forms, approval workflow, application archive and current minimal Farm workspace remain available. The keyboard-accessible sidebar, focus handling, skip link and responsive existing shell are retained/extended.

| Surface | Current H behavior |
|---|---|
| Overview / Domains | Filtered to domains the current human may read; shared aggregate endpoints are not exposed to scoped users. |
| Tasks & Automations | Existing Job schedules; installation-wide activity-summary controls only for installation administrators. No new automation engine. |
| Approvals | Existing Job actions; visible approval controls reflect resource-specific human/delegated authority. Actual writes still enforce recent authentication and Job PolicyGate. |
| Notifications / Reports / Audit | Existing global surfaces require global audit scope; no new cross-domain sharing policy. |
| System Health | Existing observed UNKNOWN / HEALTHY / DEGRADED / UNAVAILABLE, reasons and dependencies, shown separately from desired mode. |
| Component controls | Existing supported transitions only; preview displays dependent consumers, user supplies reason and confirms, backend validates the preview token again. Domain controls remain `agent_controls`. |
| Capabilities / Policy | Read-only catalog, dependency and policy/risk contracts. Job PolicyGate remains actual enforcement; Chief Policy comparison-only. |
| Models | Existing global ENABLED / SHADOW / DISABLED controls, current Job assignment and installation policy read views. No provider calls, assignment editor, paid-fallback decision or model qualification. |
| Shared Evidence | Domain-filtered recorded metadata, latest verification, contradictions and last recorded quality. No verification/confirmation write. Generic VERIFIED does not mean Job USER_CONFIRMED. |
| Decision Ledger | Up to 100 domain-filtered reference/decision metadata rows. No replacement of existing audit or application history, no narrative payload copying. |
| Runbooks | Up to 100 domain-filtered version-pinned run references/status. No run/start/resume editor; mature Job pipeline stays domain-owned. |
| Agent Runtime | Pinned G OpenClaw/Hermes candidates only; no executed qualification evidence loaded; NO_QUALIFIED_CANDIDATE_YET. No runtime install/open/select control. |
| Integrations | Explicitly limited existing website-session/email boundaries; no new connector installer. |
| Devices | Unavailable: no hardware/discovery/control implementation. |
| Updates | Staging-only G foundation; no activation/download/install UI or saved-plan UI. |
| Settings | Existing protected email settings. Native clients, public access, MFA/recovery remain open. |

## Server boundaries and privacy

New `/api/ui/context`, `/overview`, `/job-state` and `/approval-access` presentation routes authenticate the current session. Context lists only permitted registered domains. Job state excludes commands owned by other domains and does not expose shared worker messages or global notification counts. Resource-specific approval visibility includes valid exact-action delegation; visibility never grants permission.

`/api/ui/{capabilities,models,policies,runtime,integrations,devices,updates}` requires installation-wide `installation.manage`. Domain views `/api/ui/{evidence,ledger,runbooks}/{domain}` require `work.read` for that registered domain, with SQL domain filtering before serialization. These routes accept GET/HEAD only. Security/audit receipt writes and session last-seen updates are expected; views do not mutate authoritative business rows or schema. Projections do not establish agent contexts or give new capabilities to agents/models.

Existing mutation routes still require server-side permission checks, Origin/CSRF validation and reauthentication where specified. Desired controls and observed health remain independent. A stale component preview cannot authorize a change. Model disable continues to override assignments. No UI view can authorize an action through evidence quality, generic verification or a policy display.

Authentication failures remain fail-closed. Loopback/private binding, Host/Origin checks and CSP are unchanged. HTML, CSS, JavaScript and backend remain separate; new rendered data is escaped. The manifest, icons and PWA-first strategy are preserved. Network is required; H adds no service worker, offline private-data cache, background replay or native app. Browser install behavior is not certified by a static manifest check.

## Validation and rollback

Use fresh same-environment G/H comparisons; the supplied Linux G results are historical, distinct from Windows results. Relevant runnable H/security/architecture checks are repeated ten times; browser tests must actually run before browser acceptance can pass. Missing browser or symlink privileges stay OPEN. Full-suite tests must run on final frozen H source, with source hashes checked before and after. Test fixture migrations continue using the existing B.5 backup/quarantined-restore prerequisite.

For software rollback, stop dashboard/workers/schedulers and restore the exact G package (archive SHA-256 `45c1219abd107f8db4cafe5ac6d6b97c7ffdab722dc550cea108d135d5b8c33a`, source-tree `1db238e1107630b3ce511e7265effbc5f44c4d5abf04be251eee6fab6e3bc9ff`). H has no reverse schema migration. If selectively reverting UI changes, HTML/scripts/styles and the matched presentation/authentication route changes must move together; complete G software rollback is safer. Data changes made by an authorized human through controls remain operational state, not undone by source rollback. Preserve a protected recovery point and reconcile queues, approvals, models and controls before resuming external work. Quarantined restores remain quarantined.

Runtime qualification/selection, Mistral/free-only policy, actual browser/PWA acceptance, symbolic-link privileges, Docker/Folio, deployment backup/restore, real operational migrations, real providers, public remote/TLS/proxy trust, Owner/MFA/password recovery and update signing/activation remain OPEN. H does not begin I or real Farm implementation.
