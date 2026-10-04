# Identity foundation

The shared-device policy is ten minutes of human inactivity and an eight-hour
absolute lifetime. Background reads never renew activity. Trusted visible-tab
pointer/keyboard events send a same-origin CSRF-protected receipt, at most once
per fifteen seconds. The server stamps receipt time, rejects client timestamps,
and cannot revive expired sessions. Active interaction in any tab shares the
session; background polling in all tabs does not. This is inactivity handling,
not protection against a compromised browser or stolen authenticated cookie.

Reauthentication atomically replaces the token hash and derived CSRF secret.
The stable session ID, creation time and absolute expiry remain unchanged so
audit/approval references are preserved. Rotation checks the presented token
under a write transaction; simultaneous attempts with the old token cannot both
succeed. Logout/disable remain authoritative. Failed reauthentication neither
renews activity nor elevates permissions. No rejected mutation is automatically
retried. On expiry, pending forms are discarded; the user signs in and explicitly
reviews/resubmits. No drafts or credentials are added to browser storage.

New hashes use `chief-v2:` plus Argon2id (64 MiB, three iterations, one lane,
16-byte salt, 32-byte hash). Legacy scrypt hashes are verified and upgraded only
after successful authentication. Parameters are checked before native hashing,
and no more than two KDF operations run concurrently per process. Known-account
lockout persists; unknown names share one bounded bucket. In the Ubuntu VM the
five-sample median was about 0.22 seconds, versus 0.44 seconds for scrypt at
128 MiB. These measurements do not certify Folio performance.

## Administrative recovery

Use a verified release from a trusted location; stop Chief services and take a
verified private recovery point first. An OS administrator can run:

```
python -m identity --recover --database /absolute/private/state.sqlite3 --username OWNER_NAME --expected-owner-id EXISTING_OWNER_ID
```

The command asks for exact target-path confirmation and a new password through
hidden terminal input. It cannot create schemas, elevate another role or unlock
a quarantined restore. All sessions belonging to the recovered Owner are revoked,
lockout is cleared and an append-only security event records the OS administrative
authority. Administrative setup/recovery does not mean Chief runs as root/admin.
Synthetic unit tests mock OS authorization; an actual administrative drill must
be reported separately. No password, hash or session token belongs in ordinary
logs or transfer artifacts.

## Rollback

No schema addition is made here. Password-format upgrade is still a data change:
old software cannot verify new Argon2id hashes. Do not roll back an operational
installation to pre-foundation software against an upgraded identity database.
Retain a compatible release or use the separately authorized, verified private
restore/reconciliation procedure. Software rollback never authorizes queue replay
or quarantine promotion. Real operational deployment/migration remains OPEN.
