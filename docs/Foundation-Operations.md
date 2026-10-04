# Local operational foundation

Real operational backup/restore, migration and deployment remain separately gated.
All implementation demonstrations use disposable data. Restores remain quarantined.

## Explicit instance boundaries

`CHIEF_INSTANCE_MODE` is `preview`, `test` or `production`. Without a mode the
dashboard selects a separate preview directory; it refuses a legacy database
override. Preview cannot start a worker/scheduler, send test email or perform
external application retries. Prepare a new preview explicitly with
`python -m deployment prepare-preview --directory ABSOLUTE_NEW_DIRECTORY
--source-root VERIFIED_SOURCE --source-manifest VERIFIED_MANIFEST --username NAME`.
The command prompts privately for the password and runs each accepted migration
only after its own B.5 backup/quarantined-restore prerequisite. It never promotes
those restores. Set `CHIEF_PREVIEW_ROOT` to that directory to use the preview.

Test mode requires an explicit isolated development marker, root and database.
Production requires `CHIEF_INSTANCE_CONFIG`, a JSON file containing mode
`PRODUCTION`, absolute `state_root` and `database`, and the reviewed
`schema_sha256`. The state root must have `.chief-production.json` containing
`{"mode":"PRODUCTION"}`. These are preparation records, not authority to migrate.
Run `python -m deployment preflight --config FILE` before service activation.
Startup refuses missing/incompatible schemas; operational connections refuse DDL.
Do not mark a real database or install a service without its operational plan.

## Secret references and recovery

`GROQ_API_KEY_REF`, `MISTRAL_API_KEY_REF` and `SMTP_PASSWORD_REF` identify entries
in the private JSON file `CHIEF_SECRET_REFERENCES`. Each entry declares `backend`,
`key`, `domain`, exact `consumers`, nonempty `version`, boolean `revoked` and nullable
timezone-aware `expires_at`. Values are never stored in that registry. Job model
keys belong to domain `jobs`, consumer `jobs.gateway`; SMTP belongs to domain
`system`, consumer `system.email`. Metadata never grants model/capability authority.

For Ubuntu service delivery, use systemd `LoadCredential=` or
`LoadCredentialEncrypted=` and read the private file exposed under
`CREDENTIALS_DIRECTORY`. Credential keys contain no path separators. The reader
rejects links, public mode bits, unexpected ownership, expired/revoked references
and missing values. Test-delivered files demonstrate reader semantics; they do
not certify actual service credential provisioning, TPM binding or key recovery.

For Windows, `private_secrets.windows.provision` uses CurrentUser DPAPI and a
dedicated directory whose protected ACL permits the current account, SYSTEM and
Administrators. `CHIEF_SECRET_ROOT` points to that directory. Provision under the
same normal account that will run Chief; do not copy DPAPI blobs to a different
account and assume they will decrypt. Windows recovery/account backup must be
planned separately. No custom encryption key is created by Chief.

Consumers resolve references when constructing a provider or a new email
delivery. Rotation changes delivered credential material/reference version; a
long-lived model provider requires a controlled worker restart. Revocation blocks
new resolutions; it cannot recall credentials already loaded in process memory.
Stop/restart consumers and revoke the credential at its provider for immediate
containment. Missing/revoked material fails closed; no paid fallback is added.

Production refuses plaintext provider credentials and plaintext SMTP settings.
Existing legacy development settings remain explicit compatibility behavior.
There is no automatic credential migration, deletion or export. Before moving a
real legacy secret, inventory its readers, provision and test a replacement under
the correct account, arrange private backup/recovery and revoke the old value.
Keep `.env`, email settings, browser sessions, identity DB backups, OS keys and
delivered credential directories out of source/review archives. Back up recovery
material separately with appropriate access control. Loss of an OS/TPM/account
key may require provider-side credential rotation rather than blob restoration.

## Existing data and software rollback

SQLite remains WAL-based with foreign keys and a ten-second busy timeout. Use the
online backup API and integrity/quarantine verification; never copy only a live
main DB file. Documents, provenance, browser state and external paths need their
own inventory. Operational RTO/RPO and off-machine destination remain open until
the operator decides and the recovery drill measures them.

These changes add no database schema. Password upgrades are data changes and old
software cannot authenticate new hashes. A code rollback is not a data restore
or permission to replay queued/ambiguous external actions. Preserve accepted I
as the immutable software reference; retain a compatible release for upgraded
identity data and use only separately authorized operational recovery.
