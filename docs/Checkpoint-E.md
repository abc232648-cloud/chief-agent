# Checkpoint E — model routing and shared runbooks

Only Steps 7–8 are implemented. Accepted D source SHA-256: `f8f1d577864977bb9dd914789e5fbaed475cc98ab4398360699c50ee827bfb01`; tree: `6562809e72e986d7006610227763867b87c14f6e646031b2c7f9631fb8402320`. The exact E-to-D software rollback is a copy of D_Source.zip, not D_Rollback_to_C.zip. No operational migration, quarantine promotion or later checkpoint is authorized.

## Distinct model contracts

Provider identifies a transport and its adapter version. Model identifies a provider/model configuration, contract version and declared cost class. Provider credentials remain in the existing environment-backed provider constructors; the registry never persists them. Definitions are immutable application-side catalog objects, not installed models or runtimes.

Global state is ENABLED, SHADOW or DISABLED. Domain/agent assignment is a separate immutable ordered model list and routing profile. Installation policy is a restrictive allowlist plus free-only and explicitly named legacy-compatibility rules. Persisted settings take precedence over defaults and are read at each request. Assignment writes are restricted to the current domain/agent context. Installation-wide state/policy writes are trusted local configuration APIs, with explicit actor/time, and have no HTTP endpoint; they do not pretend to implement the deferred RBAC layer.

The router retains `generate(AIRequest) -> AIResponse`. It checks active domain/agent capability, assignment, global state and installation policy before dispatch, then revalidates the response against current restrictions and assignment. A global disable or shadow state prevents live use even if an assignment is stale. A configuration invalidated during inference discards the response. Responses naming a different model or invalid content stop rather than falling back. Only ProviderUnavailable triggers the next eligible route. A transport is a trusted text-generation adapter, not an action executor.

`gateway.main.build_gateway(store=...)` and worker startup use the explicit compatibility profile. Existing calls without a store remain valid for the standalone gateway smoke interface. No tables are created by routing. Without the E schema, the configured Job baseline remains Qwen/Groq primary, then the configured Mistral fallback on ProviderUnavailable. The original FreeOnlyGateway, provider implementations, AIRequest and AIResponse are unchanged and retained as compatibility/reference code. Direct construction of that old adapter does not consult E persistence; the configured worker uses ModelRouter.

**The Mistral/free-only discrepancy remains OPEN.** Its new cost class is LEGACY_UNRESOLVED, not certified FREE. The compatibility exception is limited to the exact configured Job primary/fallback pair and jobs/jobs-worker profile. Existing FREE_ONLY=FALSE and MISTRAL_PAID_ALLOWED=TRUE guards still stop before provider construction. New PAID and UNKNOWN routes are blocked; disabling the legacy exception is possible only through a separate explicit installation-policy change, not migration. No live provider billing, availability or model-quality qualification was performed. The initial Qwen free declaration preserves the accepted configuration assumption, not a new provider billing guarantee.

## Shadow evaluation

Shadow evaluation is initially offline: it accepts prepared text-only inference results under a matching active domain/agent context, computes equality, lengths and hashes, and records metadata plus a reference-only Decision Ledger event. It never dispatches a provider, invokes a handler, returns a live AIResponse, or changes facts, approvals, assignments or application state. Live routing rejects SHADOW models. Callback/action objects are rejected. No prompt/response text is persisted by the evaluator. Model performance evaluation and automated shadow inference orchestration remain outside this initial foundation. Comparison metrics are not evidence verification or permission.

## Shared SOP mechanics and domain ownership

Definitions contain a domain, immutable semantic version, start step, action reference, deterministic branches, prerequisites, evidence requirements, approval points and an optional failure branch. Cycles and arbitrary code strings are rejected in E. Domain-owned bindings provide the implementation, capability, risk, version and implementation reference. Definitions cannot create bindings, grant capabilities, override policy or assert facts.

Runs pin the definition digest/version, binding contract identities and evidence IDs. Publishing a new version does not change a historical run; changing a pinned binding sends the run to REVIEW. Reproducibility means preserved definitions, input references, branch history and declared implementation versions, not identical future model outputs or protection against a trusted developer lying about an implementation identity.

Execution checks the existing Capability Registry and WorkerContext, current Policy Engine, live shared Evidence quality and a domain-owned approval resolver. Prerequisite semantics and approval storage remain domain-owned. Approval references are validated for the exact run/step/definition digest and stored in append-only SOP events; ledger entries reference those observations. Approval does not bypass later policy blocks, capability loss or adverse evidence. Missing required evidence or incomplete quality produces REVIEW. The engine does not reinterpret Job CandidateFact lifecycle or generic verification as Job confirmation.

Only declared local READ/RECORD/ADVISE bindings execute in E. External or higher-risk bindings go to REVIEW even with an approval reference. Installed Python bindings are trusted code; this is not an OS sandbox against a malicious installed handler. Fixtures use only synthetic local handlers, with no website submission/email integration.

An optimistic revision check and SQLite transaction claim RUNNING before invocation. The Decision Ledger intent must be recorded before a handler is called; failure leaves REVIEW without execution. If interruption or outcome recording fails after a possible effect, RUNNING remains ambiguous. Explicit `recover` converts it to REVIEW, never replays it. Repeated advance on RUNNING, REVIEW or terminal state invokes no handler. READY and approval-paused runs can be resumed with all checks repeated. Low-risk failure branches are explicit separate steps with new checks; no automatic loop runs. Ledger outcomes and SOP events preserve branch/audit history.

The mature Job pipeline is unchanged and does not import or depend on the runbook engine. No Farm functionality, scheduler SOP conversion, RBAC, UI redesign, runtime integration or Hermes/OpenClaw qualification was added. A model layer is not an agent runtime layer.

## Persistence and migration

Eight additive tables:

- chief_execution_migrations: independent E schema version/checksum/UTC metadata.
- model_states: installation-wide model state, actor and time.
- model_assignments: domain/agent-specific route settings, actor and time.
- installation_policies: explicit installation restrictions, actor and time.
- shadow_evaluations: append-only comparison metadata without prompts/responses.
- sop_definitions: immutable domain/id/version/digest/definition records.
- sop_runs: current pinned run state and concurrency revision.
- sop_events: append-only transitions and validated approval references.

Eight UPDATE/DELETE rejection triggers protect the E schema ledger, shadow metrics, SOP definitions and SOP events. Configuration and current run rows remain mutable by their explicit service APIs. This is not cryptographic administrator-proof storage. Previous schema/rows, including D evidence and Decision Ledger history, remain unchanged by migration. C and D migration files are byte-identical.

`database.execution_migrations.migrate_isolated` requires the development marker, matching pinned backup identities and an intact quarantined B.5 restore. Every test fixture backs up/restores each preceding migration state and creates a fresh verified populated D recovery point before E. Unit fixtures explicitly use synthetic source identities; a separate demonstration pins the actual accepted D manifest. DDL and its ledger entry commit atomically under BEGIN IMMEDIATE. Interrupted/failed transactions leave exact D state; retry validates the schema. If legacy state changed after backup, take a fresh verified recovery point. No automatic migration or production migration command exists.

## Rollback and open acceptance

Stop development workers. Preserve E source/data for inspection and extract the exact D software archive in a separate checkout. D can read the additive schema, but ignores E global model restrictions and SOP state; reconcile both before resuming anything. Do not treat software rollback as data restoration or replay permission. A B.5 data restore is a separate isolated quarantined operation, never a file overwrite on an open WAL database. No unlock/promotion is provided or authorized.

Ordinary archives contain only source and safe summaries, not fixture databases, provider credentials or browser sessions. The inherited-ACL sandbox test harness does not certify deployment filesystem security. Browser, symbolic-link, Docker/Folio, real deployment backup/restore and real operational migration acceptance all remain OPEN. The Mistral policy discrepancy needs a separate explicit policy decision before its exception is removed or changed. Stop after E.
