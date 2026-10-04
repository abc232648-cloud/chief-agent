# Checkpoint B.5 — operational backup/restore foundation

This checkpoint adds private SQLite snapshots, explicitly selected associated files, verified isolated restores and a small UTC time contract. It does not implement Checkpoint C or migrate the operational schema. It does not promote a restored system into service.

## Baseline and state

Accepted B source archive SHA-256: `5abafd70ec817e8bdd02245d662a95b8bda25dd91dc1041cc843619e914494d9`. Accepted B source tree: `1fb587770fa061f285037fec13aaf20c9201aa9f528c82831704825e791c9126`. Preserve the archive unchanged. B's older Rollback ZIP is A; it is not the B.5 rollback.

The main database is normally `database/worker.db`. Entry points differ in whether they honor `JOB_WORKER_DB` and resolve relative paths against the installation or working directory. Establish the actual deployment paths before running operational backup. B.5 does not silently change those conventions.

Back up the entire database. It holds Job work, commands, approvals, applications and receipts; CV/fact/provenance and application history; audit and recovery attempts; Chief controls, run accounting and heartbeat/scheduler state; domain records/reminders/requests; notifications/preferences, monitoring and site-access metadata. Optional and future tables are included automatically. Existing state values are not converted.

Associated files need separate inventory: uploaded/registered CVs, generated drafts, logs/reports, notification outbox, source configuration and installation/service overrides. Their database paths may be absolute or relative. The snapshot preserves those references; isolated restore does not rebind them or infer that all dependencies were selected. Database-only and database-with-selected-files are explicitly different coverage levels. Neither is automatically a complete deployment backup.

`.env`, `email-settings.json`, browser `site-sessions`, credential/cookie/session files and environment secrets require separate protected recovery or re-provisioning. Although the email settings dataclass docstring says credentials are environment-only, the actual settings writer can persist SMTP_PASSWORD beside the database. Treat the code as authoritative. Sessions may expire or be machine/provider-bound; do not promise portability. Databases and documents may themselves contain sensitive text. All operational backup storage is private; no general content secret scanner or encryption implementation is claimed.

## Operator procedure

Use an operator-controlled private destination parent on encrypted storage with verified ACLs. Confirm these outside the application; `--private-storage-confirmed` is an assertion, not an ACL or encryption check. Do not use the ordinary source/evidence output folder for real operational packages. Do not allow untrusted processes to alter input files or destination directories during capture/verification.

The following are command templates, not paths discovered on the user's deployed machine. Run from the B.5 software root. Use the release manifest corresponding to the software whose operational state is being captured. Manifest verification checks each listed source file and the tree digest; it does not attest which binary is running, inventory unlisted plugins, or infer a database's source lineage.

```
python -m operations backup <database> <new-private-backup-directory> --source-root <release-root> --source-manifest <release-manifest.json> --private-storage-confirmed
```

SQLite's online backup API reads committed WAL data consistently without copying live WAL/SHM files or calling application startup. Concurrent committed writes may fall on either side of the snapshot point; a backup start/completion interval is recorded. A monotonic deadline bounds the SQLite copying operation. This is not a global deadline for hashing large files or checking database integrity.

For a coordinated database/file recovery point, stop all relevant writers (worker, dashboard mutations/uploads, scheduler, monitoring, reports and external file editors) and keep them stopped through completion. Supply `--writers-quiesced --assets-manifest <private-selection.json>` in addition to the backup arguments. Example selection using synthetic paths:

```json
[
  {"path": "/private/documents/example.txt", "logical_path": "documents/example.txt", "kind": "document"},
  {"path": "/private/config/sources.json", "logical_path": "configuration/sources.json", "kind": "configuration"}
]
```

Kinds are document, provenance or configuration. Paths are explicit files; there is no recursive include. Known secret/session path categories, unsupported file types, traversal, links/junctions/reparse points and duplicate identities are rejected. The caller is responsible for reviewing actual content for secrets. Missing or changing selected files fail capture. Per-file hash comparisons cannot prove global writer quiescence; the flag records the operator assertion.

The result prints a backup UUID, manifest SHA-256, verified source-tree identity, schema fingerprint and coverage. Save this receipt to a separately trusted inventory. The published directory includes a manifest, COMPLETE marker, standalone `state.sqlite3` and selected `assets/`. Each payload has a size and SHA-256. Metadata includes actual SQLite schema_version, user_version and application_id, SQLite runtime version, per-table counts and UTC timestamps. A user_version of zero means the original software did not provide a migration version, not that a migration system has been installed.

```
python -m operations verify <backup-directory> --manifest-sha256 <trusted-pin> --expected-source <trusted-source-tree> --expected-schema <expected-schema-fingerprint>
python -m operations restore <backup-directory> <new-private-isolated-directory> --manifest-sha256 <trusted-pin> --expected-source <trusted-source-tree> --expected-schema <expected-schema-fingerprint> --private-storage-confirmed
```

Verification checks the trusted manifest pin, format, exact file inventory, payload hashes, schema/version/count metadata, SQLite integrity and foreign keys. Unexpected sidecars are rejected. Hashes protect integrity relative to the trusted receipt; they are not signatures against an attacker who can replace both backup and receipt. No existing destination is overwritten. Caught failures remove only the operation's own staging directory. A hard kill may leave `.incomplete-*`; it is not accepted as a published backup and must be handled separately. Staging publication uses a same-parent rename. Power-loss, filesystem/volume durability and network-filesystem semantics require deployment testing; fsync is not an off-host backup.

No retention deletion is automatic. Keep pre-migration recovery points and pins until a replacement has been independently restored and accepted and the deployment retention window has elapsed. Set that window, off-host copy policy, encryption/key recovery, capacity alerts and deletion ownership during deployment. Never discard the last verified recovery point because an incomplete directory exists.

## Isolated restore and promotion boundary

Restore uses read-only backup inspection and data copying only. It does not import Store, start the dashboard/worker/scheduler, load deployment credentials, send email, open a website or submit anything. All records, including QUEUED, APPROVED and EXECUTING states, remain exactly preserved. The copy is not runnable: a sidecar quarantine marker and reserved SQLite application_id `0x43485251` reject normal B.5 Store construction and connections. The database marker survives a simple copy/rename without the sidecar. Original application_id is saved in the restore receipt. No tables, columns, row values or user_version are changed.

This is an application tripwire, not OS isolation. No unlock/promotion command is provided. Raw SQLite tooling, deliberately clearing the markers, custom execution that avoids Store, or running old B software can bypass it. An OS-isolated, outbound-denied environment with stopped services and no usable credentials is required for a real restore drill, especially with old software. Retain both markers throughout verification. Do not run arbitrary restored HTML/documents as active content.

Before any separately approved operational promotion:

1. Verify the actual backup coverage, deployment paths, source/schema identity, secret recovery and installation/service settings.
2. Reconcile queued, approved, executing and submitted work against external outcomes after the snapshot. Restoring an old approval never authorizes automatic replay. Uncertain outcomes require review; never resubmit solely because a receipt is absent from an older snapshot.
3. Reconcile file references and current permissions/control/autostart settings. Preserve the untouched snapshot and record every reconciliation change.
4. Establish an explicit process for removing quarantine and restoring the original application_id only on the reviewed promotion copy; validate it under the real service manager before enabling any delivery or submission. This foundation deliberately does not automate those actions.

Software rollback to B: stop B.5 services and restore the immutable accepted B source. On the original operational database, B.5 introduces no schema/data migration to undo. Do not point old B at a quarantined restore: B does not know the new guard. Data restoration is a separate operation requiring the reconciliation above. Never delete additive future tables or restore an older database just to undo source code.

## Time contract

`operations.time_integrity` normalizes aware datetimes/offset strings to UTC ISO-8601 with Z and rejects naive storage inputs. `TimeReceipt` separates received_at from optional source_at; `timestamp_status` reports MISSING, INVALID, STALE, FUTURE or CURRENT with explicit tolerance and injected current time. None of these states implies health or authorizes an action. ClockQuality carries optional clock identity, quality, offset and uncertainty; default UNKNOWN is not a claim of synchronization. Negative uncertainty and nonfinite estimates are rejected.

Use monotonic clocks for deadlines. Legacy SQLite CURRENT_TIMESTAMP text and epoch values remain unchanged; future adapters must explicitly interpret those legacy formats. No data rewrite, drift measurement, NTP service, Farm offline clock reconciliation or full synchronization system is implemented.

## Validation limits

Synthetic tests cover committed/uncommitted WAL and concurrent transactions; timeout/interruption/failed publication; corrupt payload/manifest/schema rejection; source identity; explicit file handling; exact state preservation; quarantine and worker-start blocking; offline verifier behavior; and UTC/freshness contracts. Full historical regressions must be reported separately from new checks.

Sandbox tests use the documented inherited-ACL test harness needed by the restricted Windows process. They do not certify production confidentiality or ACLs. Browser, actual privileged symbolic links, Docker/Folio execution, live sign-in/submission, host startup/reboot, real credentials, deployed backup coverage and deployment restore acceptance remain open. No Checkpoint C work starts automatically; deployment backup/restore acceptance remains a separate gate.
