# Production PWA route ownership

Production uses one certificate-backed HTTPS origin so Owner, Staff and Job Agent clients retain same-origin Chief authentication, Secure/HttpOnly/SameSite cookies and CSRF semantics without CORS or cross-domain session sharing.

The production TLS/static routing boundary is fixed as follows:

- `/api/*` → Chief backend on its private loopback listener. Never cache these responses at the proxy or service-worker layer.
- `/owner/*` → built `chief-owner-pwa` static assets. Owner manifest, start URL and service worker scope are `/owner/`.
- `/staff/*` → built `chief-staff-pwa` static assets. Staff manifest, start URL and service worker scope are `/staff/`.
- `/jobs/*` → built `Job-agent-pwa` static assets. Job Agent manifest, start URL and service worker scope are `/jobs/`.

The origin root must not publish a PWA service worker at `/sw.js` and must not publish a manifest with scope `/`. A root-scoped worker could control multiple applications and is therefore a deployment failure. Each client must remove any older root-scoped worker registration before installing its own scoped worker.

The reverse proxy/static server installed during live deployment must preserve this route ownership, serve the three static bundles without fallback across app prefixes, and send Chief-bound requests only to the loopback application listener with the verified HTTPS scheme. DNS, certificate, static bundle locations and proxy software are deployment-time inputs and are not selected by this repository phase.

## Job Agent Web Push

The Job Agent PWA uses the existing authenticated notification API and same-origin CSRF boundary to register a browser Web Push subscription. Subscription endpoint/key capability material is retained only in Chief's private notification-preference state and is not returned by the normal preferences response.

Chief sends only `ACTION_REQUIRED` and `URGENT` Job-domain notifications through Web Push. `INFO`/ALLOW activity remains quiet and is available through the PWA's recent-activity view. Each registered device keeps a durable notification cursor so transient provider failures are retried rather than silently advancing past an undelivered alert.

Production Web Push requires:

- `WEB_PUSH_VAPID_PUBLIC_KEY` — public VAPID application-server key;
- `WEB_PUSH_VAPID_SUBJECT` — `mailto:` or HTTPS contact URI;
- `WEB_PUSH_VAPID_PRIVATE_KEY_REF` — private-key reference resolved for consumer `jobs.web-push` in domain `jobs`.

Plaintext `WEB_PUSH_VAPID_PRIVATE_KEY` is development/preview compatibility only; production secret resolution refuses it. Browser-supplied push endpoints are restricted to known Apple, Google, Mozilla and Windows push-service hosts unless an administrator explicitly adds exact hosts with `WEB_PUSH_ALLOWED_HOSTS`.
