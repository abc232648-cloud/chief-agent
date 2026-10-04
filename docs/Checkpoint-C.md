# Checkpoint C — controls, System Health and policy foundation

Development baseline: accepted B.5 source ZIP SHA-256 `d050d185e1d6e301c94f01b3b369dfcc948cc10ed09694087e47e6c473f42507`, source-tree digest `c396354438a15616b0847ff691cc6f37ca4a30213f0812a8b4b4ebc4f7f1e183`. C is for isolated development/test databases only. Real deployment backup/restore acceptance remains OPEN. No operational/Folio database migration, quarantine unlock, promotion or later checkpoint is authorized by this release.

## Schema and recovery boundary

There is no migration on Store construction, dashboard startup, worker startup, service composition, health reads or transition requests. `database.migrations.migrate_isolated` is an explicit development API; there is no production migration CLI or HTTP endpoint.

Its caller must provide a disposable root with `.chief-isolated-development.json` containing `{"purpose":"ISOLATED_DEVELOPMENT"}`, a verified B.5 backup with trusted manifest/source/schema identities, and an isolated restored copy with its quarantine intact. The database, backup and restore must be inside that root. This marker is an explicit development assertion, not proof of deployment safety or a substitute for operator authorization. Do not create it around a real database.

Before first migration, the API verifies the backup and restore's legacy rows and compares the target's legacy schema/rows with the recovery point under a SQLite write transaction. A changed target requires a new verified recovery point. It migrates the original disposable fixture, never the quarantined restore.

Version 1 adds only:

- `chief_schema_migrations(version,name,checksum,applied_at)` with a pinned DDL checksum and aware UTC timestamp.
- `component_modes(kind,id,mode,revision,changed_at,actor,reason)` with a typed identity key, supported-value constraints, positive revision and aware UTC change time.

DDL and migration ledger insertion use one explicit transaction. An interrupted/failed attempt rolls back both tables. Retry returns ALREADY_APPLIED only for the exact known schema/checksum; partial, altered or unknown schemas are rejected. Concurrent requests serialize and apply once. Existing tables, rows, indexes, triggers, user_version, application_id, queued/approved work, run accounting, evidence and audit history are not converted. Migration itself appends no legacy audit row. Later mode changes append their own audit in the same transaction as the new state.

The migration tests exercise the unchanged B.5 backup/restore implementation before every migration fixture. Their populated schema is pinned to the fingerprint produced by the accepted B.5 drill. Synthetic release manifests in unit fixtures identify test context; they are not deployment source attestations. The separate C demonstration uses the actual accepted B.5 source manifest.

## Desired component controls

AgentRegistry still describes registered domains/agents and grants. CapabilityRegistry still describes capabilities/dependencies. Neither becomes the other. Two descriptive shared-service entries identify component controls and System Health; capability grants are unchanged.

New control contracts use the existing typed catalog Node and canonical modes. Current guarded agent and domain-owned capability adapters support ENABLED, DISABLED and MAINTENANCE. SHADOW is represented in the contract/persistence but rejected by current adapters because no safe simulation executor exists. Shared services, storage and domain aliases are read-only through the new mode API: unsupported transitions are rejected rather than partially enforced.

For existing domains, enabled/autostart/running remain authoritative in `agent_controls`. The new API describes domain intent plus all three flags and does not create a competing domain-mode record. Continue to change those flags through `/api/agent-controls/<domain>`. A runtime pause with autostart true still resumes on an actual startup, as in B.5; migration/composition does not call startup or resume work. A new ENABLED mode cannot override a legacy pause, disable or missing permission.

Persisted component restrictions are an additional guard. DISABLED/MAINTENANCE prevent new guarded work and defer claiming eligible queued work; they do not kill an already guarded external operation. Because current agent contracts declare their capabilities as required, disabling one required capability conservatively suspends new work by that consuming agent. Scheduler/reminder callers using the old AgentControls constructor also honor those restrictions. Existing records and commands are not deleted or rewritten to represent a pause.

Impact previews include graph impact, supported-adapter constraints and a token binding catalog content, generalized state revisions and legacy flags. Apply re-computes the preview inside BEGIN IMMEDIATE and rejects stale or altered previews. Dependency-affecting restrictions require confirmation. Unknown/unguarded consumers are rejected. An audit failure rolls back the state change. Heartbeats are not part of the intent token, so routine heartbeat writes do not continuously invalidate previews.

Additive local API surfaces (no UI redesign):

- GET `/api/component-controls`: desired state, authority, revision, supported modes and legacy flags.
- GET `/api/system-health`: observed health, provenance/freshness reasons and separately named desired mode.
- POST `/api/component-controls/preview`: kind, id, mode.
- POST `/api/component-controls/transition`: kind, id, mode, preview token, reason, confirmed boolean. Actor is the existing local user context; this is not a new RBAC system.

These endpoints inherit the existing local dashboard request restrictions. There is no migration/unlock endpoint. Existing endpoints and legacy status vocabulary remain unchanged.

## Observed System Health

Health is computed read-only and never changes a desired mode or control row. Observations carry source/receipt timestamps, probe/reason and extensible B.5 clock-quality metadata. Missing, invalid, naive, stale or excessive-future timestamps yield UNKNOWN. Probe failure also yields UNKNOWN, never inferred health.

Statuses are UNKNOWN, HEALTHY, DEGRADED and UNAVAILABLE. Declared dependency unavailability propagates conservatively; unknown dependencies prevent a fabricated healthy result. There is no automatic repair, unverified fallback or model/network probe. `safe_degradation` remains absent unless a future verified adapter supplies one.

The initial installed probe adapts the existing epoch heartbeat to aware UTC and reports only worker connectivity. A fresh heartbeat can make `chief.runtime` HEALTHY while browser and capability health remain UNKNOWN. It cannot certify Folio, deployment, login, delivery or submission. DISABLED/MAINTENANCE and a legacy pause are described separately as intentional suppression; no mode itself proves healthy or unavailable. The old domain heartbeat/status route retains its existing semantics.

## Versioned policy foundation

The generic contract defines READ, RECORD, ADVISE, LOW_RISK_AUTOMATION, PHYSICAL_FINANCIAL and HIGH_IMPACT independently of ALLOW/ASK/BLOCK. Unknown actions remain ASK regardless of a risk label. Packs pin semantic versions; duplicate versions of one identity are rejected. Resolution records matched/governing versions and conflicts.

Precedence is law/regulation > hard safety/security > professional/framework > Chief > installation/owner > agent preference > model recommendation. A hard prohibition cannot be overridden, including by a nominally higher ALLOW. At equal precedence, the more restrictive result wins. This is a contract/test foundation; no jurisdictional legal pack, professional certification or user policy editor is installed.

`domains/jobs/policy_adapter.py` owns the actual Job vocabulary and the `jobs.legacy` 1.0.0 provider. `JobComparisonGate` inherits the original `PolicyGate.execute` unchanged. Its decide method first calls the original gate's decide/context checks, compares the new engine, and returns the original decision. It never executes an operation for comparison or substitutes a candidate result.

Job factory injection passes this single gate to command, form-fill and final-submission executors. Normal MATCH, deliberate MISMATCH and COMPARISON_ERROR records include explicit version provenance where available and an aware UTC receipt time. Audit writes contain decision metadata, not action payloads. Audit failure is exposed through `audit_error` on the adapter and does not change the effective decision. The new engine is not promoted even if every test matches.

The complete equivalence matrix covers all 27 current Job actions plus an unknown action, five context cases (absent, valid, paused, missing grant, wrong domain), and both approval states: 280 cases. Deliberately permissive candidate/error tests separately prove that legacy BLOCK/ASK behavior remains effective and that the executor is not called twice.

## Rollback

Stop services before software rollback. Use the exact accepted B.5 archive, delivered as C's rollback package. Keep the earlier B archive as the historical reference; it is not interchangeable with B.5.

Before any new component mode is used, B.5 can read the additive database in an isolated compatibility check. Do not delete the new tables merely to roll code back. After a restriction is used, B.5 cannot enforce that new component mode, even though it can read the database. Reconcile restrictive intent with old enabled/autostart/running settings and keep services stopped until reviewed. A code-only rollback could otherwise resume work. Policy rollback is simpler because the old gate remains the enforcement authority throughout.

Do not restore old data blindly: post-snapshot submissions, emails, approvals and records require reconciliation. Preserve the untouched backup and the quarantined drill. No automatic down-migration, external replay, restore promotion or quarantine unlock is provided or performed. B.5 quarantine protections remain unchanged.

## Scope and unresolved acceptance

Only Steps 3–4 are implemented. Existing Farm placeholder behavior/data, candidate evidence, model routing, browser implementation and UI assets remain intact. No Evidence migration, Farm expansion/offline synchronization, runtime integration or later checkpoint is included.

Deployment backup/restore, actual volume/path/secret recovery, browser validation, privileged symbolic links, Docker/Folio builds and execution, real sign-in/submission and startup/reboot acceptance remain OPEN. Synthetic unit and migration demonstrations are development evidence only. The final report records exact baseline/candidate test outcomes without relabeling blocked tests as passes.
