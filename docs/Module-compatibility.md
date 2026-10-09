# Module compatibility

Phase A8 adds a read-only, fail-closed compatibility evaluator for validated Chief agent manifests. It consumes the declarative `AgentManifest.requires` contract and produces a derived compatibility observation for A7 module lifecycle projection.

A8 does **not** install, activate, disable, update, download or execute modules. Runtime authority remains with the existing registries and component-control path.

## Current Chief compatibility profile

The first explicit compatibility profile is:

- Chief runtime: `1.0.0`
- Owner API: `1`
- Staff API: `1`
- Companion API: `1`

These values are an internal Chief contract used only for compatibility evaluation. They do not grant interface access or runtime permission.

## Chief version requirements

A manifest may declare an AND-only whitespace-separated comparator range, for example:

`>=1.0.0 <2.0.0`

Supported comparators are `>`, `>=`, `<`, `<=`, `=`, `==`, plus an exact bare `major.minor.patch` version. Unsupported or malformed syntax fails closed as incompatible rather than being guessed or widened.

## Interface API requirements

For each declared `owner`, `staff` or `companion` interface:

1. the corresponding `<kind>_api` compatibility declaration must exist;
2. the declared API level must be a non-negative integer string;
3. the level must exactly match the current Chief compatibility profile.

A `<kind>_api` requirement without a matching declared interface also fails closed. This prevents stale compatibility declarations from silently surviving interface changes.

## Lifecycle integration

Default Chief control-service composition attaches the A8 evaluator to A7 `ModuleLifecycle`.

- a compatible module continues through the normal A7 state precedence;
- an incompatible module projects `INCOMPATIBLE`, which remains the highest-precedence lifecycle state;
- client/caller compatibility evidence cannot override A8 when the evaluator is attached;
- compatibility output remains `authority = CHIEF_DERIVED` and is not executable authority.

Legacy A7 construction without an A8 evaluator still accepts trusted internal compatibility evidence so the lifecycle contract remains independently testable and composable.

## Failure model

A8 fails closed for:

- unsupported Chief requirement syntax;
- Chief version mismatch;
- missing interface API requirements;
- invalid interface API requirements;
- interface API version mismatch;
- API requirements that no longer correspond to a declared interface.

Evaluation is deterministic and side-effect free. It does not write component mode, health, manifest, update or lifecycle state.

## Future plan boundary

A8 deliberately stops at compatibility evaluation. The later module-management work remains separate:

1. **installer/inventory authority** — represent packages discovered or installed before activation instead of inferring installation only from current runtime manifests;
2. **module update pipeline** — discover candidates, verify package/source integrity, run compatibility gates, stage safely, preserve rollback/backup guarantees and only then expose a validated update to A7;
3. **persisted module inventory/state** — only when installation/update semantics are defined; A7/A8 do not create a parallel lifecycle database today;
4. **Owner/Staff discovery surfaces** — expose sanitized lifecycle/compatibility state without accepting browser-provided authority;
5. **operational deployment and end-to-end validation** — verify install/compatibility/update/activation paths against the deployed Chief runtime and PWAs.

Those phases must continue to preserve the existing `preview -> confirm -> transition` control authority for enable/disable operations and must not let lifecycle or compatibility labels become commands.
