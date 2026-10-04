# Private model setup

The Models page can register a provider/model identifier and an optional API key. Global Owner/Administrator access and recent server-verified reauthentication are required. The form confirms the Chief password before saving. Existing Host, Origin and CSRF checks remain mandatory; credential mutations additionally require loopback or TLS. Public deployment is not introduced.

Every imported registration is DISABLED, unassigned and outside the installation allowlist. Existing Qwen/Groq-primary → Mistral-fallback behavior is unchanged. Mistral/free-only remains OPEN / LEGACY_UNRESOLVED. Pricing entered here is an unverified declaration, never authority to spend. Custom providers may be registered for review; they do not gain a transport adapter. Model weights and agent runtimes are not installed. Activation/assignment of imported models remains a separate reviewed change.

## Keys and data boundaries

Keys are encrypted using Windows CurrentUser DPAPI or Linux systemd user-scoped credentials with null-key encryption refused. If the platform backend is unavailable, saving fails closed. The current ordinary operating-system service account owns the material; moving accounts/machines requires separate protected reprovisioning. No encryption key is added to the repository. An administrator or compromise of that account can still access secrets: this is not protection against a compromised operating system.

Private files are under the explicit instance state directory's `model-credentials/`, outside application source. Entries are staged privately and published atomically. Metadata contains only generated identity, provider/model, declared cost, DISABLED state, actor/time and key-presence status; it contains no key, suffix, fingerprint or credential value. API responses and security events never include the key. Provider responses/errors are not logged or returned verbatim. The UI uses password fields, clears them after success/failure, and does not save them in browser storage. Python/browser memory zeroization is not guaranteed.

Operational backup asset validation refuses the credential directory, including its metadata. Source/evidence packagers must call `operations.source_archive.check_archive_names`; the development packager does. Neither encrypted blobs nor plaintext keys belong in ordinary source/evidence archives. There is no credential export/reveal API. Credentials must be reprovisioned separately after restore; no restore gains model authority.

## Optional connection check

Only an explicitly confirmed check contacts the saved provider, using fixed Groq/Mistral HTTPS model-list endpoints, certificate verification, no environment proxy, no redirects, bounded responses and a timeout. Preview refuses external checks. No generation prompt, application data or external action is sent. Provider terms/charges may apply; the UI asks for confirmation. A successful response is not pricing, model-quality, safety or provider qualification. The registration remains DISABLED. Development tests mock provider traffic; no live provider qualification is claimed.

## Rollback and limitations

No database migration is added. Revert the model setup feature files and UI/API additions to remove this flow; existing domain/model state is untouched. Retain private keys separately or revoke them through the provider and remove the corresponding private registration under an operator-controlled maintenance procedure. In-app key rotation/revocation and activation are not implemented in this addition. No key is ever silently attached to the existing Job provider configuration.
