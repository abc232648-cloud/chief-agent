# Managed installation preparation

This foundation prepares source and an offline Python environment in a new private folder. It does **not** install a service, start Chief, migrate a database, change the active release or enable an integration. It is intended for an ordinary local user. Windows/Linux installed-host acceptance, maintained-runtime review and guarded activation remain separate work.

## Source preparation

Use `python -m application.prepare_installation --help` from a reviewed existing tool checkout. Supply the source ZIP, independently reviewed archive and source hashes, an existing private installation root, and `--confirm-private-storage`.

Two explicit package formats are supported: the original `chief-agent/` format with the sorted path/NUL/digest identity, and `source/` with the explicitly named compact sorted JSON file-map identity. They are not interchangeable hashes. The complete file map, archive bounds, private-state exclusions and portable paths are checked before writing. A generated slot contains `source/`, its manifest and a final preparation receipt. Source is never executed. An incomplete slot is never reused.

On Linux the root must belong to the current account with no group/other permissions. On Windows an operator-controlled private ACL is a prerequisite; a Python file mode or checkbox is not proof of the installed ACL. Ancestor directory handles reject junctions/reparse points and protect against directory replacement during writes. Normal write failures remove only this attempt's tracked files. A process crash can leave an inert incomplete folder. Administrators or compromise of the same operating-system account are outside this protection boundary.

## Offline environment preparation

Use `python -m application.bootstrap_installation --help`. This requires a reviewed runtime profile, source/archive pins, a separately pinned offline wheel bundle, a private destination and explicit confirmation that the runtime and dependencies were reviewed. The caller must use a normal user account, not root/administrator.

The source must contain the applicable `release/windows-hashed.txt` and inventory or `release/ubuntu-hashed.txt` and inventory. Exact Python, implementation, platform, architecture and SQLite versions must match. The runtime profile also binds the interpreter executable hash and bootstrap pip version; it does not certify every runtime library or maintenance status. Windows' historical inventory includes bootstrap pip separately from target dependencies. Package-name normalization is explicit and duplicate normalized names fail.

The wheel bundle is a ZIP of wheel files only, one per exact lock entry. No source distributions, URL requirements, loose version ranges, network resolution or dependency substitution are allowed. Every wheel hash and distribution name/version must match the lock. Traversal, links, duplicate names, oversized payloads and interpreter startup hooks are rejected. Packages needing startup hooks require a future explicit policy; they are not silently admitted.

The bootstrap creates a new venv without system packages, installs only these reviewed wheels offline, runs pip's dependency check and verifies the exact installed distribution inventory. It does not install browsers, download runtimes or run application source. Provider keys, SMTP credentials, PYTHONPATH and pip-index variables are not inherited. Only required OS/runtime variables (including an existing LD_LIBRARY_PATH on Linux) are carried through; the reviewed host owns that runtime configuration.

`ENVIRONMENT_PREPARED_NOT_ACTIVATED` means environment preparation succeeded. Application regression, target permissions, backup/restore, schema compatibility, HTTPS/PWA, health and explicit activation/rollback approval are still required. Failures and interruptions preserve an inert attempt and sanitized result, without retry or activation. This is deliberate: do not recursively remove an unknown partially generated environment as a routine recovery step.

## Release signatures and trust

The separate `update_center.trust.verify_signed_release` contract verifies an Ed25519 detached envelope against independently provisioned public-key fingerprints. It binds the source, archive, dependency bundle and runtime-inventory hashes, release identity, sequence and validity window. Unknown/revoked keys, duplicates, substitutions, expired/future claims and sequences below the supplied floor fail closed. An embedded public key never becomes trusted automatically.

Local public-key enrollment, permanent revocation, rotation and durable sequence-floor storage are implemented in installation/trust_policy.py; see Durable-Release-Trust.md. Production key custody, revocation distribution, emergency recovery and a real release-signing process remain OPEN. No production signing key is generated or exported. The signature API currently returns a verification result; it is not wired to automatic bootstrap or activation. A local operator's dependency-review confirmation remains explicit. Signature verification is not evidence that a release is safe or compatible.

The verifier uses the library's Ed25519 interface described in the [official cryptography documentation](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/). The optional installer dependency is declared in `requirements-installer.txt`; both recorded OS lock files already pin cryptography 50.0.1. No package upgrade is implied.

## Rollback and open gates

Preparation does not alter active software or operational data, so the running release needs no rollback. Discarding an inactive attempt must be an explicit, path-verified local operation. Once an activation mechanism exists, it must preserve the old software and newer data separately; restoring an old database is not a software rollback.

No web UI, scheduled installation, service creation, public binding, real schema migration, real backup/restore promotion, provider activation, queued Job replay or external workflow execution is introduced here.

## Automatic Python dependency profiles

See [Automatic-Dependencies.md](Automatic-Dependencies.md) for the new core, optional Scrapy and full-test preparation CLI and its remaining gates. This does not activate Chief or install all external runtimes.
