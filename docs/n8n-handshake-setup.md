# Isolated n8n handshake setup

This candidate supports a synthetic workflow handoff in addition to inventory listing. It is not a general workflow runner. The handoff sends only a generated operation ID, domain identifier, contract, expiry and fixed synthetic purpose. It never sends farm records, user contact details or the inventory API key.

## Operator setup — separate test installation first

1. Install a supported, pinned n8n release in its own private state directory. Bind it to an explicit `127.0.0.1` port. Do not reuse Chief's database or a production n8n instance for initial qualification.
2. Import `integrations/workflows/chief-handshake-v1.json`. It is inactive and contains only Webhook, validation Code, and Respond to Webhook nodes. It has no timers or business-action nodes.
3. Create an n8n Header Auth credential with header name `X-Chief-Handshake-Key` and a randomly generated value of at least 32 characters. Assign it to the Webhook node. Do not disable authentication to work around setup errors. The export deliberately includes no credential IDs or secret values.
4. Configure private Chief service settings: `CHIEF_N8N_URL` with the local base address and port, `CHIEF_N8N_API_KEY` for inventory, and a **different** `CHIEF_N8N_HANDSHAKE_KEY` matching the webhook credential. Do not paste any keys into chat or source files. Use private n8n encryption-key and account configuration appropriate to the installed release.
5. Review the imported nodes and publish only this synthetic workflow in the isolated service. Chief's fixed endpoint is `/webhook/chief-handshake-v1`; it does not accept caller-supplied URLs. Publishing and installing n8n are not performed by the Chief toggle.
6. Sign in to Chief as an installation Owner/Administrator. In Integrations, enable the connection. Select a running test domain, confirm the Chief password and run the synthetic test. Refresh test history to inspect the receipt.
7. Check unauthorized webhook calls are rejected, expired/altered requests fail, and a successful response is recorded as COMPLETED. A plain HTTP 200 or “workflow started” acknowledgement is insufficient.

The webhook service may record request information: keep it private and verify retention settings in the pinned n8n release. The template requests no saved successful, failed or manual execution data. The code node requires an available n8n JavaScript runner; do not weaken runner isolation to make it execute.

## Recovery and exact limits

Chief durably records dispatch intent before network I/O, with an audit reference. Duplicate requests with the same operation ID do not send again, including after a crash or lost response. If no verified result returns, status is UNKNOWN; no automatic retry or completed-business-action claim follows. A pending request older than its deadline displays UNKNOWN. The UI can start a new synthetic test deliberately; that is a new operation.

Disable prevents new handoffs. A handoff already admitted can finish. It cannot be recalled by disabling the connection. This is acceptable only for the side-effect-free synthetic contract. Other workflow types need their own approval, idempotency and cancellation/reconciliation design.

History is bounded to 500 records and displays the most recent 50. No history deletion, arbitrary workflow execution, callbacks, external notifications, financial actions, schedule migration, camera integration or hardware control is implemented here. Existing Chief automation remains authoritative and unchanged.

## Evidence status

Fake-receiver tests prove Chief's transport, audit, refusal and no-resend behavior. JavaScript tests validate the template's request contract. Browser tests exercise the Chief controls. These do **not** prove import/publish/execute compatibility with a real n8n installation. Installed n8n qualification remains OPEN until the exact release is exercised. No n8n or Docker executable was present in this session; the bundled Node runtime also had no npm command. No system package installation or deployment was attempted.

Official protocol references:
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.respondtowebhook/
