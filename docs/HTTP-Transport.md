# Private dashboard transport

The dashboard uses Waitress 3.0.2 through a WSGI adapter. Existing route handlers still perform authorization, CSRF, policy, approval and evidence checks. They assemble bounded responses in memory and never write to a client socket. A delivery failure does not re-enter a route, undo committed work or retry a mutation. Application exceptions remain errors; even a business-level `ConnectionAbortedError` produces a redacted 500 rather than being silently treated as successful delivery.

## Binding and TLS trust

Default development/test listener: `DASHBOARD_HOST=127.0.0.1`, `DASHBOARD_PORT=8765`. Isolated test/development may use direct loopback HTTP. `localhost` resolves deliberately to one IPv4 loopback listener. Public/wildcard binds are refused.

**Production is HTTPS-only, including requests originating on loopback.** A reviewed production instance configuration must supply `public_host` and one explicit loopback `trusted_tls_proxy`. `deployment.launch` fixes the Chief backend listener to `127.0.0.1`, exports only the validated proxy/host values, and refuses production startup when the HTTPS contract is missing or ambiguous. The TLS terminator is installed and qualified separately during deployment; application configuration never contains TLS private keys or credentials.

The same-host TLS reverse proxy must terminate certificate-verified TLS, forward only to the loopback Chief listener, preserve the original Host, overwrite `X-Forwarded-Proto` with the actual HTTPS client transport, and prevent direct backend access by untrusted local processes. Only the configured loopback peer's `X-Forwarded-Proto` may establish the scheme. Forwarded Host/client identity/port and other forwarding headers confer no authority and are removed. Host and Origin checks still apply.

Production HTTP is rejected before application dispatch, even when the peer is `127.0.0.1`. This prevents a local plaintext fallback from silently becoming the operational path. Isolated test/development loopback HTTP remains available so qualification does not require an operational certificate.

Login, rotated-session and logout cookies include `Secure` in HTTPS mode, plus `HttpOnly` and `SameSite=Strict`. Non-GET/HEAD authenticated mutations also require the Chief CSRF token and a same-origin request. `HttpOnly` is an additional cookie protection; it does not replace HTTPS.

Verified production HTTPS responses add `Strict-Transport-Security: max-age=31536000`. All transport responses also add `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and `Permissions-Policy: camera=(self), microphone=(), geolocation=()`. The camera remains same-origin because Staff photo evidence requires it. HSTS is deliberately not emitted over plaintext or isolated test HTTP.

The adapter installs Waitress's proxy middleware with untrusted-header clearing and a scoped logger that records a fixed rejection code, not malformed header values. Do not replace this arrangement with raw-header trust or a wildcard trusted proxy.

## Farm production gate

Farm production availability is independent from transport availability. `farm_operations` defaults to `DISABLED`. A production Farm route opens only when all of the following are simultaneously true:

- the process is a validated `deployment.launch` service (`CHIEF_SERVICE_CONFIGURED=1`),
- the instance is `PRODUCTION`,
- the reviewed instance configuration explicitly contains `farm_operations: "ENABLED"`,
- the Chief backend is loopback-bound, and
- the configured trusted TLS proxy is an explicit loopback IP.

Merely setting `CHIEF_INSTANCE_MODE=production`, or manually setting the Farm enablement environment variable, is insufficient. The repository supports the gate but does **not** mark any real farm deployment approved or enabled. That decision belongs to deployment acceptance.

## Bounds and lifecycle

- Four request workers, connection limit 32, backlog 16.
- 16 KiB header limit, 12 MiB request body limit, 16 MiB response buffer limit. The existing 8 MiB document upload contract fits within the body limit.
- Inactive channels time out after 15 seconds with one-second cleanup. This is an idle timeout, not an absolute deadline against indefinitely trickling clients.
- Transport buffers are configured not to spill accepted-size private bodies/responses to shared temporary storage. Application parsers and their own temporary files retain their separate controls.
- Request lookahead is disabled. Incomplete bodies do not enter handlers. Invalid framing is rejected by Waitress; an oversized request may be reset before its error response reaches the client.
- A dedicated socket map per instance and event-loop-owned shutdown avoid closing Windows select handles from another thread. Bind/start failures clean up their dispatcher and sockets. Shutdown requests stop transport processing, cancel queued requests and give active request workers up to five seconds to finish; this is not guaranteed completion of a long-running external action or OS call.

These are bounded transport controls, not comprehensive resource-exhaustion protection. JSON serialization, application execution time, cumulative memory pressure, parser limits and target capacity need their own qualification.

## Diagnostics and evidence

Unexpected application errors return a generated correlation ID and fixed error message. Logs contain fixed codes and IDs, not request URLs, headers, body values, raw exceptions or stack locals. Waitress handles client socket aborts without trying to write a second application error response. Request body/response traffic is not access-logged by this adapter.

Tests include socket resets, committed-mutation no-replay assertions, malformed/oversized/incomplete requests, synthetic error redaction, bind failure, repeated shutdown, trusted/untrusted proxy behavior, production plaintext rejection, production HSTS/browser headers, and real certificate-verified HTTPS login/reauthentication/logout through a disposable loopback proxy. The fixture is test-only and is not an operational proxy template.

Actual DNS, certificate issuance/renewal, firewall rules, TLS-proxy installation, dedicated OS identity/socket containment and public reachability remain deployment work and are not claimed by this phase.
