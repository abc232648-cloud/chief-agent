# Module lifecycle

Phase A7 adds one read-only Chief lifecycle projection for agent modules. It does not add a lifecycle database table and it does not replace the existing component-control, health, manifest, compatibility or Update Center authorities.

## Public states

The lifecycle contract is exactly:

- `NOT_INSTALLED`
- `INSTALLED`
- `DISABLED`
- `ENABLED`
- `DEGRADED`
- `UPDATE_AVAILABLE`
- `INCOMPATIBLE`

These are presentation/discovery states. They are not executable commands and do not grant permission to change a module.

## Existing authorities remain authoritative

A7 derives lifecycle state from existing Chief systems:

| Input | Authority |
| --- | --- |
| installed runtime module + version | validated `ManifestRegistry` |
| runtime enable/disable intent | `ComponentControls` / existing audited component-mode state |
| observed runtime condition | `SystemHealth` |
| incompatible evidence | A8 `ModuleCompatibilityEvaluator` in default composition |
| validated update candidate | existing Update Center or a future module updater using equivalent gates |

A7 never writes `component_modes`, never creates a lifecycle table and never activates an update.

A future Owner UI that enables/disables a module must continue to use the existing dependency-aware `preview -> confirm -> transition` control path. A client-provided lifecycle label is never authoritative.

## State precedence

For a validated installed module, the projection uses this precedence:

1. explicit trusted incompatible evidence -> `INCOMPATIBLE`
2. installed but not yet activated -> `INSTALLED`
3. existing desired mode other than `ENABLED` -> `DISABLED`
4. observed health `DEGRADED` or `UNAVAILABLE` -> `DEGRADED`
5. trusted validated newer candidate -> `UPDATE_AVAILABLE`
6. otherwise -> `ENABLED`

`UNKNOWN` health is retained as `health=UNKNOWN`; it is not relabeled as a failure. This follows the existing health contract, where missing, stale or unusable probe evidence remains unknown.

`DISABLED` also covers existing intentionally suppressed runtime modes such as maintenance. The underlying `desired_mode` is preserved in the snapshot so callers can distinguish why the lifecycle state is disabled.

## Installation boundary

The current `ManifestRegistry` only accepts manifests that match an already-installed runtime domain and agent. Therefore A7 does not claim that Chief can enumerate arbitrary packages that are not installed.

- requesting an unknown module id returns `NOT_INSTALLED`
- `INSTALLED` is representable through trusted activation evidence for the future installer/inventory path
- default current runtime modules are treated as activated unless a trusted installer layer says otherwise

A later installer/discovery phase may provide real installed-before-activation inventory without changing the public lifecycle vocabulary.

## Update and compatibility boundaries

A7 intentionally does not perform version comparison, candidate download, staging, migration, backup, approval or activation. `validated_update_version` is a trusted internal observation only after the existing Update Center (or an equivalent future module updater) has validated the candidate.

Phase A8 now owns compatibility evaluation through the read-only `ModuleCompatibilityEvaluator`. Default control-service composition supplies that evaluator to A7, so `compatible` is derived from the validated manifest's Chief/API requirements and cannot be overridden by caller evidence. Standalone A7 construction without the evaluator retains the legacy trusted-evidence seam for isolated composition/testing.

## Output

Each snapshot includes:

- module id
- runtime agent id, when installed
- installed manifest version
- lifecycle state
- underlying desired mode and revision
- exact observed health status
- compatibility observation, when evaluated
- validated update version, if supplied
- bounded reason
- `authority = CHIEF_DERIVED`

The projection is suitable for later Owner/Staff module discovery APIs, but A7/A8 themselves add no browser endpoint and accept no client-supplied authority.
