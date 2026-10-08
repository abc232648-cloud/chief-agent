# Durable local release trust

The operator CLI `python -m application.release_trust` manages public-key trust in a dedicated private directory, separate from the operational Chief database. No signing private key is generated or stored. No service, update or runtime is activated by these commands.

Provision an existing absolute directory owned by the operator. Linux checks owner-only permissions; Windows requires independently configured current-user ACLs. The confirmation flag is not ACL certification. Never place this policy in an extracted release or let a downloaded package choose its trusted signer. This mechanism does not protect against compromise of its owning OS account or administrator.

Commands:

- `initialize --root ABSOLUTE_PRIVATE_DIRECTORY --confirm-private-storage --operator LOCAL_OPERATOR_REFERENCE --reason REASON` creates an empty policy exclusively. Existing or interrupted files are refused, not overwritten.
- `inspect --root ABSOLUTE_PRIVATE_DIRECTORY --confirm-private-storage` shows the current revision, public keys, permanent revocations and accepted sequence floor.
- `enroll` or `revoke`, with the same storage/operator flags, `--expected-revision CURRENT_REVISION` and `--public-key RAW_PUBLIC_KEY_FILE`, records an explicit decision. Verify that 32-byte public key independently before enrollment. A revoked key cannot be re-enrolled; rotate by enrolling a different key, then revoking the old one after independently reviewing the overlap.
- `review`, with current revision/operator/storage flags, `--signed-envelope FILE`, `--archive-sha256 HASH`, `--source-sha256 HASH`, `--dependencies-sha256 HASH` and `--runtime-inventory-sha256 HASH`, verifies signed claims and records acceptance of those identities. Pins must be independently reviewed. Local wall time is used for expiry; correct host time remains an installation prerequisite.

Every successful change is an atomic SQLite transaction with a sequential audit event and consistency digest. Concurrent stale decisions, absent/corrupt/interrupted policy, changed signatures, revoked keys, expired claims and downgrade below the durable floor are refused. The same sequence cannot be rebound to another envelope. The event chain detects accidental inconsistency; it is not a cryptographic defense against the database owner rewriting history.

Release review advances the floor even though it does not activate software. This intentionally prevents an ordinary software rollback from lowering trust. A failed later installation must be retried or explicitly reviewed without resetting policy. Emergency recovery requires an independently reviewed recovery procedure and preserved policy history; deleting the database to accept an older release is not a supported recovery workflow.

These records do not certify dependency safety, native-library imports, test evidence, clock accuracy or backup/recovery. Installation activation and software rollback must still bind all evidence, schema/data compatibility, operator approval and service lifecycle. No operational data migration is performed by this module. Native Windows ACL/installed-host acceptance remains separately required.
