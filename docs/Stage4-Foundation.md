# Stage 4 development foundation

No SQL schema migration. Existing audit JSON receives reference metadata; legacy rows, CandidateFacts, snapshots, domain controls, PolicyGate and shared ledger schemas are unchanged. Accepted service baseline tree: `58e7b8b5ad1734a02f956080c32ddad067df68bde2747dba4321e76d52a2f5ff`.

## Storage and resource boundaries

Every Store connection explicitly requests and verifies WAL, synchronous=FULL, foreign_keys=ON, busy_timeout=10000 ms and wal_autocheckpoint=1000 pages. Failed configuration closes the connection. SQLite FULL improves commit durability subject to filesystem/hardware guarantees; neither a VM test nor a successful fsync certifies physical power-loss durability. Never put WAL databases on an unqualified network filesystem.

Configured service startup requires at least 64 MiB free on the database filesystem and a temporary write/flush/fsync probe. The probe is removed on success/failure. This is a startup observation, not a reservation, continuous disk monitor, database backup or permission to delete history. Disk-full and read-only errors still fail the current operation; unresolved execution intent prevents blind replay. WAL may grow while long readers hold snapshots. Operational disk sizing/monitoring and controlled checkpoint maintenance remain deployment responsibilities.

Per-component diagnostics retain one 1 MiB active log and three rotated files: at most about 4 MiB per component, 12 MiB for three components. Only allowlisted fixed codes plus generated correlation IDs are accepted. Exception values, paths and payloads are discarded. Private parent permissions remain mandatory. OS journal/Task Scheduler retention is a separate deployment setting. Audit/Decision Ledger/application history is never rotated or deleted by this diagnostic policy; authorized archive/retention policy remains open.

## Actions and traceability

A single retained OS lock serializes approved-action execution within the database instance, including direct processor calls. An APPROVED row becomes EXECUTING before the effect. Competing calls do not change the running row. Existing restart recovery moves uncertain actions to review rather than replay. This is not cross-machine coordination or exactly-once delivery.

Queued identified approvals are rechecked during browser field mutation and immediately before final submission: current session/identity, expiry, action/payload digest, role/delegation, scoped component/capability and existing Job PolicyGate. Existing evidence/source/managed-session/form guards remain in place. Revalidation and a remote site's effect are not one atomic transaction; distributed revocation cannot undo a completed external effect. Direct domain-owned executor APIs retain their existing explicit approval contracts; the new context only adds restrictions.

Generated HTTP request IDs flow to audit JSON. Queue receipt audit rows connect request ID to command ID; existing command/action/application references connect worker policy/approval/outcome records. Job ledger invocation IDs are attached to audit entries within that invocation. IDs correlate records; they are not bearer credentials or permission. Contexts reset across exceptions and threads. No global cross-domain search or new ledger is introduced. Repeated HTTP commands are separate user requests; transport does not retry mutations automatically.

## Release inputs

`release/*-inventory.json` records actual interpreter, SQLite, package and Playwright browser-manifest versions. Platform hashed requirements pin exact observed versions and published non-yanked wheel SHA-256 values. Use a NEW isolated Python 3.12 environment and `python -m pip install --require-hashes --only-binary=:all: -r release/<platform>-hashed.txt`. Browser installation remains a separate Playwright-managed artifact; inspect recorded revision/version. Do not install these files into an operational instance automatically.

These inputs support repeatable dependency selection; they are not a reproducible CPython/OS build or a signed supply-chain attestation. Source ZIP/manifest verification remains mandatory. Public advisory lookup results and fresh environment validation belong in delivery evidence. Known-advisory absence does not prove absence of vulnerabilities; repeat checks before deployment. No provider model/routing changes or paid fallback are introduced.

## Rollback and open gates

Stop services; preserve data and unresolved intent; switch software to the accepted service baseline. No down migration is needed. Do not clear EXECUTING/REVIEW/SUBMITTING/SUBMISSION_UNKNOWN to make a retry possible. Do not promote quarantined restores. The older baseline lacks the new action guard and diagnostics: keep high-impact work stopped until reviewed.

Deployment identity/permissions, OS containment, TLS/backend isolation, operational log retention/disk monitoring, physical storage behavior, boot/logout, long-operation shutdown, real backup/restore recovery targets, provider/browser/Folio/Docker validation, remote/shared-device MFA choice and runtime qualification remain OPEN. Stage 4 does not authorize deployment or later stages.
