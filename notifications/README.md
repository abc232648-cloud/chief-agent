# Notifications

The notification system has two delivery paths:

1. **Local inbox (default, ₦0):** writes human-readable TXT notifications and daily/weekly/monthly summaries to `notifications/outbox/`.
2. **SMTP email (optional, ₦0 when using an existing email account/provider):** sends the same structured content by email. Credentials are environment/configuration secrets and must never be exposed to job sites.

The system distinguishes **action-required notifications** from informational summaries. Examples include:
- registering for a newly discovered legitimate job platform;
- completing an account/profile step needed before applying;
- manually inspecting an HTTP-only job/source;
- approving a high-impact application decision;
- resolving a blocked or unresolved item.

Reports are generated separately for daily, weekly and monthly periods. The schedule module provides runner commands; the host scheduler (cron/systemd) can invoke them later.
