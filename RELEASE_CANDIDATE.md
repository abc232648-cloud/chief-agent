# Chief release candidate — 24 October 2026

Release ID: `chief-2026-10-24-console-review`.

The first-release candidate includes the Console changes from PR #39, domain onboarding and health repairs, and the registered inference-purpose guards and Farm connection-test token-limit correction. This selects the software scope; it does not approve production deployment.

The matching chief-installer review manifest is the canonical component pin set. Its CURRENT_RELEASE_MANIFEST and CI checkout must agree with review-dns-manifest.json. Backend, installer, all three PWAs, Node and Caddy must be recorded with the rebuilt package hashes in the new clearance record. Never mix this backend with older installer packages or substitute floating branches.

Historical deployment and closure records dated before 24 October 2026 are retained for evidence only and are superseded for this candidate. New installation instructions and the exact release-set/clearance evidence accompany the 24 October release package. A package without that evidence remains unqualified.

Release HOLD remains active until separate Folio installation, phone/browser acceptance and existing Farm-data backup/restore and reconciliation checks pass. Do not overwrite existing Farm records. The software test/packaging clearance must explicitly list these remaining gates.

The planned new desktop shell, expanded provider activation/spending controls, SOP/framework expansion and OpenClaw/Hermes qualification are not claimed delivered by this candidate. Its current Console remains the existing browser-based implementation. No existing runtime safety gate is bypassed.
