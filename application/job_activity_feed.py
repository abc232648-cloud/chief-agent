"""Sanitized presentation feed for the Job companion PWA.

This projection deliberately omits raw command instructions/results, application
payload JSON, form contents, credentials, job URLs, and cross-domain records.
"""
from __future__ import annotations

DEFAULT_LIMIT = 50
MAX_LIMIT = 50


def _bounded_limit(limit):
    try:
        value = int(limit or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        value = DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _severity(*parts):
    text = ' '.join(str(part or '') for part in parts).upper()
    if any(marker in text for marker in ('STOP', 'BLOCK', 'FAIL', 'ERROR', 'DENIED')):
        return 'URGENT'
    if any(marker in text for marker in ('REVIEW', 'APPROVAL', 'ACTION_REQUIRED', 'WAITING_USER')):
        return 'ACTION_REQUIRED'
    return 'INFO'


def recent_job_activity(store, limit=DEFAULT_LIMIT):
    """Return newest safe Job activity without leaking execution payloads."""
    take = _bounded_limit(limit)
    from control.notifications import initialize as initialize_notifications
    from database.store_extensions import init_extensions

    if not getattr(store, 'operational', False):
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
        has_domain_requests = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='domain_requests'"
        ).fetchone() is not None
        if has_domain_requests:
            command_sql = """SELECT c.id,c.status,c.created_at,c.processed_at
                             FROM commands c
                             LEFT JOIN domain_requests d ON d.command_id=c.id
                             WHERE COALESCE(d.domain,'jobs')='jobs'
                             ORDER BY c.id DESC LIMIT ?"""
        else:
            command_sql = """SELECT c.id,c.status,c.created_at,c.processed_at
                             FROM commands c ORDER BY c.id DESC LIMIT ?"""
        commands = [dict(row) for row in con.execute(command_sql, (take,))]

    events = []
    for row in notifications:
        events.append({
            'id': f"notification:{row['id']}",
            'occurred_at': row['created_at'],
            'source': 'notification',
            'severity': str(row['severity'] or 'INFO').upper(),
            'title': row['title'],
            'summary': row['body'],
            'status': 'READ' if row['read'] else 'UNREAD',
            'related_page': row.get('related_page') or '',
            'unread': not bool(row['read']),
        })

    for row in application_events:
        event_type = str(row['event_type'] or 'APPLICATION_EVENT')
        status = str(row['status'] or 'RECORDED')
        label = event_type.replace('_', ' ').strip().title()
        job = str(row.get('job_title') or 'Application').strip()
        company = str(row.get('company') or '').strip()
        events.append({
            'id': f"application-event:{row['id']}",
            'occurred_at': row['event_time'],
            'source': 'application',
            'severity': _severity(event_type, status),
            'title': label,
            'summary': f'{job} · {company}' if company else job,
            'status': status,
            'related_page': 'applicationArchive',
            'unread': False,
        })

    for row in commands:
        status = str(row['status'] or 'UNKNOWN').upper()
        events.append({
            'id': f"command:{row['id']}",
            'occurred_at': row.get('processed_at') or row['created_at'],
            'source': 'command',
            'severity': _severity(status),
            'title': f'Job Agent command {status.replace("_", " ").lower()}',
            'summary': f"Command #{row['id']} status: {status}.",
            'status': status,
            'related_page': 'jobOverview',
            'unread': False,
        })

    events.sort(key=lambda item: (str(item['occurred_at'] or ''), item['id']), reverse=True)
    events = events[:take]
    unread = [item for item in events if item['source'] == 'notification' and item['unread']]
    return {
        'events': events,
        'counts': {
            'unread': len(unread),
            'action_required': sum(item['severity'] == 'ACTION_REQUIRED' for item in unread),
            'urgent': sum(item['severity'] == 'URGENT' for item in unread),
        },
        'limit': take,
        'privacy': 'Sanitized Job presentation feed; raw command/application payloads are not exposed.',
    }
