# Prevent defects at the boundary

For each change involving credentials, private data, network access, authority, browser mutation or recovery:

1. Identify the trusted caller, untrusted inputs, permitted effects, source identity and rollback boundary before coding. Preserve existing authority gates; similar names do not imply interchangeable concepts.
2. Write an abuse case and an assertion of absence: no request reached the sink, no field received a value, no second process changed live state, no old token remained valid, no secret entered a response/log/archive. A BLOCKED label alone is insufficient.
3. Add a positive compatibility case alongside the denial. Exercise real browser/OS boundaries where possible; mocks must be labelled, not counted as deployment qualification.
4. Inspect every construction path and fallback. Put shared checks at the boundary, fail closed on unavailable guards and handle redirects, concurrency, interruption and stale state deliberately.
5. Keep secrets out of process environments, diagnostics and ordinary packages. Prefer OS-backed storage; bound inputs/work; do not invent encryption.
6. Test a coherent candidate, preserve failed/aborted evidence, repair causes and rerun affected checks. Full regressions and critical browser gates must identify the exact final tree; earlier-source passes are historical.
7. Record each finding as demonstrated, fixed with scoped evidence, or still open. Never substitute a functional pass count for security acceptance. Describe residual deployment and independent-review requirements explicitly.

This is an engineering release gate, not another recurring user-confirmation flow and not a promise that software can have zero bugs. No feature or time pressure waives these checks or authorizes deployment.
