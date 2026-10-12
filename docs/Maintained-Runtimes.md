# Runtime selection and maintenance

## Role-scoped runtime qualification

Runtime candidate snapshot: **2026-10-12**.

Chief assigns two deliberately different runtime roles:

- **General execution:** OpenClaw `2026.10.1`, lifecycle `SERVICE`, action authority `CHIEF_GATED`.
- **External research:** Hermes Agent `0.21.3`, lifecycle `ON_DEMAND`, action authority `NONE`.

These are pinned qualification candidates, not installed or production-approved runtimes. Documentation establishes provenance and candidate capabilities only. It is never execution evidence and cannot make either runtime eligible.

Chief remains the policy, identity and consequential-action authority. OpenClaw may execute only work already authorized through Chief. Hermes may gather and synthesize external evidence but cannot authorize or perform a consequential action merely because its research supports one.

The runtime layer is agent-agnostic. Current or future Chief domains request capabilities rather than naming runtimes. Internal Chief data remains on the `CHIEF_INTERNAL` route. Genuine external/public research automatically targets the qualified research runtime. `ON_DEMAND` means the research runtime need not remain active when no research work exists; it does not require the owner to manually request each invocation.

### Qualification rules

Both roles fail closed. A missing, stale, documented-only or failed mandatory gate leaves that role unqualified.

The general-execution role requires fresh `PROVEN` evidence for authority/security, isolation, reliability, Windows compatibility, Linux/Folio compatibility, operations and privacy.

The research-only role requires those same base gates plus `RESEARCH_INTEGRITY`. Research qualification must prove, among other comparable workloads, that malicious research content cannot grant action authority, external research routes to the research runtime, internal data does not start it, source provenance survives the research path, unrelated Chief secrets remain inaccessible, provider failure degrades safely and delegated/parallel research remains isolated.

The roles qualify independently. A qualified research runtime does not make the execution runtime ready, and a qualified execution runtime does not activate external research. Chief reports both roles separately.

### Runtime profile isolation

Pinned runtime candidates reserve Chief-owned profile paths below Chief private state. Existing desktop/global profiles are not discovered, imported or adopted. A missing reserved profile is reported as `CREATE_REQUIRED`; a correctly marked Chief-owned profile is `CHIEF_OWNED`; an unrelated, linked or malformed object at the reserved path is a conflict and blocks readiness.

Runtime-specific qualification must additionally prove that all relevant writable state is actually redirected into the Chief-owned profile/sandbox. A profile feature described in upstream documentation is not sufficient evidence by itself.

## Research/search routing

`config/research_policy.json` defines a provider-neutral research boundary.

- Internal data route: `CHIEF_INTERNAL`.
- External research route: `RESEARCH_RUNTIME`.
- Initial enabled search backend: `ddgs` (key-free).
- Reserved, not enabled: `brave`, `searxng`.
- Automatic provider fallback: disabled.
- Research action authority: `NONE`.

Domain code must not bind itself directly to one search provider. The generic research route selects the configured backend only after the research runtime has qualified. Reserved providers are design candidates only and are not silently activated if the initial backend fails.

## Candidate provenance

OpenClaw documentation records isolated gateway state/workspace/profile controls, sandbox/tool-policy controls, root-bounded sensitive file operations and a managed agent browser profile separate from the user's personal browser profile.

Hermes documentation records independent runtime profiles, isolated execution options, delegated subagents, web search/extraction through configurable providers, scheduled fresh-session jobs and optional checkpoint/rollback behavior. These statements remain `DOCUMENTED` until exercised by Chief's qualification workloads.

## Language and database baselines

Windows tests use CPython 3.12.14 with private SQLite 3.53.4. The Ubuntu 26.04 VM uses distribution CPython 3.14.4 with private SQLite 3.53.4. Different interpreter builds require separate results. Private runtimes still require maintenance and explicit provenance.

SQLite came from official 3.53.4 Windows DLL and source-amalgamation distributions with published integrity verification. Windows uses a private interpreter copy. Ubuntu uses a private library selected explicitly at launch, with compiler/build receipts recorded separately. Neither replaces shared system libraries. Services must use the same library selection as their tests; a virtual environment alone does not select SQLite.

## Browser runtime baseline

The maintained browser baseline is a sandboxed Chromium-family build at version `154.0.8037.57`, with exact executable/archive hashes retained in the platform inventories. `CHIEF_BROWSER_RUNTIME`, or service field `browser_runtime`, references a bounded non-secret JSON object with exactly `executable`, `sha256`, and `version`. Paths must be absolute and plain. Explicit launches verify the executable digest and require sandboxing. Digest mismatch or sandbox failure does not trigger an automatic fallback.

This Chief browser baseline and OpenClaw's own managed-browser capability are separate qualification surfaces. Selecting OpenClaw does not authorize reuse of a personal browser profile or weaken Chief's existing browser sandbox requirements.

## Maintenance rule

A version string alone does not prove security state. Record provenance and identity, apply updates in isolation, rerun the relevant role regressions and full acceptance, then repin the candidate or executable. Do not preserve obsolete binaries only to retain a digest, disable security controls to make a runtime start, or silently repin changed executables. Runtime binaries, profiles, cookies and credentials stay out of source archives.
