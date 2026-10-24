"""Tests for Farm Live AI Option B provider configuration, Owner authorization,
qualification testing through ModelRouter, consequence summary, and guidance-only execution.
"""
import io
import json
import os
from pathlib import Path
import pytest
import urllib.request

from domains.farming import live_ai, assistant
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_tasks import staff


class DummyHTTPResponse(io.BytesIO):
    def __init__(self, body_bytes: bytes, status: int = 200):
        super().__init__(body_bytes)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_farm_live_ai_owner_qualification_and_consequence_summary(dashboard, monkeypatch):
    d = dashboard
    _, _, _, _, _, manager, _, worker, _, _, _ = staff(d)

    # Setup state root and register a mock provider connection (e.g. OpenAI)
    from model_registry.setup import ModelSetup
    setup = ModelSetup(Path(os.environ['CHIEF_STATE_ROOT']))
    reg = setup.add({
        'provider': 'openai',
        'provider_model': 'gpt-4o-mini',
        'cost': 'FREE',
        'api_key': 'sk-test-mock-key-12345'
    }, actor='owner')

    owner_principal = d.credentials['principal']

    # Non-owner (Worker or Manager) cannot configure
    with pytest.raises(PermissionError, match='Only the Owner'):
        live_ai.configure(d.store, worker, {
            'operation': 'qualify',
            'registration': reg['id'],
            'free_account_confirmed': True
        })

    with pytest.raises(PermissionError, match='Only the Owner'):
        live_ai.configure(d.store, manager, {
            'operation': 'qualify',
            'registration': reg['id'],
            'free_account_confirmed': True
        })

    # Mock provider response for qualification test
    mock_response = {'choices': [{'message': {'content': 'connection working'}}]}
    def mock_open(self, req, timeout=15):
        return DummyHTTPResponse(json.dumps(mock_response).encode('utf-8'))
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', mock_open)
    monkeypatch.setenv('CHIEF_INSTANCE_MODE', 'production')

    # Owner qualifies connection
    res = live_ai.configure(d.store, owner_principal, {
        'operation': 'qualify',
        'registration': reg['id'],
        'free_account_confirmed': True,
        'consequence_summary_acknowledged': True
    })

    assert res['status'] == 'ACTIVE'
    assert res['provider'] == 'openai'
    assert res['model'] == 'gpt-4o-mini'
    assert res['action_authority'] == 'NONE'

    # Check status endpoint
    st = live_ai.status(d.store, owner_principal)
    assert st['status'] == 'LIVE_AI_CONFIGURED'
    assert st['provider'] == 'openai'
    assert 'Openai answers are guidance, not verified facts' in st['notice']
    assert 'Questions and permitted context are sent to Openai' in st['notice']

    # Now ask a guidance question through live_ai
    ans_res = {'choices': [{'message': {'content': 'Check nipple drinker line height so birds stretch slightly when drinking.'}}]}
    def mock_answer_open(self, req, timeout=15):
        return DummyHTTPResponse(json.dumps(ans_res).encode('utf-8'))
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', mock_answer_open)

    ans = assistant.ask(d.store, worker, {'question': 'How high should water lines be?'})
    assert ans['status'] == 'LIVE_AI_GUIDANCE'
    assert 'nipple drinker line height' in ans['answer']
    assert ans['action_authority'] == 'NONE'
    assert ans['external_requests'] == 1


def test_qualification_failure_blocks_activation(dashboard, monkeypatch):
    d = dashboard
    from model_registry.setup import ModelSetup
    setup = ModelSetup(Path(os.environ['CHIEF_STATE_ROOT']))
    reg = setup.add({
        'provider': 'anthropic',
        'provider_model': 'claude-3-5-sonnet-20241022',
        'cost': 'FREE',
        'api_key': 'sk-ant-test-key'
    }, actor='owner')

    owner_principal = d.credentials['principal']
    monkeypatch.setenv('CHIEF_INSTANCE_MODE', 'production')

    # Failing provider mock
    def mock_fail(self, req, timeout=15):
        raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'Bad key'))
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', mock_fail)

    # Qualification must fail and NOT activate
    with pytest.raises(ValueError, match='qualification failed'):
        live_ai.configure(d.store, owner_principal, {
            'operation': 'qualify',
            'registration': reg['id'],
            'free_account_confirmed': True
        })

    # Status remains PENDING
    st = live_ai.status(d.store, owner_principal)
    assert st['status'] == 'LIVE_AI_PENDING'
