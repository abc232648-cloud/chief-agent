# A10 — Cross-PWA contract and security verification

Chief's Owner, Staff, and optional Job Companion consumers share one authenticated
API origin. A client label, route, cached interface declaration, or claimed role
never grants authority. A10 tests the current Chief HTTP contract and browser
session behavior against the real dashboard server and disposable databases.

## Contract coverage

| Boundary | Regression coverage |
| --- | --- |
| Discovery | Exact context and interface fields; Owner/Administrator, Manager, Worker and domain-scope matrix; undeclared Job Staff interface remains absent |
| Owner | Scoped domain views, installation-control denial for Staff, recovery controls retained after module exposure is revoked |
| Staff | Task assignment isolation, rejected cross-person writes, idempotent replay and stale-revision rejection through HTTP |
| Companion | Exact feed/event/count fields, bounded feed, raw-command and cross-domain privacy, Owner/Administrator-only access |
| Sessions | Missing credentials, logout/revocation, disabled identities, shared Owner/Companion session revocation and independent Staff sessions |
| Request integrity | CSRF, same-origin and cross-site checks on Farm and Job mutations, with persistence assertions proving rejected requests changed no work records |
| Exposure | Cached discovery cannot bypass disabled, incompatible, missing or replaced modules; direct GET and HEAD checks and current discovery agree |
| Browser cache | API responses use `no-store`; browser-local stale metadata cannot restore server access; disabling and re-enabling takes effect without recreating the browser session |

Existing notification audience/deep-link, canonical Staff role, identity, A7/A8/A9,
and production routing tests run alongside the new cross-PWA checks. The full
suite continues to exercise the built-in Farm portal, offline queue, and service
worker regressions.

## Running verification

Install `requirements-test.txt` and Playwright Chromium, then run:

```sh
python -m pytest -q tests/test_cross_pwa_contract.py tests/test_cross_pwa_security.py tests/test_cross_pwa_browser.py
python -m pytest -q
```

Cross-PWA Contract and Security CI runs focused checks and the complete suite,
including Chromium acceptance tests, for changes to the relevant boundaries.
The CI-only portable secret backend is inherited from existing test practice;
production secret handling is unchanged.

## Scope of the evidence

These tests exercise Chief's current consumer-facing backend contract and real
browser requests. They do not build or certify the separate `chief-owner-pwa`,
`chief-staff-pwa`, or `Job-agent-pwa` repositories, and do not claim live TLS,
reverse-proxy, phone-installation, or background-push qualification. Production
bundle and service-worker scopes remain governed by [PWA Routing](PWA-Routing.md).
The browser test uses same-origin test pages and separate authenticated browser
contexts; it is not an end-to-end test of the external PWA bundles.
