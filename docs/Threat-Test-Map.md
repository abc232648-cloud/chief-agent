# Scoped risk-to-test map

| Risk | Evidence boundary |
|---|---|
| SQLite contention, full/read-only storage, interrupted write, connection leak | test_stage4_storage: real SQLite transactions, page-limit full condition, read-only URI, child-process crash; injected startup I/O errors explicitly synthetic |
| Duplicate action execution | test_stage4_actions concurrent claimed/unclaimed calls: exactly one synthetic sink call; existing test_worker_ownership covers OS process ownership/death |
| Revoked/stale/future/tampered approval or changed component permission | test_stage4_actions: no sink call; Chromium navigation-time revocation prevents filling and clicking |
| Incorrect final submission / ambiguous result replay | test_access_browser, test_final_submission, recovery and submission tests: reviewed fields, confirmation, persisted uncertain status |
| Request/work/ledger correlation leak or forged identifier | test_stage4_correlation: thread/exception cleanup, generated HTTP ID, reference-only audit linkage |
| Secret-bearing diagnostics or unbounded files | test_stage4_diagnostics: discarded payload/exception, actual rollover and bounded retained files; foundation secret/package tests remain mandatory |
| Domain/capability/evidence/approval bypass | Existing identity, domain, policy, evidence, CandidateFact governance, ledger and browser suites; Stage 4 never replaces these controls |
| Source/dependency substitution | Existing test_release_package + source/patch hash verification; exact inventories and hashed installation inputs; advisory lookup evidence is scoped to known Python advisories |

Passing functional tests is not independent penetration testing or deployment certification. Evidence must name the exact final source and each platform's passes/failures/skips; OS-specific skips must be matched to native execution or remain untested. Physical Folio, actual providers, real backup/restore, installed containment and offline device mechanics remain separate acceptance gates.
