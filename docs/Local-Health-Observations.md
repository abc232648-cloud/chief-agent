# Local System Health observations

The default health view observes database read responsiveness, the existing worker connectivity timestamp, and dashboard/scheduler supervisor heartbeats. A service launched through deployment.launch updates its private service-control record every five seconds. READY means the service supervisor reports readiness, not that queued jobs are progressing or Farm observations are current. STARTING/STOPPING are degraded; a freshly recorded STOPPED/REVIEW state is unavailable. A missing, stale, future, malformed or older transition-only record remains unknown.

The database probe opens the existing database in read-only mode with a short lock timeout and reads its schema. It does not create a missing database, perform schema changes, repair corruption, test writes, scan all data or validate backups. Authentication and the rest of the dashboard still depend on database availability; this is not independent outage monitoring.

The probes do not call AI providers, launch browsers, contact cameras, inspect secrets, repair services or alter desired component modes. Browser, provider, camera, runtime qualification and uninstrumented capabilities retain their actual unknown state. Dependency health can also keep a higher-level capability unknown even when a lower-level database read succeeds.

Old installed service processes do not gain heartbeats until started from the updated source using the normal launcher. This change does not install or restart any service. Service record timestamps represent local supervisor observations; private state permissions must remain enforced. The records are not cryptographically attested health or proof of task progress.

The private status file is replaced atomically. Lifecycle writes and periodic heartbeats share a lock, so a pulse cannot overwrite a newer transition using stale state. Failure to persist a pulse stops new service activity through the existing cooperative-stop mechanism. Shutdown still records STOPPED or REVIEW after the watcher finishes.
