"""Sanitized, job-scoped presentation feed for the Job Agent PWA.

This module deliberately exposes a small allow-listed projection. It never returns
application-event details/data_json, job URLs, form contents, credentials, or raw
application payloads.
"""
from __future__ import annotations

DEFAULT_LIMIT = 50
MAX_LIMIT = 50


def _bounded_limit(limit: int | None) -> int:
    try:
        value = int(limit or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        value = DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _application_severity(event_type: str, status: str) -> str:
    text = f"{event_type} {status}".upper()
    if any(marker in text for marker in ("STOP", "BLOCK", "FAIL", "ERROR", "DENIED")):
        return "URGENT"
    if any(marker in text for marker in ("REVIEW", "APPROVAL", "ACTION_REQUIRED", "WAITING_USER")):
        return "ACTION_REQUIRED"
    return "INFO"


def recent_job_feed(store, limit: int = DEFAULT_LIMIT) -> dict:
    """Return recent job notifications and application events, newest first.

    The feed is intentionally presentation-only. Sensitive ledger payloads remain
    server-side and must be inspected through their existing authorized surfaces.
    """
    take = _bounded_limit(limit)
    from control.notifications import initialize as initialize_notifications
    from database.store_extensions import init_extensions

    # Normal initialized stores already have these schemas. These calls only retain
    # compatibility with older development DBs and perform no work on operational
    # read-only stores where migrations are forbidden.
    if not getattr(store, "operational", False):
        initialize_notifications(store)
        init_extensions(store)

    with store._connect() as con:
        notifications = [dict(row) for row in con.execute(
            """SELECT id,created_at,severity,title,body,read,related_page
               FROM notifications
               WHERE domain='jobs'
               ORDER BY id DESC LIMIT ?""",
            (take,),
        )]
        application_events = [dict(row) for row in con.execute(
            """SELECT ae.id,ae.event_time,ae.event_type,ae.status,ae.application_id,
                      j.title AS job_title,j.company AS company
               FROM application_events ae
               JOIN applications a ON a.id=ae.application_id
               LEFT JOIN jobs j ON j.id=a.job_id
               ORDER BY ae.id DESC LIMIT ?""",
            (take,),
        )]

    events = []
    for row in notifications:
        events.append({
            "id": f"notification:{row['id']}",
            "occurred_at": row["created_at"],
            "source": "notification",
            "severity": row["severity"],
            "title": row["title"],
            "summary": row["body"],
            "status": "READ" if row["read"] else "UNREAD",
            "application_id": None,
            "related_page": row.get("related_page") or "",
            "unread": not bool(row["read"]),
        })

    for row in application_events:
        label = str(row["event_type"] or "APPLICATION_EVENT").replace("_", " ").strip().title()
        job = (row.get("job_title") or "Application").strip()
        company = (row.get("company") or "").strip()
        summary = f"{job} · {company}" if company else job
        events.append({
            "id": f"application-event:{row['id']}",
            "occurred_at": row["event_time"],
            "source": "application",
            "severity": _application_severity(row["event_type"], row["status"]),
            "title": label,
            "summary": summary,
            "status": row["status"],
            "application_id": row["application_id"],
            "related_page": "applicationArchive",
            "unread": False,
        })

    events.sort(key=lambda item: (str(item["occurred_at"] or ""), item["id"]), reverse=True)
    events = events[:take]
    unread = [item for item in events if item["source"] == "notification" and item["unread"]]
    return {
        "events": events,
        "counts": {
            "unread": len(unread),
            "action_required": sum(item["severity"] == "ACTION_REQUIRED" for item in unread),
            "urgent": sum(item["severity"] == "URGENT" for item in unread),
        },
        "limit": take,
        "privacy": "Sanitized job-domain presentation feed; raw event payloads are not exposed.",
    }
