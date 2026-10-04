# Users & access

The dashboard exposes user management according to the signed-in human's role and domain scope. The API independently enforces the same restrictions; revealing a hidden control cannot grant access.

| Signed-in role | Accounts they may create, view and disable |
| --- | --- |
| Owner with installation-wide scope | All registered roles, including restricted custom domain roles |
| Administrator | Manager and Worker accounts whose entire domain scope is within their own |
| Manager | Worker accounts whose entire domain scope is within their own |
| Worker | None |

A domain-scoped Owner does not receive installation-wide user management. An account spanning Jobs and another domain cannot be managed by a Jobs-only Manager or Administrator. Managers cannot manage peers, Administrators or Owners, change their own access, or assign domains they do not hold. Custom domain roles receive no user-management permission by default.

The last enabled installation-wide Owner must remain enabled even when domain-scoped Owner accounts also exist. A domain-scoped Owner cannot substitute for installation-wide recovery and user administration.

Creating or disabling an account requires password reauthentication within the preceding five minutes. A readable user list does not imply permission to mutate it without reauthentication. Every mutation rechecks the current session and authority. Disabling an account revokes its sessions and preserves its identity and historical records. The last enabled Owner cannot be disabled.

The initial page provides creation, listing and disabling. It does not provide role editing, re-enabling or password recovery. Password fields are cleared after a submitted operation succeeds or fails; credentials are not included in the user list or security-event payloads. Transfer initial sign-in details privately.

Domain membership is shared domain access, not a separate private workspace for each person. Human privileges do not add capabilities to an agent or model, and do not bypass Job Policy Gate, evidence requirements, approval gates or action-risk controls. Manager access to user management does not grant access to global settings, models, runtimes, audit or component administration.

No database schema migration is introduced by this page or the Manager permission. Existing identity and security-event tables remain in use.
