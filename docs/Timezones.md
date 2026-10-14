# Timezones

Chief has no country-specific default for a new installation. The host timezone
is detected through the operating system using `tzlocal`, with IANA `ZoneInfo`
rules for historical offsets and daylight-saving changes. Missing or conflicting
host timezone settings need operator correction; they are not replaced by Lagos.

The Job scheduler follows the host timezone unless `JOB_WORKER_TIMEZONE` explicitly
selects another IANA zone. General Chief displays and Farm date/time entry use
the current browser's local timezone. Recorded instants remain timezone-aware UTC.
No GPS or location permission is requested.

The Farm reporting zone is pinned during the first successful authorized Farm
setup/write transaction, in existing domain storage. Reading a page does not
write settings. Once pinned, moving the host or using a travelling device does
not regroup daily totals, change deadlines or reinterpret opening dates.
Existing records from older releases retain their original Lagos reporting
meaning; this compatibility rule applies only to pre-existing Farm history.
There is no schema migration, automatic historical rewrite or public override.

Farm reporting dates use the saved Farm zone; timestamp entry uses device-local
time. Nonexistent local times during a clock change are rejected. A repeated
local time requires the person to choose which UTC occurrence they mean. A daily
reporting deadline that is itself ambiguous or nonexistent is marked for clock
review, never used to invent a missing/late report.

The optional encrypted offline form uses the same local-time conversion before
queueing a UTC timestamp. Its public shell cache version includes the time helper;
private reports are not added to that cache.

Dependency: [tzlocal maintainer documentation](https://pypi.org/project/tzlocal/).
Actual Windows/Linux host detection and synthetic London/New York/DST cases are
covered by `tests/test_installation_timezones.py`. This does not certify the
correctness of a real deployment's OS clock or timezone configuration.
