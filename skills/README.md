# Skills

Skills are reusable procedures executed by the AI/workers. They are not separate AI models.

Current skills:
- `job_discovery`: accepts and validates raw listings.
- `job_normalization`: converts listings to a stable schema.
- `scam_screening`: conservative scam classification; `NO_OBVIOUS_SCAM` does not mean legitimate.
- `candidate_matching`: ranks opportunities against preferences without inventing candidate facts.
- `evidence_check`: separates valid evidence labels from evidence that may actually be claimed.
- `job_pipeline`: runs discovery → normalization → scam screening → matching.
