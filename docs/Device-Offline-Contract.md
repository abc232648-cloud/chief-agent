# Future device and offline boundary — contract only

This document adds no device registration endpoint, credential, Farm behavior, synchronization worker or authority. A future implementation must be qualified separately.

Each enrolled device has an installation-scoped opaque identity, explicit owning domain, credential reference and revocation generation. Human sessions, agent capabilities and device identity remain separate. A device never assigns itself a role or capability. Re-enrollment does not revive old queued authority.

Receipts carry a unique event ID scoped to device and enrollment generation, monotonic sequence when available, source time, Chief receipt time, and extensible clock-quality/drift metadata. UTC timestamps are timezone-aware. Missing/uncertain source time is recorded as such, not invented. Receipt time does not prove when an observation occurred. Future/stale timestamps and uncertain clock quality reduce eligibility; they never grant action authority.

An identical event ID and identical canonical content is a duplicate receipt, not a second effect. The same ID with different content is a conflict requiring review. Contradictions remain separate evidence records with references, never last-writer-wins truth. CandidateFact lifecycle remains Job-owned. Domain-private evidence is not shared simply because devices use the same mechanics.

Offline observations may be quarantined until identity, revocation, ownership, replay and time checks pass. Offline approval is not permission to replay a high-impact action when connectivity returns. Recheck current policy, capability, evidence, human approval and target/payload at execution. Expired/revoked authority remains unusable. Ambiguous external outcomes require reconciliation; never automatically retry them. No exactly-once external-action guarantee.

Future synchronization requires bounded queues, explicit quotas, encrypted private storage, tested conflict handling and an approved operational recovery plan. These remain unimplemented and unqualified in this stage.
