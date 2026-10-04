# n8n workflow boundary and next implementation

Current support is connection control, workflow metadata inventory and an explicitly requested synthetic handshake with durable dispatch evidence. Enabling the connection does not publish or execute a workflow. A successful synthetic handshake is not runtime qualification. See n8n-handshake-setup.md for its exact limits.

Next, prove the supplied handshake on an isolated, pinned n8n installation before expanding to business workflows. Use n8n's authenticated production webhook mechanism and its response node; an HTTP acknowledgement alone must never be displayed as completion of the requested action. Keep a separate webhook credential from the inventory API credential.

Chief owns the schedule and approval for a Chief-originated workflow. Do not also enable an independent n8n timer for the same occurrence. Any schedule migrated to n8n needs an explicit ownership switch and rollback record. Do not retire the existing scheduler to get a first integration working.

Each handoff needs a stable operation ID, approved workflow/version binding, fixed allowed payload fields, deadline, originating domain and evidence reference. Never accept an arbitrary URL, shell command, destination address or runtime tool list from model output. n8n receives the minimum data needed for the selected operation, not general Chief database access.

Record dispatch intent before sending. A lost response or process interruption is an uncertain outcome, not proof the operation failed. Do not automatically retry consequential operations until the recipient's idempotency and reconciliation contract is demonstrated. Retry limits, failure alerts and operator review must be explicit. Keep accepted, running, completed, failed and unknown outcomes distinct.

Start with a synthetic, side-effect-free handshake on an isolated n8n installation. Follow with one explicitly approved internal reporting workflow. External notification delivery requires configured recipients and channel testing. Purchases, payments, equipment control and arbitrary job applications are outside this first workflow.

Required evidence: authentication denial; altered payload rejection; duplicate occurrence; lost response; interrupted sender; connection disable; domain disable; revoked approving identity; timeout; workflow mismatch; and safe rollback to existing Chief ownership. Mock-server checks do not replace an installed n8n check.

Possible later benefits: approved notification delivery, third-party connectors, bounded reporting workflows and operational failure monitoring. Camera workflows still require qualified camera ingestion and separate analysis/permission rules.

Official references checked on 2 October 2026:
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/workflow-development/
- https://docs.n8n.io/integrations/builtin/credentials/webhook/
