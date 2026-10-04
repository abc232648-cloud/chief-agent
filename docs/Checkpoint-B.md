# Checkpoint B — capability declarations and dependency analysis

This checkpoint introduces a separate Capability Registry. `agents.registry.AgentRegistry`
continues to describe registered domains and agents; its class and `describe()` output are
unchanged. The two registries are not aliases, subclasses, or interchangeable APIs.

## Current implementation

`application.composition.default_catalogs()` returns `ApplicationCatalogs(agents,
capabilities)`. `default_registry()` remains a compatibility factory returning only the
AgentRegistry. Existing entry points therefore validate the default capability declarations
at composition time, while their execution, control and dashboard APIs remain unchanged.

- `capabilities.contracts`: immutable capability, component, consumer, dependency and
  version declarations. Contract versions use strict major.minor.patch; prereleases are
  deliberately rejected. Dependency ranges are inclusive minimum/exclusive upper bound.
- `capabilities.registry`: rejects duplicates, missing references, unknown owners/consumers,
  invalid statuses, incompatible dependencies and permission declarations outside supplied
  grants. Snapshots expose read-only mappings and detached JSON descriptions.
- `capabilities.graph`: deterministic forward/reverse traversal, topological order and
  change impact. Explicit dependencies, health dependencies and consumption are graph edges.
  Ownership additionally propagates change impact, but is not an execution edge; owners can
  consume their own capabilities without introducing an artificial cycle.
- `capabilities.regression`: selects declared capability/component tests plus every required
  affected consumer's tests. Unknown changes and changes with no required coverage fail.
  Optional-consumer tests remain separately visible. Paths are project-relative test modules.
  The coverage checker rejects missing/failed/skipped/blocked results and evidence for a
  different plan/candidate. Callers must still execute fresh tests and authenticate results.
  This is not the Step 10 updater, automatic regression runner or release certificate.

## Existing capability adapters

The four unchanged permission strings are described by domain-owned adapter modules:

- Job: `jobs.execute`.
- Farm placeholder: `farming.records.read`, `farming.records.write`, `farming.reminders.write`.

The application composition layer declares four existing shared mechanics:
`chief.agent_controls`, `chief.domain_storage`, `chief.domain_dispatch` and
`chief.reminder_delivery`. These names describe current implementations; they do not create
new WorkerContext grants. Core declarations do not import Job or Farm. Domain adapters
import Core contracts, never other domain internals.

Known consumers include the current Job/Farm agents, dashboard and scheduler. The graph
describes these declared surfaces, not every conceivable future capability or a complete
automatic import inventory. Providers must add contracts and regression declarations for
new surfaces. Domain registration checks that capability declarations exactly cover existing
agent grants and declared actions; it does not infer dependencies from permission names.

Contract `1.0.0` labels this adapter interface, not the application release, Python version or
a vendor runtime version. `LIMITED` avoids implying full deployment certification.
`LEGACY_CONTROLLED` means existing enabled/running/autostart controls remain authoritative.
Other mode/maturity enum values are validated metadata for later checkpoints; SHADOW and
MAINTENANCE execution semantics and a live health service have not been implemented.
Health dependencies declare what to assess; this catalog does not report live health.
Framework/model/data-access fields describe current boundaries and do not certify legal
frameworks, grant access, change model selection or create shared evidence storage.

## Read-only usage

From the source directory:

```sh
python -m application.capability_catalog
python -m application.capability_catalog --changed capability:chief.domain_storage --candidate SOURCE_SHA256
python -m application.capability_catalog --changed component:jobs.worker --candidate SOURCE_SHA256
```

The first command exports declarations. The others return an impact/test plan and a plan ID
bound to the candidate identity, catalog and requested changes. Supply the real tested source
digest; the library cannot independently prove that a caller's candidate label or results are
truthful. No command starts workers, changes settings, contacts providers or executes tests.

Programmatic example:

```python
from application.composition import default_catalogs
from capabilities.contracts import Node
from capabilities.regression import regression_plan

catalogs = default_catalogs()
plan = regression_plan(catalogs.capabilities,
                       [Node('capability', 'chief.domain_storage')],
                       candidate='actual-source-digest')
plan.validate_test_files('.')
# Execute plan.required_tests against that source, then supply fresh module results.
# Any required browser module that cannot run must be BLOCKED, never PASSED.
```

Shared changes conservatively select both domains, dashboard and scheduler where affected.
Job-only worker changes do not select Farm consumers. Required browser tests remain in the
plan even when the current environment cannot run them.

## Preservation, packaging and remaining gates

Permission enforcement, Job approval/provenance/recovery, AgentRegistry, database schemas,
domain actions, UI assets and provider configuration are unchanged. No database migration.
The Dockerfile now copies `application` (missing after Checkpoint A) and `capabilities`.
Static packaging tests verify these paths; an actual image build/run is still unverified.

Checkpoint A is accepted for progression, but its unresolved browser/Folio validation remains
OPEN. Checkpoint B does not clear real-browser repeats, symlink privileges, Linux descriptor
checks, real Folio sign-in/submission, startup/reboot or container acceptance. Keep the
original tests and report fresh failures rather than treating a passing subset as full acceptance.

Step 3 controls/health, the policy/evidence/model migrations, operational Farm expansion and
runtime integration are not part of this checkpoint. Step 11 remains a future runtime
qualification gate. Shared-capability promotion remains an explicit ownership/contract/test
review, never an automatic rename or a reason to move domain semantics into Core.

Rollback restores Checkpoint A software from its preserved source archive. Keep existing
private databases/configuration/documents/sessions separate. No state migration needs to
be reversed. The Checkpoint A archive itself predates the Dockerfile copy correction; do
not interpret its restoration as container deployment certification.
