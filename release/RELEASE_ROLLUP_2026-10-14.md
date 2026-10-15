# Chief install-closure release roll-up

**Release set:** `chief-install-closure-2026-10-14`  
**Historical release tag:** `chief-install-closure-2026-10-14`

This file is the human-readable release summary. It exists so operators and reviewers do not need to reconstruct the release by reading the repository's entire commit history.

## What the release contains

### Runtime and backend
- Production/preview/test instance boundaries and fail-closed startup checks.
- Dashboard, worker and scheduler service runtime controls.
- Explicit source/schema/backup identity pinning.
- Storage, backup, restore-quarantine and migration safeguards.

### Managed installation and updates
- Immutable private source slots.
- Manual fail-closed source selection and rollback.
- Signed release/update verification and archive path/private-state protections.
- Dependency-compatible runtime references and separately qualified runtimes for dependency changes.
- Linux managed service lifecycle and fresh READY-state verification.

### Farm/staff/security
- Farm production gating and staff authorization boundaries.
- Evidence, incident/SOP and operational controls completed by the release-set work.
- Secrets, sessions, operational databases and private keys remain outside source packages.

### PWA clients
- Owner PWA release-set compatibility marker and CI qualification.
- Staff PWA release-set compatibility marker and CI qualification.
- Job Agent PWA release-set compatibility marker and CI qualification.

### CI and release evidence
- Backend release workflows passed on the published backend commit.
- Companion PWA CI passed on the exact published PWA commits.
- `release/PRODUCTION_CLEARANCE_2026-10-15.md` corrects the immutable evidence ledger and separates automated qualification from live-host/device acceptance.

## Commit-history policy

The existing history is intentionally preserved. A repository having 100+ commits is normal for an actively developed system and is not a deployment problem. Rewriting/squashing historical `main` now would change commit identities and weaken links between tags, CI runs, release records and cross-repository evidence.

For readability, use this roll-up plus dated release records. Going forward, logical PRs may be squash-merged when appropriate so `main` remains easier to scan; historical tagged commits remain immutable.

## Identity terminology

`release/release-set-2026-10-14.json` records the source snapshots originally qualified by that release contract. The annotated Git tag records the later repository commits that were published. Those are separate provenance facts and must not be silently rewritten to look identical.

The deployment bootstrap introduced after the historical tag therefore generates a deterministic manifest for the exact checkout being installed and creates a new installation backup pinned to that source identity. This avoids falsely reusing the older `30d2...` source pin for different bytes.
