# Update activity records

The Updates page shows the latest 20 local operator records. Records persist in the existing database, including across service restarts. Earlier entries remain available through the local history reader. No migration or separate database is required.

These are observations supplied by the installation operator, not independently verified live deployment status. The page explicitly distinguishes a recorded outcome from compatibility approval or installation authority. A fingerprint identifies a referenced artifact; it does not authenticate its publisher or prove the evidence passed. No automatic installation, rollback, download or service action is triggered.

The local operator can use `python -m application.update_history --config /absolute/instance.json --event-id UUID_WITHOUT_HYPHENS --outcome DEPLOYED --source-sha256 SOURCE_DIGEST --evidence-sha256 EVIDENCE_FILE_DIGEST`. Allowed outcomes are STAGED, DEPLOYED, ROLLED_BACK and FAILED. Choose a new event ID for a new observation; an identical retry is idempotent. A conflicting rewrite is rejected. The command uses only an existing prepared instance and never initializes or migrates its schema. It does not import operational data.

Compute the source digest using the referenced release's documented manifest method and the evidence digest from the exact evidence file bytes. Retain those artifacts privately when appropriate. Never insert credentials, personal information or unrestricted free text. The history accepts only a fixed outcome, identity and digests, and records its own timestamp. It does not backdate events to claim a historical observation was made earlier.

There is no remote history-writing endpoint and no delete/edit UI. This protects against accidental modification through the application, not a malicious database administrator; these records are not a cryptographically tamper-proof audit log. Malformed entries are reported as unreadable rather than treated as successful updates.

Universal dependency installation, activation orchestration, signed release trust and independent compatibility checks remain separate work.
