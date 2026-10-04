# Security boundaries and acceptance limits

The supplied 2026-09-25 security assessment demonstrated defects on accepted I. Passing functional regression counts never closed those findings. The development fixes below need adversarial, compatibility and target evidence; this document does not certify a deployment or claim that all defects are eliminated.

## Automated browsing (SEC-01/02)

Managed and unmanaged readers, application preparation and final submission share one request policy. Automated navigation requires public HTTPS, the exact reviewed origin and port 443. HTTP-only sites need manual review; there is no insecure fallback. Every intercepted subresource is checked, with separately configured exact resource origins; broad parent-domain/CDN permission is not inferred. Only GET/HEAD are allowed while reading. GET itself can have server-side effects, so this is restricted browsing, not a guarantee of zero website effects.

Service workers, downloads, upstream WebSockets and unnecessary worker/WebRTC/WebTransport APIs are blocked. Routed WebSockets never call `connect_to_server`, so Playwright keeps them local; no synchronous close is attempted inside the handshake callback. See [Playwright routing](https://playwright.dev/python/docs/network) and [WebSocket routing semantics](https://playwright.dev/python/docs/api/class-websocketroute).

A per-operation loopback CONNECT proxy independently checks exact approved hosts, port 443 and all DNS answers. Private, loopback, link-local, multicast and mixed private/public answers are rejected before connection. Connections use a validated numeric address rather than resolving the hostname again, and the connected peer is checked. Limits: eight proxy handlers, bounded socket timeouts, 40-second tunnel lifetime and 16 MiB transfer budget. QUIC/nonproxied WebRTC and DNS prefetch are disabled. Redirect routing limitations do not grant another origin proxy access. Tests cover denied real local proxy connections and pinned-connector behavior; positive public-network qualification and OS-level browser containment remain deployment gates.

The proxy is not a kernel firewall or protection against a browser/OS exploit. Chromium process isolation, dedicated operating-system identities, complete transport containment and physical-target policy require deployment acceptance. Real website/CDN compatibility is unqualified; blocked functionality must be reviewed explicitly, not automatically allowlisted. No test uses a real provider account or external target.

## Form mutation (SEC-03)

Existing PolicyGate, explicit approval, confirmed-fact and source checks remain authoritative. Preparation and final submission revalidate the actual URL, main document, unique stable target, actual type/tag, visibility, editability, labels, metadata and form destination. Private values are filled with network disabled, including same-origin beacons. A captured-native DOM operation performs the final type/attribute checks and value write in one script turn, closing the check-to-fill race; page-supplied descriptions cannot disguise password fields. Replacements, changed fields and navigation fail closed. Final submission rechecks the stable submit control, persists SUBMITTING intent before any click and never replays an ambiguous outcome. Only the approved-origin submission phase permits POST after those gates. Website semantics and compromised browser engines cannot be certified from these fixtures.

## Sessions, secrets and process ownership (SEC-04/05/06)

Ten-minute human inactivity and eight-hour absolute session lifetime are enforced server-side. Passive authentication/polling does not renew activity. Reauthentication rotates the bearer and derived CSRF value atomically while preserving session identity/approval relationships. Existing current-role, revocation, payload and expiry rules remain. A client activity receipt is not cryptographic proof of a human; sensitive operations still need fresh reauthentication. See Foundation-Identity.md and Model-Setup.md.

Chromium and the visible-login child receive an explicit OS environment allowlist, not provider-key/proxy variables. Model keys remain encrypted in private instance storage and excluded from ordinary packages. Website state uses private temporary-file write, flush and atomic replacement. Native Windows interactive site sign-in is explicitly unqualified and directs the operator to the Ubuntu desktop; it no longer silently calls systemctl on Windows. OS-account compromise, credential reprovisioning and target ACL checks remain separate concerns.

One worker owns the canonical declared instance using a held OS file lock before opening its store or running recovery. A second worker is refused without touching live actions. Lock files are retained to avoid unlink/recreate ownership races; process death releases the OS lock. Shutdown requests stop new loop work, stop heartbeat and keep ownership until the heartbeat exits. Blocking operating-system I/O or an external operation may require the supervisor to terminate the process; unresolved effects remain REVIEW/unknown, never replayed. This is single-machine ownership, not distributed fencing or support for multiple aliases to one SQLite database.

## Evidence and residual work

Browser denials use bounded fixed reason codes and correlation IDs, excluding URL queries, request bodies and browser exception payloads. Store-backed events use existing audit_log; unmanaged-reader events use the security logger. Existing sensitive-data history stays in its domain and is not exported as test evidence.

The accompanying report must identify exact tested source hashes, original reproduced findings, negative/positive test names, interruptions/failures and repairs. Still open: full independent assessment, complete adversarial endpoint/role matrix, installed TLS/services, resource exhaustion, actual target recovery/power loss, provider qualification, dependency advisory triage and physical Folio acceptance. No ASVS conformity or exhaustive security certification is claimed.
