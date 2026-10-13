# Chief Update Center

The update target is Chief itself. Job, Farm and companion PWAs remain compatibility consumers, not independently installed products in this Update Center. Existing dependency discovery remains read-only.

## Available software

The publisher verifier accepts a bounded, signed Chief release envelope and verifies its repository, stable channel, sequence, commit, source manifest, archive identity, expiry and declared Windows/Linux targets. It uses Ed25519 with the context `chief-release-envelope-v1` followed by a zero byte. The signature covers the canonical JSON payload (sorted keys, compact separators, ASCII escapes, no NaN); the public key must come from separately provisioned installation policy. A key inside downloaded metadata is never trusted.

No production publisher key is included. Publisher signing and installation trust provisioning must be established separately before real releases can use this path. Synthetic test keys are generated in memory and are not release credentials. Verification errors return fixed messages rather than input contents or private paths.

The policy file has exactly `version` (1), `repository` (`abc232648-cloud/chief-agent`), `minimum_sequence` (nonnegative integer), and `keys`. Each key has exactly `key_id`, `public_key` (base64 raw 32-byte public key), timezone-aware `valid_from`/`valid_until`, and boolean `revoked`. Provision this file in private operator-controlled storage, separately from the release. Downloaded envelopes cannot add or rotate keys. The sequence floor is not a substitute for publisher key revocation.

An envelope has exactly `key_id`, `payload`, and base64 `signature`. Its payload includes `format_version` (1), `product` (`chief`), `repository`, `channel` (`stable`), positive `sequence`, immutable 40-character `commit`, `archive_sha256`, `archive_size`, existing Chief release `manifest`, `issued_at`, `expires_at`, and `targets`. Each target declares `os` (`windows` or `linux`), `architecture` (`x86_64` or `aarch64`), inclusive `python_minimum` and exclusive `python_before` as three-part versions. Declaring a target does not certify tests on that target. Duplicate fields, unsupported fields, duplicate targets, expired/revoked keys, future issue dates and validity periods over 31 days are rejected.

Artifact verification checks every ZIP member against the existing source-package manifest and the signed digest. Package bounds, path containment, case collisions and operational/private file exclusions are enforced before inert staging. Files are not extracted or executed. Existing staged slots are not overwritten.

## Operator verification and staging

`python -m application.update_verify --envelope ABSOLUTE_ENVELOPE --trust-policy ABSOLUTE_POLICY` checks publisher metadata only. Add `--archive ABSOLUTE_ARCHIVE` to verify package bytes too. Without an instance, replay protection is limited to the supplied policy floor; no durable acceptance is claimed.

Add `--config ABSOLUTE_PREPARED_INSTANCE_CONFIG` to bind the signed sequence in the existing private database. The command does not initialize or migrate a database. Concurrent retries are idempotent; a different envelope at the same sequence or an older sequence is rejected. Revocation and expiry are rechecked on retries. Interrupted database writes roll back both the receipt and the sequence head. Quarantined restores remain blocked. Database restore/administrator tampering is outside this local receipt's anti-replay guarantee: a restored installation must remain quarantined for reconciliation.

Add `--current-manifest ABSOLUTE_CURRENT_MANIFEST` for read-only planning against an operator-supplied current release and the current Chief capability catalog. This checks actual host OS, architecture and Python against signed targets, then retains dependency/regression/model/benchmark requirements. It does not infer successful tests from metadata. Operational schema migrations remain blocked even if a release declares them reversible. The current manifest is an operator input, not independent proof of the running source.

Inert staging additionally requires all of `--archive`, `--config`, `--current-manifest`, `--staging-root ABSOLUTE_EXISTING_PRIVATE_DIRECTORY`, and `--confirm-private-storage`. A blocked preflight prevents staging. A successful result is `STAGED_NOT_INSTALLED`; acceptance, backup and human activation authority are still absent. Failure after recording a valid publisher receipt can leave that receipt but never an installed release; retry the same exact signed envelope. Incomplete slots have no activation authority.

The Chief Updates page displays recorded verification separately from operator-reported update activity. Technical fingerprints are under expandable details. A recorded signature check does not imply current key validity, compatibility or installation.

## Boundaries still to complete

This verification/staging path does not download releases, build an isolated runtime, run candidate acceptance, establish verified operational backup/restore, approve installation, switch service versions, restart Chief or perform post-update rollback. Those remain separate lifecycle gates; do not use a verification receipt as permission to bypass them. There are no separate PWA/domain update controls and no unattended self-modification.

Existing Job/Farm controls, identities, policy checks, audit records and workflows remain authoritative. No schema addition is needed for verification receipts, which use the existing `control_state` table. Ordinary software rollback returns to the previous source; operational data restore remains a separate, quarantined procedure.
