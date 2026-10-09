# A9 — Dynamic module/API exposure

Chief derives exposure on each request from the installed ManifestRegistry, A7
ModuleLifecycle, A8 ModuleCompatibilityEvaluator, ComponentControls, and the
current authenticated identity. Exposure neither changes those authorities nor
grants execution permission.

`ModuleExposure` rejects missing manifests, replaced installation metadata, runtime
or interface registration drift, unknown/incompatible compatibility, disabled or
maintenance component modes, disabled legacy domains, disabled dependencies, and
unauthorized or revoked sessions. Installed metadata replacement requires server
recomposition; old interface registrations cannot acquire the replacement's
surface. Health degradation alone does not revoke an otherwise enabled module:
A7 health remains observational and existing operation guards stay authoritative.

`GET /api/ui/context` now includes only currently accessible domains/pages and an
`interfaces` list containing authorized Owner, Staff, and Companion declarations.
`GET /api/domains` also filters actionable domain descriptions. Static JavaScript,
HTML shells, and manifest declarations are not authority and may remain cached.
Every direct module API request passes the same current exposure gate before its
existing permission, assignment, resource, confirmation, and execution checks.

Server-owned route bindings cover Farm APIs, domain APIs, legacy Job APIs, Job
Companion feed, and domain UI views. Existing scoped legacy Job access remains
supported; it does not create an undeclared Staff interface. Interface audiences
filter presentation only; Chief identity authorization remains required. Farm's
operation-specific role and assignment checks remain unchanged.

Control and identity management remain independently authorized and reachable for
recovery. Global audit/administration observations are not actionable module
surfaces. Client bodies, query parameters, browser caches, and claimed roles,
versions, compatibility, or enabled flags cannot register or enable a surface.
There is no client endpoint to mutate exposure and no persisted exposure grant.

Focused regression tests cover discovery, direct requests, disable/re-enable,
maintenance, compatibility changes, installation drift, missing modules, revoked
sessions, unauthorized interfaces, and forged browser inputs. Module Exposure CI
runs those checks plus the complete suite including Chromium acceptance tests.
A10 cross-PWA contract/security work is not part of this phase.
