# Private dashboard transport

The dashboard uses Waitress 3.0.2 through a WSGI adapter. Existing route handlers still perform authorization, CSRF, policy, approval and evidence checks. They assemble bounded responses in memory and never write to a client socket. A delivery failure does not re-enter a route, undo committed work or retry a mutation. Application exceptions remain errors; even a business-level ConnectionAbortedError produces a redacted 500 rather than being silently treated as successful delivery.

## Binding and TLS trust

Default: `DASHBOARD_HOST=127.0.0.1`, `DASHBOARD_PORT=8765`. Local HTTP remains available. `localhost` resolves deliberately to one IPv4 loopback listener. Explicit private addresses remain configurable, but non-loopback plaintext application requests are refused. Public/wildcard binds are refused. Windows listeners use exclusive address ownership so a second dashboard cannot silently share the port.

An optional **same-host TLS reverse proxy contract** uses `CHIEF_TRUSTED_PROXY` set to one explicit loopback IP and a loopback dashboard listener. The proxy must terminate TLS, overwrite the scheme header with the actual client transport, preserve the original Host, and prevent direct access by untrusted local processes. This is not a public-deployment design or an installed proxy. A local process able to reach that trusted backend has the same proxy trust; dedicated OS identity/socket isolation still requires deployment acceptance.

Only the trusted peer's `X-Forwarded-Proto` may establish the scheme. Forwarded Host/client identity/port and other forwarding headers confer no authority and are removed. Proxy mode refuses HTTP, missing or untrusted scheme assertions. Origin must match the established scheme and the literal approved Host; Host and cross-site protections remain. Login, rotated-session and logout cookies include Secure in HTTPS mode, plus HttpOnly and SameSite=Strict. Cookies are not made secure merely because an arbitrary client supplied a header.

The adapter explicitly installs Waitress's proxy middleware with untrusted-header clearing and a scoped logger that records a fixed rejection code, not malformed header values. The server's duplicate default middleware is disabled to avoid parsing twice and to prevent its default malformed-header logger from recording supplied secrets. Do not replace this arrangement with raw-header trust or a wildcard trusted proxy.

Reference: [Waitress arguments](https://docs.pylonsproject.org/projects/waitress/en/latest/arguments.html) and [proxy deployment guidance](https://docs.pylonsproject.org/projects/waitress/en/latest/reverse-proxy.html). The dependency is pinned because adapter lifecycle and middleware compatibility must be reverified on upgrades.

## Bounds and lifecycle

- Four request workers, connection limit 32, backlog 16.
- 16 KiB header limit, 12 MiB request body limit, 16 MiB response buffer limit. The existing 8 MiB document upload contract fits within the body limit.
- Inactive channels time out after 15 seconds with one-second cleanup. This is an idle timeout, not an absolute deadline against indefinitely trickling clients.
- Transport buffers are configured not to spill accepted-size private bodies/responses to shared temporary storage. Application parsers and their own temporary files retain their separate controls.
- Request lookahead is disabled. Incomplete bodies do not enter handlers. Invalid framing is rejected by Waitress; an oversized request may be reset before its error response reaches the client.
- A dedicated socket map per instance and event-loop-owned shutdown avoid closing Windows select handles from another thread. Bind/start failures clean up their dispatcher and sockets. Shutdown requests stop transport processing, cancel queued requests and give active request workers up to five seconds to finish; this is not guaranteed completion of a long-running external action or OS call. Service orchestration and graceful long-operation draining remain separate work.

These are bounded transport controls, not comprehensive resource-exhaustion protection. JSON serialization, application execution time, cumulative memory pressure, parser limits and target capacity need their own qualification.

## Diagnostics and evidence

Unexpected application errors return a generated correlation ID and fixed error message. Logs contain fixed codes and IDs, not request URLs, headers, body values, raw exceptions or stack locals. Waitress handles client socket aborts without trying to write a second application error response. Request body/response traffic is not access-logged by this adapter.

Tests include actual socket resets, committed-mutation no-replay assertions, malformed/oversized/incomplete requests, synthetic error redaction, bind failure and repeated shutdown, trusted/untrusted proxy behavior, and real certificate-verified HTTPS login/reauthentication/logout through a disposable loopback proxy. A test-only pinned cryptography dependency generates a fresh short-lived certificate/key under the private fixture directory; no test key is embedded in source, installed in an OS trust store or exported as evidence. The fixture is not an operational proxy template.

Full HTTP/browser regressions use this same Waitress transport. Schema: unchanged. No real database migration, key/provider call, restored-state promotion or operational TLS deployment is performed. Installed proxy/certificate lifecycle, real/Folio service separation, backup/restore, OS containment, power-loss and deployment acceptance remain OPEN.
