# Optional planning and guidance checks

This slice extends the October 1 progress candidate. It does not remove the Farm
production guard, migrate a database or configure real external messages/providers.

## Budgets and forecasts

Open Bookkeeping, then **Budgets and forecasts**. All six areas are available:
egg production, feed duration, sales value, customer receipts, supplier payments,
operating expenses. Inputs are optional; blank is unknown and explicit zero is zero.
The operating-cost budget is optional. Record a basis explaining assumptions,
dates, exclusions and supporting records. Money is entered in the selected currency,
not kobo/cents; storage and calculations retain exact units.

These are human-entered scenarios, not automatically learned predictions. Egg
production is a constant daily estimate multiplied by inclusive calendar days,
rounded down to whole eggs. Feed assumes constant use and no deliveries/losses.
Sales uses a separately entered count of eggs expected to sell and unit price;
the system never assumes all collected eggs are saleable. Receipt/payment/cost
figures are explicit period estimates. Operating expenses and supplier payments
can overlap and are never combined into a misleading total. No cash balance or
profit is inferred.

An authorized financial reader may preview. Only a Farm Owner may save. Saved
plans preserve input, calculation version, original result, actor and receipt time.
An older plan can be copied and saved as a new version. There is no delete/update
of the original. Changed journal/bookkeeping records flag review of saved plans;
this broad marker does not certify record coverage or automatically derive revised
assumptions. Other planning saves do not change that evidence marker. A record
change between preview and save requires a new preview. Competing saves use a
revision check; same-ID retries are idempotent even if later records arrive.

Dates span 1–366 inclusive Lagos calendar days. APIs:

- GET `/api/farm/planning?offset=0` — 20 saved plans per page.
- GET `/api/farm/planning?revision=<id>` — one original saved plan.
- POST `/api/farm/planning/preview` — read-only calculation (CSRF still required).
- POST `/api/farm/planning` — Owner-only append, no financial/stock effects.

The existing domain_records table is used; no schema DDL. Neither forecasts nor
budgets approve purchases, execute payments, generate debts, control equipment or
dispatch notifications. Period targets can be added without the missing Owner
questionnaire; valid real forecasts still require confirmed farm inputs.

## Costing presentation

Cost settings accept currency amounts such as NGN 123.45 per kg. Reports display
currency, including four decimal places per egg, with exact integer/decimal-based
rounding rather than JavaScript floating-point conversion. Underlying valuation
remains provisional. Saleable egg cost is still unavailable without production-cohort
attribution. Price references remain reported sources, not independent verification.

## Farm guidance and diagnostics

The live prompt now relies on authorized schedule status rather than asserting
all schedules are unconfigured. A General Manager whose financial access is
disabled retains operational guidance without bookkeeping context. Existing
in-flight permission/data rechecks and advisory-only boundaries remain.

`tests/fixtures/farm_answer_benchmark_v1.json` is a versioned synthetic baseline.
It checks built-in instructions, unknown balances, Lagos dates, absent arithmetic,
and refusal to invent action authority. Mocked-provider tests inspect permitted
context and prompt constraints. They do NOT measure a real model's answer quality,
medical reliability, source citation accuracy or provider account qualification.
Those require separate explicit live acceptance and reviewed examples.

Guidance responses carry a trace_id. When deployment diagnostics are configured,
the existing rotating safe log records a closed outcome (BUILTIN/LIVE/BLOCKED/FAILED),
correlation reference, bounded duration and model alias (builtin/farming.qwen/none).
No question, answer, farm record, username, key, provider error body or arbitrary
model label is logged. The alias is not proof of a particular provider registration
or successful qualification. Existing diagnostic limit: 1 MiB plus three rotations;
audit retention is separate. Filtering applies at both the logger and file sink.

## Browser identity and remaining acceptance

The user selected a move to Chromium. Do not silently select installed Google Chrome.
This host's Playwright `chromium` cache identifies itself as **Google Chrome for
Testing 153.0.8010.12**. Results on it are labelled accordingly, not counted as an
independent Chromium-build pass. No standalone Chromium executable was found here.

The existing `CHIEF_BROWSER_RUNTIME` supports an explicit absolute executable,
SHA-256 and version with Chromium sandbox enabled. Supply/qualify the intended
Chromium build on each installation; do not reuse a Google Chrome hash or bypass
the sandbox on failure. Physical-phone PWA and installed-host acceptance stay
separate from headless regression checks.

Still pending: full installed Windows/Linux qualification of this changed source,
real notification delivery, owner-specific configuration, saleable-cost attribution,
further finance/contact/history scaling and complete human-facing audit navigation,
actual provider answer evaluation, final scoped pentest and production acceptance.
Sensors, physical automation and detailed shared-cost allocation remain deferred.
