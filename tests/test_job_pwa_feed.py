import json

from database.store_extensions import add_application_event
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


def _job_worker(dashboard):
    service = IdentityService(dashboard.store)
    service.create_user(
        dashboard.credentials['principal'],
        'job-feed-worker',
        PASSWORD,
        'Worker',
        ('jobs',),
    )
    raw, _ = service.login('job-feed-worker', PASSWORD)
    return raw


def test_job_feed_is_read_only_job_scoped_and_sanitized(dashboard):
    d = dashboard
    d.store.add_job({
        'id': 'job-feed-1',
        'title': 'Junior SOC Analyst',
        'company': 'Example Security',
        'url': 'https://secret.example/apply?token=DO-NOT-LEAK',
        'status': 'NEW',
    })
    d.store.add_application('application-feed-1', 'job-feed-1', status='DRAFT')
    add_application_event(
        d.store,
        'application-feed-1',
        'FORM_REVIEW',
        status='REVIEW',
        details='PRIVATE FORM DETAIL DO NOT LEAK',
        data={'answer': 'PRIVATE APPLICATION ANSWER DO NOT LEAK'},
    )
    d.store.add_notification(
        'Approval needed',
        'Review the Junior SOC Analyst application.',
        severity='ACTION_REQUIRED',
        domain='jobs',
        related_page='applicationArchive',
    )
    d.store.add_notification(
        'Farm secret',
        'PRIVATE FARM NOTICE DO NOT LEAK',
        severity='URGENT',
        domain='farming',
    )
    d.store.add_notification(
        'System secret',
        'PRIVATE SYSTEM NOTICE DO NOT LEAK',
        severity='URGENT',
        domain='system',
    )

    raw = _job_worker(d)
    code, _, body = request(d, '/api/ui/job-feed', raw=raw)
    assert code == 200
    assert body['counts'] == {'unread': 1, 'action_required': 1, 'urgent': 0}
    assert len(body['events']) == 2

    encoded = json.dumps(body)
    assert 'Junior SOC Analyst' in encoded
    assert 'Example Security' in encoded
    assert 'Approval needed' in encoded
    assert 'PRIVATE FARM NOTICE' not in encoded
    assert 'PRIVATE SYSTEM NOTICE' not in encoded
    assert 'PRIVATE FORM DETAIL' not in encoded
    assert 'PRIVATE APPLICATION ANSWER' not in encoded
    assert 'DO-NOT-LEAK' not in encoded

    application_event = next(event for event in body['events'] if event['source'] == 'application')
    assert application_event['severity'] == 'ACTION_REQUIRED'
    assert application_event['application_id'] == 'application-feed-1'
    assert set(application_event) == {
        'id', 'occurred_at', 'source', 'severity', 'title', 'summary', 'status',
        'application_id', 'related_page', 'unread',
    }

    assert request(d, '/api/ui/job-feed', 'POST', {}, raw)[0] == 405


def test_job_feed_requires_job_read_authority(dashboard):
    d = dashboard
    service = IdentityService(d.store)
    service.create_user(
        d.credentials['principal'],
        'farm-only-feed-worker',
        PASSWORD,
        'Worker',
        ('farming',),
    )
    raw, _ = service.login('farm-only-feed-worker', PASSWORD)
    assert request(d, '/api/ui/job-feed', raw=raw)[0] == 403
