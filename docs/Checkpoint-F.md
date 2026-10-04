# Checkpoint F — isolated authentication and human authority foundation

F adds human identity/session authentication and server-side RBAC to the existing private dashboard. Human permission never adds an agent capability and does not replace Job PolicyGate, factuality, evidence, source access, component modes or action-risk checks.

This is an isolated development candidate. Real/Folio database migration and deployment backup/restore remain unaccepted. No migration runs at startup. `database.identity_migrations.migrate_isolated` requires the development marker, a pinned B.5 backup and verified quarantined restore of the unchanged E fixture. It adds eight tables and six append-only triggers in one transaction. Missing F storage causes protected APIs to return SETUP_REQUIRED; there is no anonymous fallback.

The worker entry point also requires valid F storage before recovering queues or constructing providers. Trusted in-process legacy unit adapters can still operate on pre-F schemas for compatibility testing; they are not an anonymous service startup mode or an OS security boundary.

After a separately verified **isolated** migration, `python -m identity --database <isolated-db> --username <owner-name>` prompts locally for the first Owner password. Bootstrap is available once; there is no web bootstrap or default credential. `/login` supplies sign-in and reauthentication, with a sign-out affordance on the existing dashboard. UI layout redesign is outside F.

## Permission boundaries

All domain operations require explicit membership, or Owner/Administrator `*` scope. Global aggregates and installation settings require `*`; a domain-scoped Administrator is not a global administrator.

| Operation | Owner | Administrator | Manager | Worker |
|---|---|---|---|---|
| Scoped read / queue request | Yes | Yes | Yes | Yes |
| Scoped non-destructive work management | Yes | Yes | Yes | No |
| Action approval, fact confirmation, application retry | Recent authentication | Recent authentication | Only an exact delegated action | Only an exact delegated action |
| Destructive work operations / component controls | Recent authentication | Recent authentication | No | No |
| Installation/model/email settings | Global scope + recent authentication | Global scope + recent authentication | No | No |
| Global audit/reports/notifications/aggregates | Global scope | Global scope | No | No |
| Create Owner/Administrator identities | Global scope + recent authentication | No | No | No |
| Create Manager/Worker identities | Global scope + recent authentication | Global scope + recent authentication | No | No |
| Delegate one action approval | Global scope + recent authentication | No | No | No |
| Issue emergency pause authority | Global scope + recent authentication | Global scope + recent authentication | No | No |
| Pause a scoped agent | Yes | Yes | Emergency grant only | Emergency grant only |
| Grant agent capability / runtime authority | No | No | No | No |

Manager can propose/edit facts, but cannot confirm them by default. An edit still returns a Job fact to PROPOSED. Domain-defined roles may only select scoped read/request/manage permissions and cannot replace built-in roles. No new Farm semantics are introduced.

Delegation expires within one hour, covers one `action:<id>` in one existing recipient domain, cannot be redelegated, and is revalidated against the issuer. It does not cover factual confirmation or application retry. Emergency authority expires within 15 minutes and permits only exactly `{"running": false}` for the scoped agent: no resume, disable, approval or policy bypass. Neither authority is self-asserted. Broader business delegation, emergency governance and Owner recovery remain open.

## Authentication and approvals

Passwords are salted scrypt hashes (N=16384, r=8, p=1). Session bearer tokens have 256 bits of randomness and only SHA-256 digests persist. Public session record IDs are correlation identifiers, not bearer credentials. Sessions expire after eight hours or 30 minutes idle; sensitive operations require password reauthentication within five minutes. Five failed sign-ins/reauthentications trigger a five-minute per-username limit. Disabled identities and revoked/expired sessions fail closed.

Cookies are HttpOnly/SameSite=Strict; Secure is added on a directly TLS-wrapped connection. Existing Host/Origin protections run before authentication. Every mutation requires same-origin Origin plus a session-bound CSRF token; login also requires same-origin Origin. The default remains 127.0.0.1. Wildcard/public binds are rejected. Trusted proxies, public exposure, TLS provisioning and shared-host hardening are not implemented or certified here.

An action approval records human ID, public session ID, exact action digest, authority and UTC receipt time atomically with legacy action state and its audit record. Dispatch revalidates authority, payload, live session and a 30-minute approval lifetime before the original PolicyGate. Existing approved rows remain intact during migration but need identified reapproval before F dispatch. If already claimed without valid approval they are BLOCKED, not silently replayed. Approval does not guarantee execution; existing Job gates still apply. Logout/revocation invalidates pending approval authority.

Security history stores references/codes rather than request bodies or credentials. Existing audit writes acquire human provenance in authenticated request context. Application retry authorization is captured in security history and existing application/audit history. Decision Ledger and application snapshots remain additive, unchanged systems. Operational backups containing password hashes, sessions and private domain data require protected storage; none belongs in source/evidence archives.

## Rollback boundary

Use the existing accepted E source archive and recorded hash; F creates no duplicate rollback archive. Stop dashboard, workers and schedulers first. Preserve a protected recovery point before changing software or data. E can read the additive database but ignores F authentication and human approval records: **do not start E against F work queues as an automatic rollback**. Reconcile controls, queued/approved actions and authority deliberately in isolated development, keeping external actions disabled. Software rollback is not a data restore. A B.5 restored database remains quarantined; F provides no unlock/promotion path. No reverse migration drops identity/audit history.

Browser, symlink privileges, Docker/Folio, operational backup/restore, real migration, real provider qualification and the inherited Mistral/free-only discrepancy remain OPEN. MFA, password reset/recovery, authenticated transport acceptance and wider delegated/emergency authority policy need separate acceptance. Stop at F.
