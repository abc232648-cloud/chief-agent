# Production Deployment and Release Clearance — Corrected Evidence Ledger

**Release set:** `chief-install-closure-2026-10-14`  
**Backend contract:** `chief-install-closure-v1`  
**Release tag:** `chief-install-closure-2026-10-14`  
**Audit date:** 2026-10-15  
**Purpose:** authoritative correction to the earlier narrative release-clearance report. This file records only evidence independently resolvable from the repositories and explicitly separates automated qualification from live-host/device acceptance.

## 1. Release identity

The release contract in `release/release-set-2026-10-14.json` records the source commits and source-tree digests used for release-set qualification. The Git tag records the subsequently published repository commit. These are different identities by design and MUST NOT be conflated.

| Component | Release-contract tested commit | Published tag target |
|---|---|---|
| Backend (`chief-agent`) | `1cdac6178fe9964ac7fbb80fd9c02fb42740dbb9` | `0d96fdf979b9eba0f3deb0024278f5c2557a5319` |
| Owner PWA | `d0d924a507778be8497f827bf5471c81678a2934` | `b8d139b0789918f3c937e640cc618e66194c966b` |
| Staff PWA | `d46b1c18b6fc6ce5f1ad0be6887235fca14dfb30` | `4f88eb79f2382406c55b85675e939405d70f0c98` |
| Job Agent PWA | `3435ca0ffb4b04116a59268e48e8529fa377acac` | `90eb0cfa2155c4d5f6a4ca8bf510a5d01e1f7db9` |

### Corrected errors from the earlier report

The following commit IDs in the earlier narrative report were invalid and are superseded by this ledger:

- Staff PWA: `4f88eb7a7e800a7b457dc5b49ae61beaf2479e39` → **correct:** `4f88eb79f2382406c55b85675e939405d70f0c98`
- Job Agent PWA: `90eb0cfec2cb8498877bc9ec3315a4d0482260e0` → **correct:** `90eb0cfa2155c4d5f6a4ca8bf510a5d01e1f7db9`

All four repositories contain the annotated tag `chief-install-closure-2026-10-14` pointing at the published commits above.

## 2. Publishing semantics

The release is **tag-published to GitHub**. At audit time the backend repository has **no GitHub Release object**. Therefore documentation MUST use `tag-published` or `annotated tag published`, not imply that a GitHub Releases page/object was created.

This is not a deployment defect; it is a terminology/evidence correction.

## 3. CI gate — VERIFIED PASS

### Backend final-merge commit

Final backend release commit: `0d96fdf979b9eba0f3deb0024278f5c2557a5319`.

Verified successful runs include:

- Job Agent PWA Backend CI #114 — run `38653259010` — **success**
- Agent Manifest, Registry, Interface, Capability & Staff CI #73 — run `38653259064` — **success**
- Module Lifecycle CI #68 — run `38653259025` — **success**
- Farm Staff CI #80 — run `38653259014` — **success**
- Graph Update #4 — run `38653272540` — **success**

The earlier narrative report incorrectly recorded Farm Staff CI as `38653259015` and Graph Update as `38653259030`. Those IDs are superseded by the correct IDs above.

### Companion published commits

Post-publish main-branch CI also completed successfully on the exact published tag targets:

- Owner PWA CI #50 — run `38660863093` — commit `b8d139b0789918f3c937e640cc618e66194c966b` — **success**
- Staff PWA CI #78 — run `38660874688` — commit `4f88eb79f2382406c55b85675e939405d70f0c98` — **success**
- Job Agent PWA CI #53 — run `38660882446` — commit `90eb0cfa2155c4d5f6a4ca8bf510a5d01e1f7db9` — **success**

**Gate 1 status: PASS.**

## 4. Release contract — PRESERVED

`release/release-set-2026-10-14.json` is intentionally not rewritten by this evidence correction. It records qualified source identity and source-tree SHA-256 values used by installation/readiness verification. Changing those fields after release would create a new release contract and could invalidate reproducibility.

The existing expected backend source-tree digest remains:

`30d2f4580ebf06ec3d09251548c1f6291dce00a9275a7864f9569568f7a066c3`

## 5. Production bundle verification — PENDING EXTERNAL ARTIFACT EVIDENCE

The earlier report names:

- `Chief_Production_Release_Set_2026-10-14.zip`
- claimed SHA-256 `2bc6dd487f569ca93b8be1cb3d646d642a9610caa7f2cd2361b9dd13ead6e579`
- claimed size `399,634` bytes

The production ZIP is not stored in this repository and the inspected release workflow run exposes no downloadable Actions artifact. Consequently this audit cannot independently recompute the ZIP SHA-256 or validate its `MANIFEST_SHA256.json` from GitHub alone.

**Status: PENDING until the exact production ZIP is supplied to the verifier or placed in an auditable release asset location.**

Required verification:

```bash
sha256sum Chief_Production_Release_Set_2026-10-14.zip
unzip -q Chief_Production_Release_Set_2026-10-14.zip -d /tmp/chief-release-audit
cd /tmp/chief-release-audit
python - <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path('.')
manifest = json.loads((root / 'MANIFEST_SHA256.json').read_text())
# Verify according to the manifest schema shipped by the release tooling.
print('manifest-present', bool(manifest))
PY
```

Use the repository's release verification tooling for the authoritative manifest-schema check rather than treating the illustrative Python snippet above as a replacement for project logic.

## 6. Folio staging/live-host acceptance — NOT YET INDEPENDENTLY PROVEN

Repository implementation and automated tests support the intended service architecture, but GitHub evidence alone does not prove that the target Folio host currently has the release installed and active.

The earlier report's live-host statements therefore remain **unverified acceptance claims**, not failed checks.

Required live evidence must capture at minimum:

```bash
git rev-parse HEAD
systemctl --user --no-pager --full status chief-agent-dashboard.service
systemctl --user --no-pager --full status chief-agent-worker.service
systemctl --user --no-pager --full status chief-agent-scheduler.service
systemctl --user show chief-agent-dashboard.service -p NoNewPrivileges -p UMask -p Restart -p KillMode
curl -fsS http://127.0.0.1:8000/api/health
```

The host evidence must also show:

- deployed release-set identity;
- source/manifest verification result;
- reverse-proxy routing for `/api/`, `/owner/`, `/staff/`, `/jobs/`;
- authorization failure for prohibited role escalation;
- isolated backup/restore drill result without production-state mutation.

**Gate 2 status: PENDING LIVE ACCEPTANCE.**

## 7. Client qualification vs physical-device acceptance

Automated client qualification and physical-device acceptance are separate gates.

The successful PWA CI runs above establish client build/contract qualification for the published commits. They do **not**, by themselves, prove installation and real-browser behavior on physical Android and iPhone devices.

Required physical-device matrix:

| Check | Android | iPhone |
|---|---|---|
| `/owner/` loads through production proxy | Required | Required |
| owner login/session/logout | Required | Required |
| `/staff/` login/session/logout | Required | Required |
| install/add-to-home-screen behavior | Required | Required |
| offline shell launch | Required | Required |
| offline outbox enqueue | Required | Required |
| reconnection drain/deduplication | Required | Required |
| camera/photo submission path | Required | Required |
| push subscription/notification where platform permits | Required | Required |

**Automated client qualification: PASS.**  
**Gate 3 physical-device acceptance: PENDING.**

## 8. Security/model qualification

No contradictory GitHub evidence was found against the release's fail-closed model/runtime policy. This ledger does not expand the qualified model set beyond evidence already established by the release qualification workflows.

**Gate 4 status: PASS subject to the exact production bundle matching the qualified source/package manifests.**

## 9. Current production decision

### `HOLD — CODE/CI QUALIFIED; EXTERNAL ACCEPTANCE EVIDENCE REQUIRED`

No application rebuild is required by the discrepancies corrected here. The release candidate remains the tagged commit set above.

The HOLD can be changed to `GO` only after all three conditions are evidenced:

1. exact production ZIP digest and internal manifest verification;
2. live Folio install/readiness/service/health acceptance;
3. physical Android + iPhone smoke acceptance.

When those are complete, issue a new dated clearance record referencing this file; do not rewrite historical evidence or move the existing release tag.
