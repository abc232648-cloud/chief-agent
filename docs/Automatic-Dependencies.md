# Automatic Python dependency preparation

Status: implemented CLI foundation, focused-tested; not a complete universal installer or deployment acceptance.

`python -m application.install_dependencies --help` downloads source-pinned binary wheels from PyPI with hash checking, validates wheel contents, and invokes the existing isolated offline bootstrap. It never modifies an existing environment or activates services.

Required inputs are `--source-archive`, `--archive-sha256`, `--source-sha256`, `--runtime-profile`, `--installation-root`, `--confirm-private-storage`, and `--confirm-runtime-and-dependencies-reviewed`. Use an independently reviewed source identity and host runtime profile. The destination must already be private and the command must run as an ordinary user. No model keys are required.

Selection:
- No component flag: core Chief Python dependencies.
- `--component scrapy`: core plus a separate Scrapy environment; the result provides `CHIEF_SCRAPY_PYTHON` for future guarded service configuration. It does not edit the running service configuration.
- `--component test`: a single full-test environment including the optional collector. It cannot be mixed with production selections.

Profiles validate their recursive requirements before downloading. Missing optional locks or incompatible pins fail visibly instead of silently installing only core. Core uses the historical OS locks; Scrapy/test use explicit extended locks. Extended locks derive from the successful Windows Scrapy installation with exact-version PyPI wheel hashes. Linux extended-profile acceptance remains separate. The supplemental dependency selection does not upgrade existing core pins.

A successful result is `SELECTED_PYTHON_DEPENDENCIES_PREPARED_NOT_ACTIVATED`. Failure/interruption is recorded per component; later selections remain NOT_ATTEMPTED. Attempts are inert and not silently reused. Exact wheel hashes do not establish vulnerability-free or publisher-authenticated software. Existing wheel startup-hook rejection is retained.

Still required: production signing policy integration; browser/Node/npm/n8n/Docker/runtime artifact acquisition and qualification; installed-host runtime/ACL checks; startup/health/import probes; Update Center presentation; service activation/rollback; integration tests with the newer GitHub main. This command requires an already qualified Python/SQLite runtime and does not download Python or fix an incompatible host. Windows Codex SQLite 3.53.1 is NOT identical to the historical lock's qualified 3.53.4 runtime. Do not weaken that check just to make preparation pass.
