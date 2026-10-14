# Paired client acceptance (in progress)

The actual Owner, Staff and Job production bundles are served on one disposable
loopback TLS origin against Chief's real HTTP server and synthetic database.
Set `CHIEF_A10_CLIENT_ROOT` to the directory containing the three built client
checkouts. Missing bundles fail an opted-in run; the default suite reports a skip
when no client builds were supplied. `CHIEF_A10_BROWSER` selects chromium,
firefox or webkit. This fixture is not a production reverse proxy.

The production route contract reserves exactly three non-overlapping static PWA
prefixes on that shared origin: `/owner/`, `/staff/` and `/jobs/`. Their service
workers are scoped only to those prefixes. `/api/` remains a non-cacheable Chief
backend route and no root-scoped PWA is permitted.

Run `python -m pytest tests/test_a10_paired_clients.py tests/test_a10_operational_chain.py` after each client's
`npm ci` and production build. Notification transport tests use an explicit
no-network spy; browser/API tests do not substitute mock backend responses.
The lost-response test lets the real backend accept a report and then discards
the transport response, verifying durable replay and server deduplication.

## WebKit harness and scope

WebKit uses a fresh persistent profile under the test's temporary directory.
The tested private-context runtime rejects IndexedDB Blob writes, whereas its
persistent profile accepts them. No real browser profile, credentials or farm
data is used. This qualifies the synthetic regular-profile workflow, not private
browsing or physical Safari/iPhone storage behavior.

WebKit network loss is injected by closing requests at the isolated TLS proxy
before serving static content or forwarding to Chief. The browser can still
read local selected files and retains its service worker. `navigator.onLine`
remains true, as with an unreachable server on an otherwise connected device.
The runtime's offline-emulation API causes local File reads to fail and can leave
worker fetches broken after restoration, reproduced without application code.
Chromium and Firefox retain browser-level offline emulation. Neither method
substitutes success responses, changes production trust or disables the sandbox.
Real device airplane-mode and restart checks remain separate acceptance work.

The transport fixture regression proves both API and static requests are blocked
before backend access and verifies restoration. Photo hashes/bytes, durable
queuing, reassignment denial and exactly-once report assertions remain in place.

## Security corrections

Owner and Staff background polling now checks session validity without extending
idle lifetime. Only trusted, visible human interaction requests an activity
receipt, throttled to once per minute. Chief still enforces ten-minute idle and
maximum session lifetime. Cross-tab Job account switching clears previous data;
cached client role metadata never grants access.

Push subscriptions bind to the registering Chief identity and session in existing
JSON persistence; there is no table/schema migration. Two delivery modes remain
separate:

- ordinary session-bound registration rechecks the current Chief session and
  therefore stops when that session expires;
- explicit `after_timeout` device consent may continue only generic Job ASK/STOP
  wake-up notifications after idle timeout. It is a device-delivery consent, not
  an authenticated application session. Delivery does not update `last_seen`,
  restore API access or include private notification title/body text. The payload
  tells the user to sign in to view the update.

Both modes still fail closed on sign-out/session revocation, disabled identity,
role or domain-scope changes, loss of Job companion exposure, or invalid device
consent. Each attempted delivery rechecks the relevant Chief authority before the
external send. Unbound legacy subscriptions remain inert until explicitly
registered again. Pending notification rows are preserved when delivery is no
longer authorized.

The Job PWA must present the same distinction: continuing a generic wake-up under
explicit device consent never means the user remains signed in. Private Job data
continues to require a current authenticated Chief session.

## CI and release boundary

The manual paired workflow requires immutable full client commit SHAs and a
read-only private-repository token. It must be run after all four reviewed
candidate revisions are available upstream. It is not yet an automatically
triggered cross-repository release gate. Local client import commits are not
upstream identities. Existing full backend/client suites remain required.

Windows and Linux results, browser engine results, emulated viewport checks and
actual Android/iPhone installation results must be reported separately. Real
devices, installed-host TLS, external push delivery, operational restore and the
final security assessment remain independent acceptance gates.

## Reconciliation on the Phase 9 baseline

Reconciled unique contracts from A10 commit `2a1b50ecceb280051d45e18897868231f6224e01` onto Phase9 `0e51408323c112615f0b2d4403fa8b9aba3d5562`. Its standalone workflow was not adopted: the existing paired-pwa-ci workflow already builds pinned clients and runs actual Chromium/Firefox/WebKit bundles. Its raw-fetch browser scenario overlaps module-exposure and paired lifecycle/logout tests. Its general missing/revoked session and CSRF cases overlap identity HTTP tests.

The retained `test_a10_http_contracts.py` adds exact HTTP consumer schema/cache/privacy checks, broader role/domain interface matrices, stale discovery over GET/HEAD with forged claims, task object/replay/revision isolation over HTTP and current-role downgrade enforcement. It shares production fixtures/authority and joins the existing workflow, with no second contract engine or workflow. No production interface change is implied by these tests.
