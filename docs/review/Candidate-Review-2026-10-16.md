# October 16 candidate review — DRAFT / DO NOT MERGE

Restored the 13 backend candidate files from the supplied handover onto the verified
`main` baseline `be7b4b48a1e7462b8f4441387a92778b39597c95`. The handover's local
commit `b9a930d07fa4ba106bb9c92257d079ebcb80255c` is absent from GitHub; this branch
preserves its source snapshot, not its unavailable commit history. Source files
were used because the supplied Windows patches have UTF-16/line-ending issues.
Unchanged original line endings were retained to minimize diff noise.

All 35 handover checksums verified; the extracted folder matches the ZIP. Those
checks establish package integrity, not implementation correctness. No supplied
script was executed. No live storage, schema, service, deployment, or deferred
multi-company branch was changed.

## Merge blockers

1. **P1 — Treatment sales enforcement is bypassable.** `bookkeeping.append` only
   invokes the restriction check when the client supplies a flock. A correctly
   linked egg sale is rejected during an indefinite hold, but the same sale is
   accepted after removing details, removing flock_id, substituting an unregistered
   flock, or using output_type `OTHER`. `finance.details` accepts arbitrary output
   text and does not validate that the selected entity is an existing flock.
   The ordinary finance form also does not supply this new flock provenance.
   Define and enforce authoritative product/flock linkage without treating missing
   provenance as clearance; cover the normal UI and direct API paths.
2. **P1 — Unknown treatment status is not automatically a hold.** Recording a
   MEDICINE health event does not create a restriction or unresolved clearance
   state. `check_restriction` returns unrestricted until a separate hold is entered.
   The handover's claim that unknown withdrawal duration fails closed applies only
   to a manually created restriction with `active_until=None`, not all treatments.
   Resolve the intended workflow before claiming treatment-safe sales enforcement.
3. **P1 — Claimed qualification is not verified.** Every submitted restriction is
   stamped `QUALIFIED_INSTRUCTION`; source_document and reviewed_by are free text.
   There is no evidence-verification or reviewer-identity check. Preserve reported
   provenance and distinguish it from verified instruction before release.
4. **P2 — Existing implicit manager access also changes.** DEFAULTS=False applies
   to every manager with no permission ledger, including existing installations.
   Explicit grants are preserved. The current implementation has no clean-install
   versus existing-Farm adoption distinction; document and resolve that behavior
   before updating an existing installation.

## Independent validation

- 37 passed, 4 browser tests deliberately deselected: financial permissions,
  bookkeeping, opening debts, treatment restrictions, operational requirements,
  and existing health records. Local Python 3.14 environment; CI uses Python 3.12.
- 5 review-only probes reproduced the four sale bypasses and the absent automatic
  hold. Their passing status confirms the bugs; it is NOT safety acceptance.
- The 655 test runs in the supplied report were not independently repeated.
- Deferred PRs #32/#33 remain separate. Their green CI does not validate this code.

Commands (with CHIEF_TEST_SECRET_BACKEND=portable-aead):

```sh
python -m pytest -q tests/test_farm_financial_permissions.py tests/test_farm_bookkeeping.py tests/test_farm_opening_debts.py tests/test_farm_treatment_restrictions.py tests/test_farm_operational_requirements.py tests/test_farm_health_records.py -k 'not browser and not permission_page'
python -m pytest -q tests/review_candidate_probes.py
```

The review-only probe filename is excluded from normal pytest discovery. Once a
blocker is fixed, replace its diagnostic assertion with a rejection regression.
This PR is for review and remediation only; not deployment approval.
