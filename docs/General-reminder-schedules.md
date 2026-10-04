# General reminder schedules

The Chief Tasks page supports one-time and recurring in-app reminders for registered domains. Installation-wide Owners and Administrators can create and pause/resume them. This first action does not send email/SMS, invoke an AI model, run an n8n workflow, or operate equipment or cameras.

Choose the domain, reminder text, first occurrence in the device's local time, and recurrence in elapsed minutes (zero means once; otherwise at least five minutes). Review the confirmation before saving. The time is stored as an absolute instant alongside the timezone. Recurrence is an elapsed interval, not a same-wall-clock daily recurrence across daylight-saving changes.

A running scheduler is required for delivery. A stopped or disabled domain does not receive reminders. After downtime or a pause, at most one overdue reminder is delivered and the next occurrence advances beyond the current time. Delivery and progress are committed together; failed writes roll back and concurrent delivery cannot create duplicate occurrences.

The creator must remain an enabled, globally scoped Owner or Administrator. Lost authority closes the schedule. Completed, unsupported and authority-revoked schedules cannot be reopened, including through repeated pause requests. There is a 500-schedule limit including closed schedules; archive/removal and editing existing dates are not implemented yet.

These schedules reuse existing control-state storage and notification tables. They do not authorize a production database migration. Operational stores must already satisfy the existing schema preflight. All development tests use disposable synthetic state.
