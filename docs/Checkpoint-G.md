# Checkpoint G — compatibility/update planning + runtime qualification gate

Checkpoint G deliberately stops **before update activation and before runtime integration**.

## Update Center boundary

`compatibility/` defines a strict release manifest. `update_center/` turns a current/candidate manifest plus the Chief Capability Registry into a deterministic staging plan. The plan carries the candidate source identity, dependency-selected regression coverage, migration/backup requirements and blockers.

There is no downloader, package installer, database migrator, service restarter or activation path in this checkpoint. A plan can only reach `READY_FOR_MANUAL_REVIEW`; it cannot install itself. Private installation state must be excluded from release payloads and schema-changing candidates require the already-established operational backup foundation.

Arbitrary downgrade is rejected. A candidate must explicitly declare compatibility with the current known-good release. Replacing software does not reverse database migrations; operational data restore remains a separate procedure.

## Runtime qualification boundary

`runtime_qualification/` is a fail-closed comparison framework. It does not import, install or execute OpenClaw or Hermes Agent.

The pinned research snapshot in `config/runtime_candidates.json` records two current candidates as of 2026-09-24:

- OpenClaw 2026.9.5 — release provenance: https://github.com/openclaw/openclaw/releases/tag/v2026.9.5
- Hermes Agent 0.21.3 / v2026.9.14 — release provenance: https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14

Documentation-derived capabilities are hypotheses/provenance, **not qualification evidence**. A hard gate counts only as `PROVEN` after fresh execution evidence for the required target. `DOCUMENTED`, `UNTESTED`, stale evidence, or `FAILED` blocks qualification.

Mandatory gates are:

- authority/security;
- isolation;
- reliability/recovery;
- Windows compatibility;
- Linux/Folio compatibility;
- operations/update/rollback;
- privacy/data egress/credentials/memory.

The framework intentionally does not auto-select between multiple eligible runtimes. Product/runtime selection remains an explicit reviewed architecture decision. A secondary runtime remains disallowed by default unless a separate evidenced capability gap justifies the added operational/security burden.

## Current outcome

No runtime is selected by Checkpoint G. This development environment cannot prove the required Windows + Linux/Folio runtime gates for either candidate, and no vendor runtime is installed here. Runtime integration remains blocked until the qualification gate has fresh target evidence.

## Unchanged boundaries

Chief remains authoritative for policy, approvals, evidence, RBAC, private records, Decision Ledger and action authority. The Job and Farm runtime code is not moved into either candidate. No Farm expansion occurs here.
