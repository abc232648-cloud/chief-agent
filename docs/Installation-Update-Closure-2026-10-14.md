# Installation/update closure — 2026-10-14

This document supersedes the earlier `Chief-Update-Center.md` statements that dependency installation, release downloading, and managed service restart are wholly outside the update mechanism. Those statements describe the pre-closure implementation and remain useful historical design context.

## What is now implemented

### Release acquisition

`update_center.acquisition` can download a release archive and signed envelope into a new operator-owned private staging directory. Acquisition is credential-free HTTPS only, uses an explicit local host allow-list, rechecks the final redirect host, applies the existing envelope/archive size bounds, and writes inert files with private permissions. A download has status `DOWNLOADED_NOT_VERIFIED` and grants no execution or activation authority.

The existing publisher trust path remains authoritative. `ManagedInstallation.stage()` still verifies the signed release and complete source package before a source slot can exist. Download metadata cannot add publisher keys, lower the replay floor, or choose an activation policy.

### Dependency-changing releases

A signed, compatibility-approved candidate whose production dependency identity differs from the selected release is no longer rejected solely because dependencies changed. During explicit human-authorized validation, `update_center.runtime_install` prepares a new runtime inside that candidate's private release slot.

Automatic dependency changes require the signed source package to contain both `requirements.txt` and `requirements-lock.txt`. The lock must contain exact `==` package pins with SHA-256 hashes only. Requirements-file index directives, external/direct URLs, editable references, unpinned entries, missing hashes and duplicate hashes are rejected before pip runs. Test/development requirements files do not alter the production runtime identity.

The installer creates a virtual environment from the separately pinned bootstrap interpreter and runs isolated, non-interactive pip against the explicit `https://pypi.org/simple` index with `--require-hashes` and `--only-binary=:all:`. It then runs `pip check` and records the runtime interpreter target/hash plus the signed production-requirements identity. This intentionally fails closed when the required locked wheel set is unavailable for the platform; a source-distribution build is not silently executed by the automatic path.

The bootstrap interpreter is not modified in place. Incomplete runtime creation fails closed for operator review. A candidate requiring different dependencies cannot probe, run acceptance, or become selected until its release-local runtime is valid. Candidate regression tests, read-only probes, and managed service launch all use the same qualified runtime. Later releases whose production dependency identity is unchanged inherit the currently active qualified runtime, including across an earlier dependency-changing release; they do not fall back to the original bootstrap environment.

The signed lock and hashes make automatic resolution deterministic, but they do not by themselves prove that a third-party dependency is safe. Publisher dependency review and final supply-chain/security acceptance remain required before unattended production updating is enabled.

### Linux managed service restart and rollback

`deployment.linux_service_control` implements the automatic restart path for the Folio-class Linux deployment using only the fixed `systemd --user` units for dashboard, worker and scheduler. It does not accept arbitrary unit names or shell commands.

`application.update_manage apply --restart-linux-services` performs the following sequence after fresh administrator authentication and existing validation/approval gates:

1. stop the fixed managed Chief services;
2. execute the existing guarded backup/quarantined-restore preparation and exact source switch;
3. start the fixed services;
4. require a new `READY` run identity for dashboard, worker and scheduler;
5. report `SOURCE_SELECTED_SERVICES_READY` only when all three are freshly ready.

If the selected release cannot reach fresh READY state, the command stops the managed services again, performs an authenticated exact-transaction source rollback under the existing guarded recovery locks, verifies the previous source with its qualified runtime, restarts it, and returns `ACTIVATION_ROLLED_BACK` with a failure exit status. Operational data is not automatically restored.

Interrupted source switching retains the existing fail-closed recovery path. `recover --restart-linux-services` can stop the fixed services, recover only the exact interrupted previous source, and restart it.

### Service definitions and first installation

The existing `deployment.service_files` output remains a reviewable service-definition generator; this closure does not silently install or enable operating-system services. Initial service installation/enabling is an explicit Folio staging action and belongs to the next deployment-validation phase. Windows and macOS automatic restart are not claimed by this Linux-specific closure path.

## Release-set relationship

The cross-repository release set remains `chief-install-closure-2026-10-14` with backend contract `chief-install-closure-v1`. The historical full-suite/browser evidence recorded in `Release-Candidate-Closure-2026-10-14.md` covers the tested source points identified there. It does **not** retroactively certify the installation/update code added after those source points.

The new installation/update code has dedicated regression tests for:

- bounded release acquisition and redirect/host policy;
- dependency-change staging and runtime gating;
- signed hash-lock validation and deterministic wheel-only dependency preparation;
- isolated runtime preparation, interpreter identity and multi-release runtime inheritance;
- managed source activation rollback;
- fixed Linux service stop/start ordering and fresh READY requirements;
- the optional authenticated CLI restart path.

Those tests and repository CI must pass before this closure branch is merged. Real Folio service installation, HTTPS/LAN access, boot/restart behavior, secrets/permissions, backup/restore, and phone/PWA delivery remain separate on-device acceptance work. Nothing in this closure document claims production deployment certification.
