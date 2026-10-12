# Runtime selection and maintenance

## Agent runtime qualification

Runtime candidate snapshot: **2026-10-12**.

- **Primary candidate:** OpenClaw `2026.10.1`.
- **Supplemental candidate:** Hermes Agent `0.21.3`.
- **Supplemental mode:** `GAP_ONLY`.
- **Authorized supplemental workloads:** none.

These are research/qualification candidates, not installed or approved production runtimes. Documentation establishes provenance and candidate capabilities only. It is not execution evidence and cannot make a candidate eligible.

Chief selects the primary candidate only after every mandatory gate has fresh `PROVEN` evidence: security/isolation, privacy/secrets, Windows, Linux, long-running reliability, restart recovery, observability, updates/rollback and device scope. A missing, stale, documented-only or failed gate keeps the primary unselected.

The supplemental candidate is not a general fallback. It may receive a workload only when all of the following are true:

1. the primary candidate is independently qualified;
2. the supplemental candidate is independently qualified; and
3. a specific primary-runtime gap is explicitly recorded in `config/runtime_policy.json`.

No supplemental workloads are recorded in the current policy. The supplemental candidate therefore has no workload authority. Its documented limitation around recovery of already-running delegated children must remain `UNKNOWN` until exercised; it must not be treated as evidence that a recovery gap is solved.

`config/runtime_candidates.json` pins candidate identity and documentation provenance. `config/runtime_policy.json` pins runtime roles. Runtime policy parsing rejects unknown fields, duplicate JSON keys, unknown candidates, duplicate workload IDs and any supplemental mode other than `GAP_ONLY`.

Phase 6 does not install a runtime, change service startup, migrate state or deploy anything. Installation, host ownership, backup/restore and service-start behavior belong to the installation-readiness phase.

## Language and database baselines

Windows tests use CPython 3.12.14 with private SQLite 3.53.4. The Ubuntu 26.04 VM uses distribution CPython 3.14.4 with private SQLite 3.53.4. Different interpreter builds require separate results. Private runtimes still require maintenance and explicit provenance.

SQLite came from official 3.53.4 Windows DLL and source-amalgamation distributions with published integrity verification. Windows uses a private interpreter copy. Ubuntu uses a private library selected explicitly at launch, with compiler/build receipts recorded separately. Neither replaces shared system libraries. Services must use the same library selection as their tests; a virtual environment alone does not select SQLite.

## Browser runtime baseline

The maintained browser baseline is a sandboxed Chromium-family build at version `154.0.8037.57`, with exact executable/archive hashes retained in the platform inventories. `CHIEF_BROWSER_RUNTIME`, or service field `browser_runtime`, references a bounded non-secret JSON object with exactly `executable`, `sha256`, and `version`. Paths must be absolute and plain. Explicit launches verify the executable digest and require sandboxing. Digest mismatch or sandbox failure does not trigger an automatic fallback.

## Maintenance rule

A version string alone does not prove security state. Record provenance and identity, apply updates in isolation, rerun the relevant regressions and full acceptance, then repin the candidate or executable. Do not preserve obsolete binaries only to retain a digest, disable security controls to make a runtime start, or silently repin changed executables. Runtime binaries, profiles, cookies and credentials stay out of source archives.
