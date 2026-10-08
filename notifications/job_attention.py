from __future__ import annotations

from .webpush import ATTENTION_SEVERITIES, deliver_job_attention


def record_job_attention(store, title: str, body: str, severity: str, *, related_page: str = 'actions') -> dict:
    """Persist a Job-scoped attention event and attempt background delivery.

    This is deliberately separate from generic system notifications so the Job
    PWA can consume an authoritative domain feed without raw-log access or
    cross-domain leakage.
    """
    severity = str(severity or '').upper()
    if severity not in ATTENTION_SEVERITIES:
        raise ValueError('Job attention notifications must require action or be urgent.')
    from control.notifications import initialize
    initialize(store)
    with store._connect() as con:
        cur = con.execute(
            'INSERT INTO notifications(title,body,severity,domain,related_page,presented) VALUES(?,?,?,?,?,0)',
            (str(title)[:240], str(body)[:4000], severity, 'jobs', str(related_page or '')[:120]),
        )
        notification_id = int(cur.lastrowid)
    delivery = deliver_job_attention(
        store,
        notification_id=notification_id,
        title=title,
        body=body,
        severity=severity,
        related_page=related_page,
    )
    return {'notification_id': notification_id, 'delivery': delivery}
