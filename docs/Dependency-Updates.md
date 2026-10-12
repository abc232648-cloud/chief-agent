# Dependency update discovery

Chief performs read-only dependency release discovery on a separate maintenance cadence. The default is once every 24 hours and the operator may change it with `DEPENDENCY_UPDATE_CHECK_HOURS`. The accepted range is 1 through 720 hours. This setting does not change `SCHEDULER_POLL_SECONDS`; the scheduler may wake frequently while release discovery remains due only at its own cadence.

The discovery surface is intentionally bounded to dependencies Chief can identify without sending private application data to a vendor account:

- the pinned external runtime candidates in `config/runtime_candidates.json`, using allow-listed official public release metadata;
- Python packages declared by the maintained production/optional-worker requirement files when their installed version is observable in the current environment.

A dependency check can record `CURRENT`, `UPDATE_AVAILABLE`, `LOCAL_NEWER`, `UNORDERED`, `NOT_INSTALLED_HERE`, or an error state. Unknown/pre-release/unorderable version text is never treated as proof that a newer stable version exists. Source failure is never reported as current.

The state file is private Chief state at `maintenance/dependency-updates.json`. It contains sanitized version observations and deduplication keys only. It does not store response bodies, credentials, cookies, authorization headers, or private Chief data. A malformed or linked state file fails closed rather than being overwritten.

A newly observed update set produces one generic local notification. Repeated discovery of the same current-to-latest pair does not notify again. A later different version is a new observation and may notify once.

## No automatic update authority

Discovery has no install or activation authority. It must not download packages, execute a package manager, modify pinned versions, replace runtime binaries, restart services, migrate data, or activate a candidate. An available update still goes through Chief's existing update planning, compatibility/regression, backup, runtime qualification, and operator-review gates before any future installation path can be authorized.

Network/metadata failures do not stop normal scheduler duties. They are recorded as review-required maintenance evidence and no dependency is changed.

## Search providers are separate

Search/research provider selection is not dependency-update discovery. Chief's research interface remains provider-neutral. DDGS is the initial enabled search route; Brave, SearXNG, and Tavily are reserved future candidates and are not automatically enabled or used as fallback providers.
