# Chief-scoped DNS policy and safety audit (2026-10-16)

This is a review-branch implementation, not release or host acceptance evidence.
No OS DNS configuration, resolver files, NetworkManager, firewall, systemd-resolved,
or browser-wide managed policy is changed.

## Configuration and behavior

New/default Chief configuration selects `quad9`. Production instance JSON accepts
`"dns_mode": "quad9"` or `"dns_mode": "system"`. Non-service invocations use
`CHIEF_DNS_MODE=quad9|system`. Service configuration is authoritative and rejects
conflicting environment values. Missing configuration defaults to Quad9; invalid
configuration fails closed. Restart the relevant Chief process after an intentional
configuration change. Do not edit an active production installation during review.

Quad9 uses https://dns.quad9.net/dns-query with TLS certificate verification,
HTTP/2, and numeric bootstrap 9.9.9.9. Bootstrap is the DoH server connection, not
an unencrypted DNS request. Resolver configuration does not read host resolvers or
search suffixes. A and AAAA share a five-second resolution budget. NXDOMAIN,
SERVFAIL, timeout, TLS/dependency errors, and empty answers stop the operation;
there is no automatic alternate provider, System DNS, or Google fallback.
There is no page-load-time based policy switching. System DNS is an explicit user
choice and preserves the user's existing resolver setup. It does not promise
Quad9 filtering or encrypted DNS.

Transport and provider metadata are separate (`DoHProvider`); changing the AI
provider never changes DNS policy. Additional DNS providers would require explicit
reviewed metadata/configuration, not a hard-coded hidden fallback. No global
socket monkeypatch is used. Public destinations remain subject to existing
public-address validation, including mixed public/private answer rejection and
numeric connection pinning. IP literals require no DNS lookup and therefore get
no DNS reputation verdict; public-address and HTTPS controls still apply.

## Exact coverage

| Path | DNS enforcement |
| --- | --- |
| Automated Playwright reader and worker | Existing loopback CONNECT proxy now resolves via selected Chief policy; original TLS hostname retained by browser |
| Optional Scrapy collector | Same parent-process CONNECT proxy; no collector resolver change needed |
| Human-operated website sign-in | Dedicated CONNECT proxy, public HTTPS only; identity-provider redirects allowed; selected mode and runtime propagated to child process |
| Release acquisition / dependency metadata | Chief HTTPS adapter resolves and connects to validated numeric addresses with original SNI/certificate hostname; ignores ambient proxy settings; redirects refused before a second request |
| Owner, Staff, Job PWAs on user devices | Device/browser DNS; web JavaScript cannot set browser or OS DNS; local Chief mDNS stays unchanged |
| Groq/Mistral SDKs, SMTP, Web Push | Existing transports remain outside this policy; no claim of complete Chief-wide encrypted DNS |
| n8n/internal-reporting integrations | Existing explicit endpoint transport, including local integrations, remains outside this public-egress policy |
| Installer bootstrap, Git/pip/npm, Caddy, external runtime processes | Their own networking; not controlled by this application-layer resolver |

The selected policy is **not an OS network sandbox**. Chromium native secure-DNS
flags/policies are not relied upon: proxy-side resolution provides application
request coverage independently of native DoH support. Browser background traffic,
future transports and browser compromise are not proven contained. QUIC/DNS
prefetch are disabled and non-proxied WebRTC UDP restricted; automated contexts
also block WebRTC/Worker/WebTransport APIs and service workers. Real-browser
packet-level leak testing remains a release qualification requirement. Human
sign-in is intentionally not subject to the autonomous GET/HEAD-only rule.

Secure mode has no silent fallback on any covered path. Uncovered transports are
coverage gaps, not fallback mechanisms. Extending SDK/notification transports
needs explicit client adapters and tests that retain streaming, TLS, proxy, local
network and credential semantics. Local integrations must not be accidentally
sent to a public resolver. No latency guarantee is made; there is no shared cache
or cross-request connection pool in this initial resolver implementation.

## Existing implemented safety versus remaining work

| Layer | Existing implemented evidence | Planned/documented or unimplemented gap |
| --- | --- | --- |
| Browser protection | `browser/request_policy.py`: default-deny origins/methods, submission phase gate, redirect checks, blocked downloads/service workers/WebSockets; `browser/egress.py`: public IP pinning; `browser/runtime.py`: production executable hash and sandbox requirement | No verified Google Safe Browsing/equivalent reputation service; no exhaustive browser-exploit containment or packet-level leak qualification |
| URL/domain checks | `browser/request_policy.py` validates public HTTPS origins, credentials and ports; proxy rechecks each connection; `browser/site_access.py` manages explicit source permission/revisions; `skills/scam_screening/skill.py` provides job scam heuristics | No complete URL-path threat reputation engine. DNS reputation sees domains, not every malicious path on an otherwise legitimate host |
| Download/file controls | Automated downloads disabled; `control/files.py` limits type/size, rejects active PDF/DOCX content, bounds parser subprocesses and registered retrieval; `update_center/acquisition.py`, `release_trust.py`, `staging.py` separate acquisition from signed verification/staging | No general antivirus verdict or guaranteed detection of every malicious document; signature verification authenticates a publisher/artifact, not its harmlessness |
| Chief STOP rules | `policy/rules.py` forbids payment/OTP/password entry, secret disclosure, fabrication and security bypass; `policy/engine.py` gives hard prohibitions precedence; worker policy gates require authority and preserve ASK/BLOCK outcomes; PWA STOP display is not execution authority | Existing `docs/Security-Boundaries.md` calls for independent assessment, complete adversarial matrix, target recovery and real-host acceptance. UI wording alone does not prove hardware interlocks |
| DNS reputation | Added here for covered paths through Quad9 Secure DoH | Does not replace any safety layer above; uncovered paths listed explicitly |

Inspected repository baselines: chief-agent `6ab701097b774fb3ea14513b164cdf05f74b7bf9`;
chief-installer acceptance-hardening `d0bc48198f0c305177e3b65ae533888ec8bde317`;
chief-owner-pwa `b8d139b0789918f3c937e640cc618e66194c966b`;
chief-staff-pwa `4a07d324e13630e2742a4e69127569d70a3cdf83`;
Job-agent-pwa `90eb0cfa2155c4d5f6a4ca8bf510a5d01e1f7db9`.
PWA repositories were inspected without modification.

Technical references: [Quad9 services](https://docs.quad9.net/services/),
[Quad9 HTTP/2 requirement](https://docs.quad9.net/FAQs/), and
[dnspython DoH transport/bootstrap API](https://dnspython.readthedocs.io/en/stable/query.html).
