# Shared notification/event bus

Chief owns one presentation-event contract for Owner, Staff and optional companion surfaces. The bus does **not** grant authority. Authentication, domain scope, Farm assignment checks, policy gates and sensitive-operation controls remain authoritative at the API/domain layer.

## Event envelope

`notifications.events.NotificationEvent` is a versioned, immutable, bounded presentation envelope. It carries only:

- stable event id and event type
- source agent/domain
- `INFO`, `ACTION_REQUIRED` or `URGENT` severity
- bounded title/body
- explicit presentation audience
- optional structured internal deep links
- optional object type/id
- priority and timezone-aware creation time

There is deliberately no arbitrary metadata/payload field. Raw commands, application forms, credentials, provider responses, evidence payloads and domain-private records must not be copied into notification events.

Deep links are structured as `surface + registered module + relative route`. External URLs, query strings, fragments, path traversal and module mismatches fail closed.

## Audience model

Supported surfaces are `owner`, `staff` and `companion`.

Agent events are valid only for surfaces that the agent's validated manifest actually declares, and only when that manifest declares notifications. A deep-link module must exactly match the Interface Registry entry for that surface.

Staff routing uses the A5 role contract:

| Staff role | Notification routing scope |
| --- | --- |
| Manager | Domain-wide events may be addressed by role |
| Supervisor | Explicit principal id required |
| Worker | Explicit principal id required |

This prevents a `Supervisor` or `Worker` label from becoming a farm-wide broadcast authority. Role matching is presentation filtering only; the domain still decides whether that human may read or mutate the underlying object.

Target principal ids, when present, apply before role matching. Domain scope is always checked by the presentation projection.

System notifications are restricted to the Owner surface and require installation-wide (`*`) Owner/Administrator context.

## Persistence

A6 introduces no database migration.

Authoritative notification envelopes are appended as `chief_notification_event_v1` records in the existing `domain_records` table. That table is already part of Chief's accepted operational schema. New record kinds are private from legacy agent storage unless explicitly granted elsewhere.

Publishing is idempotent by immutable event id inside a serialized SQLite write transaction. An exact replay returns the existing receipt; reusing the same event id with different content fails closed.

Malformed persisted event records are never projected to a presentation surface.

## Compatibility projection

When an event includes an Owner or companion audience, the same transaction also writes the existing `notifications` row. This keeps current dashboard/Job companion consumers and the durable Job Web Push cursor contract intact while the shared event model is introduced.

Staff-only events do **not** enter the legacy global notification table. They remain in the audience-aware event store, preventing a targeted Worker/Supervisor event from being widened into the old global inbox.

The existing Job Web Push implementation remains unchanged in A6:

- only Job `ACTION_REQUIRED`/`URGENT` rows are pushed
- registration starts at the current attention cursor, so history is not replayed
- cursor advances only after provider success
- transient provider failures retain the cursor
- HTTP 404/410 removes an expired endpoint
- subscription endpoint/key material remains private

A6 tests prove that a new shared Job event can flow through the compatibility row into that existing Web Push path without changing its external contract.

## Delivery status

A6 establishes the shared event/audience/persistence primitive. It does **not** claim that Owner or Staff background Web Push is deployed or phone-validated. Existing Job companion Web Push remains the only current background push path. Future API/PWA phases may consume `NotificationBus.list_for(...)` after applying normal Chief authentication and domain authorization.
