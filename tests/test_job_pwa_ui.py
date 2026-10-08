from types import SimpleNamespace

import pytest

from application.ui_api import dispatch
from database.store import Store


class Handler:
    def __init__(self):
        self.payload = None
        self.status = 200

    def json(self, payload, status=200):
        self.payload = payload
        self.status = status


class Service:
    def __init__(self):
        self.calls = []

    def authorize(self, principal, permission, domain=None, resource=None, sensitive=False):
        self.calls.append((principal.role, permission, domain, sensitive))
        if domain == 'jobs' and 'jobs' not in principal.domains and '*' not in principal.domains:
            raise PermissionError('jobs scope required')
        return SimpleNamespace()


def principal(role, domains=('jobs',)):
    return SimpleNamespace(role=role, domains=list(domains))


def test_job_feed_rejects_staff_roles_even_with_jobs_scope(tmp_path):
    state = Store(tmp_path / 'worker.db')
    handler = Handler()
    with pytest.raises(PermissionError, match='administration roles'):
        dispatch(handler, state, Service(), principal('Manager'), '/api/ui/job-feed', 'GET')


def test_job_feed_requires_jobs_authority_for_owner(tmp_path):
    state = Store(tmp_path / 'worker.db')
    handler = Handler()
    with pytest.raises(PermissionError, match='jobs scope required'):
        dispatch(handler, state, Service(), principal('Owner', ()), '/api/ui/job-feed', 'GET')


def test_job_feed_returns_only_sanitized_projection_for_owner(tmp_path):
    state = Store(tmp_path / 'worker.db')
    state.add_notification('ASK', 'Review safely.', 'ACTION_REQUIRED', domain='jobs', related_page='actions')
    command = state.queue_command('private command text')
    state.update_command(command, 'COMPLETED', 'private command result')

    handler = Handler();service = Service()
    assert dispatch(handler, state, service, principal('Owner'), '/api/ui/job-feed', 'GET') is True
    assert handler.status == 200
    serialized = str(handler.payload)
    assert handler.payload['counts']['action_required'] == 1
    assert 'private command text' not in serialized
    assert 'private command result' not in serialized
    assert ('Owner', 'work.read', 'jobs', False) in service.calls
