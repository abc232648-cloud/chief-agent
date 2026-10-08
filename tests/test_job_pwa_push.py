from identity.service import IdentityService
from notifications import web_push
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


def _jobs_worker(dashboard, username='job-push-worker'):
    service = IdentityService(dashboard.store)
    service.create_user(
        dashboard.credentials['principal'], username, PASSWORD, 'Worker', ('jobs',)
    )
    raw, _ = service.login(username, PASSWORD)
    return raw


def _subscription(endpoint='https://push.example.test/subscription/123'):
    return {
        'endpoint': endpoint,
        'keys': {
            'p256dh': 'A' * 88,
            'auth': 'B' * 24,
        },
    }


def test_push_config_and_subscription_are_job_scoped(dashboard, monkeypatch):
    monkeypatch.delenv('CHIEF_WEB_PUSH_VAPID_PUBLIC_KEY', raising=False)
    monkeypatch.delenv('CHIEF_WEB_PUSH_VAPID_PRIVATE_KEY', raising=False)
    monkeypatch.delenv('CHIEF_WEB_PUSH_VAPID_SUBJECT', raising=False)
    raw = _jobs_worker(dashboard)

    status, _, config = request(dashboard, '/api/job-pwa/push-config', raw=raw)
    assert status == 200
    assert config == {'enabled': False, 'public_key': '', 'storage_ready': True, 'subscribed': 0}

    assert request(dashboard, '/api/job-pwa/push-subscriptions', 'POST', _subscription(), raw)[0] == 201
    assert request(dashboard, '/api/job-pwa/push-config', raw=raw)[2]['subscribed'] == 1

    endpoint = _subscription()['endpoint']
    assert request(dashboard, '/api/job-pwa/push-unsubscribe', 'POST', {'endpoint': endpoint}, raw)[0] == 200
    assert request(dashboard, '/api/job-pwa/push-config', raw=raw)[2]['subscribed'] == 0


def test_push_subscription_requires_csrf_origin_and_job_scope(dashboard):
    raw = _jobs_worker(dashboard, 'job-push-security-worker')
    payload = _subscription('https://push.example.test/subscription/security')
    assert request(dashboard, '/api/job-pwa/push-subscriptions', 'POST', payload, raw, csrf=False)[0] == 403
    assert request(dashboard, '/api/job-pwa/push-subscriptions', 'POST', payload, raw, origin=False)[0] == 403

    service = IdentityService(dashboard.store)
    service.create_user(
        dashboard.credentials['principal'], 'farm-only-push-worker', PASSWORD, 'Worker', ('farming',)
    )
    farm_raw, _ = service.login('farm-only-push-worker', PASSWORD)
    assert request(dashboard, '/api/job-pwa/push-config', raw=farm_raw)[0] == 403
    assert request(dashboard, '/api/job-pwa/push-subscriptions', 'POST', payload, farm_raw)[0] == 403


def test_subscription_validation_rejects_unsafe_or_extra_fields(dashboard):
    raw = _jobs_worker(dashboard, 'job-push-validation-worker')
    bad = _subscription('http://push.example.test/not-https')
    assert request(dashboard, '/api/job-pwa/push-subscriptions', 'POST', bad, raw)[0] == 400

    bad = _subscription('https://push.example.test/subscription/extra')
    bad['unexpected'] = 'field'
    assert request(dashboard, '/api/job-pwa/push-subscriptions', 'POST', bad, raw)[0] == 400


def test_notification_hook_pushes_only_job_ask_and_stop(dashboard, monkeypatch):
    sent = []

    def fake_send(store, title, body, severity, related_page=''):
        sent.append((title, body, severity, related_page))
        return {'status': 'SENT', 'delivered': 1, 'failed': 0}

    monkeypatch.setattr(web_push, 'send_job_push', fake_send)

    dashboard.store.add_notification('Silent info', 'No push.', 'INFO', domain='jobs')
    dashboard.store.add_notification('Farm urgent', 'Wrong domain.', 'URGENT', domain='farming')
    dashboard.store.add_notification('Approval required', 'Review this job.', 'ACTION_REQUIRED', domain='jobs', related_page='applicationArchive')
    dashboard.store.add_notification('Stop', 'Job Agent halted.', 'URGENT', domain='jobs')

    assert sent == [
        ('Approval required', 'Review this job.', 'ACTION_REQUIRED', 'applicationArchive'),
        ('Stop', 'Job Agent halted.', 'URGENT', ''),
    ]


def test_web_push_is_fail_closed_without_vapid_configuration(dashboard, monkeypatch):
    monkeypatch.delenv('CHIEF_WEB_PUSH_VAPID_PUBLIC_KEY', raising=False)
    monkeypatch.delenv('CHIEF_WEB_PUSH_VAPID_PRIVATE_KEY', raising=False)
    monkeypatch.delenv('CHIEF_WEB_PUSH_VAPID_SUBJECT', raising=False)
    assert web_push.send_job_push(dashboard.store, 'Approval', 'Review', 'ACTION_REQUIRED') == {
        'status': 'SKIPPED', 'reason': 'not_configured'
    }
