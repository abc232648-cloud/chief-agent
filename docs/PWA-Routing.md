# Production PWA route ownership

Production uses one certificate-backed HTTPS origin so Owner and Staff clients retain same-origin Chief authentication, Secure/HttpOnly/SameSite cookies and CSRF semantics without CORS or cross-domain session sharing.

The production TLS/static routing boundary is fixed as follows:

- `/api/*` → Chief backend on its private loopback listener. Never cache these responses at the proxy or service-worker layer.
- `/owner/*` → built `chief-owner-pwa` static assets. Owner manifest, start URL and service worker scope are `/owner/`.
- `/staff/*` → built `chief-staff-pwa` static assets. Staff manifest, start URL and service worker scope are `/staff/`.

The origin root must not publish a PWA service worker at `/sw.js` and must not publish a manifest with scope `/`. A root-scoped worker could control both applications and is therefore a deployment failure. Both clients contain migration cleanup for their older root-scoped worker registration before installing their scoped worker.

The reverse proxy/static server installed during live deployment must preserve this route ownership, serve the two static bundles without fallback across app prefixes, and send Chief-bound requests only to the loopback application listener with the verified HTTPS scheme. DNS, certificate, static bundle locations and proxy software are deployment-time inputs and are not selected by this repository phase.
