# Database

v13 adds a local SQLite state store (`worker.db`). SQLite is built into Python, requires no server, and keeps the worker local/offline by default.

The store tracks jobs, applications, pending actions, notifications, sources, dashboard commands, reports, and worker status.
