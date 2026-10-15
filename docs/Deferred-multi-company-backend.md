# Deferred multi-company backend — do not merge or deploy

This work belongs to `feature/farm-finance-tenant-scope` (draft PR #33).
The branch includes the earlier record-isolation work from draft PR #32.
Both drafts are deferred from the first release. Main, installations and
operational databases are not changed by retaining this code here.

## Development boundary

Starting software reference: `7610eff87e1952dbe09ca3fc151a2fe3af77abd6`.
The additions use the existing `domain_records` scope envelope; they add no
tables or schema migration. Existing unscoped records are not reassigned to a
company. Adoption of historical operational records requires a separate,
backed-up migration design and explicit acceptance.

The backend resolves `X-Chief-Farm-ID` against the current user's authorized
assignments. Multiple accessible farms require an explicit selection. Nested
operations retain that selection and cannot switch farms. Current assignment
authority is rechecked; cached context does not preserve revoked access.

The scope now covers planning and its evidence revisions, labour and expense
links, costing policy and unit conversions, expense allocation, egg-loss links,
physical counts and their stock revisions, clarification sources and effects,
staff work and directories, private photos, daily briefs, recipient settings,
and Farm AI configuration/context. Queries apply scope before history limits.
Matching event identifiers in different farms do not share notification IDs.

Desired roles do not replace Chief authentication, capability, policy,
approval, financial-permission or evidence controls. Linking an expense or
egg loss does not create another expense or stock movement. AI remains guidance
only, without action tools. Provider registrations and global model controls
remain installation-owned; farm configuration references them. Existing shared
AI request limits remain installation-wide.

Farm reporting zones are pinned independently on first authorized write.
Existing scoped data from earlier draft builds inherits the recorded
installation timezone, preserving historical day boundaries. Missing or
conflicting historical settings require review rather than host-zone guessing.
New farms without history use host detection. Legacy no-catalog behavior is
preserved.

## Intentionally not activated

- No merge, deployment, operational migration or external provider call.
- No multi-company UI/PWA rollout or automatic reassignment of old records.
- No new automation, equipment controls or external delivery integration.
- The external aggregate reporting provider refuses tenant-mode execution until
  it has an explicit farm-scoped integration contract. It cannot silently emit
  combined company counts.

Before future activation: review the draft, qualify selected-farm behavior in
each client and its offline storage/queues, design any historical-data adoption,
accept notification navigation and integrations, complete security/recovery
acceptance, and obtain an explicit decision to merge. These are not implied by
backend tests.

## Verification and rollback

`tests/test_farm_tenant_remaining.py` adds adversarial tests for colliding IDs,
cross-farm references, private photos, AI context/configuration, directories,
API selection, limits, timezone history, revoked assignments, notification IDs,
conversion versions and expense allocation. Existing tenant and legacy suites
remain required. CI runs tenant coverage on Windows and Ubuntu. Final run
results belong to the exact tested commit and the PR evidence; earlier runs
must not be described as tests of a later candidate.

Software rollback is checkout of the starting commit in an isolated development
copy. This does not restore operational data. No operational database has been
migrated as part of this work. Do not use an older build to write a development
database containing records from this candidate; discard synthetic fixtures or
restore their verified isolated backup instead.
