"""Tests for actual request/response execution adapters for all 9 Option B providers,
spending controls, private credential handling, explicit fallback, and loopback SSRF rejection.
"""
import io
import json
import pytest
import urllib.request

from gateway.models import AIRequest, AIResponse
from gateway.errors import ProviderUnavailable, InvalidProviderResponse, GatewayError
from gateway.providers import HttpProviderAdapter
from model_registry.contracts import Assignment, Model, Provider, Cost
from model_registry.registry import ModelRegistry
from model_registry.routing import ModelRouter


class DummyHTTPResponse(io.BytesIO):
    def __init__(self, body_bytes: bytes, status: int = 200):
        super().__init__(body_bytes)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.mark.parametrize('provider,model,expected_url,sample_response,extracted_text', [
    ('groq', 'llama-3.3-70b-versatile', 'https://api.groq.com/openai/v1/chat/completions',
     {'choices': [{'message': {'content': 'Groq output answer.'}}]}, 'Groq output answer.'),
    ('mistral', 'mistral-small-latest', 'https://api.mistral.ai/v1/chat/completions',
     {'choices': [{'message': {'content': 'Mistral output answer.'}}]}, 'Mistral output answer.'),
    ('openai', 'gpt-4o-mini', 'https://api.openai.com/v1/chat/completions',
     {'choices': [{'message': {'content': 'OpenAI output answer.'}}]}, 'OpenAI output answer.'),
    ('anthropic', 'claude-3-5-sonnet-20241022', 'https://api.anthropic.com/v1/messages',
     {'content': [{'text': 'Anthropic output answer.'}]}, 'Anthropic output answer.'),
    ('gemini', 'gemini-1.5-flash', 'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent',
     {'candidates': [{'content': {'parts': [{'text': 'Gemini output answer.'}]}}]}, 'Gemini output answer.'),
    ('deepseek', 'deepseek-chat', 'https://api.deepseek.com/chat/completions',
     {'choices': [{'message': {'content': 'DeepSeek output answer.'}}]}, 'DeepSeek output answer.'),
    ('xai', 'grok-beta', 'https://api.x.ai/v1/chat/completions',
     {'choices': [{'message': {'content': 'xAI output answer.'}}]}, 'xAI output answer.'),
    ('openrouter', 'meta-llama/llama-3.3-70b-instruct', 'https://openrouter.ai/api/v1/chat/completions',
     {'choices': [{'message': {'content': 'OpenRouter output answer.'}}]}, 'OpenRouter output answer.'),
    ('ollama', 'llama3:8b', 'http://127.0.0.1:11434/api/chat',
     {'message': {'content': 'Ollama local output.'}}, 'Ollama local output.'),
])
def test_all_nine_provider_execution_adapters(monkeypatch, provider, model, expected_url, sample_response, extracted_text):
    captured_requests = []

    def mock_open(self, req, timeout=15):
        captured_requests.append(req)
        return DummyHTTPResponse(json.dumps(sample_response).encode('utf-8'))

    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', mock_open)

    adapter = HttpProviderAdapter(provider, model, api_key='sk-test-secret-key-12345')
    request = AIRequest(system='You are a farming assistant.', user='Check chicken feed ratio.', temperature=0.2, max_tokens=100)
    response = adapter.generate(request)

    assert response.provider == provider
    assert response.model == model
    assert response.text == extracted_text
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.full_url == expected_url

    # Check headers
    if provider in {'groq', 'mistral', 'openai', 'deepseek', 'xai', 'openrouter'}:
        assert req.get_header('Authorization') == 'Bearer sk-test-secret-key-12345'
    elif provider == 'anthropic':
        assert req.get_header('X-api-key') == 'sk-test-secret-key-12345'
        assert req.get_header('Anthropic-version') == '2023-06-01'
    elif provider == 'gemini':
        assert req.get_header('X-goog-api-key') == 'sk-test-secret-key-12345'


def test_spending_control_token_budget_enforcement():
    adapter = HttpProviderAdapter('groq', 'llama-3.3-70b-versatile', api_key='sk-test', max_token_budget=150)
    request1 = AIRequest(system='', user='Prompt 1', max_tokens=100)
    adapter.total_tokens_consumed = 100

    # Next request requesting 100 tokens exceeds 150 budget
    request2 = AIRequest(system='', user='Prompt 2', max_tokens=100)
    with pytest.raises(GatewayError, match='spending or token budget limit exceeded'):
        adapter.generate(request2)


def test_ollama_ssrf_rejection():
    # Attempting to target external server via Ollama adapter must raise GatewayError
    adapter = HttpProviderAdapter('ollama', 'llama3:8b', endpoint='http://192.168.1.50:11434/api/chat')
    request = AIRequest(system='', user='Test prompt')
    with pytest.raises(GatewayError, match='Local runtime must target loopback'):
        adapter.generate(request)


def test_secret_api_key_leakage_in_response_is_rejected(monkeypatch):
    secret_key = 'sk-super-secret-key-99999'

    def mock_open(self, req, timeout=15):
        # Malicious or buggy model echoes private API key in text
        leak_response = {'choices': [{'message': {'content': f'Here is your key: {secret_key}'}}]}
        return DummyHTTPResponse(json.dumps(leak_response).encode('utf-8'))

    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', mock_open)

    adapter = HttpProviderAdapter('openai', 'gpt-4o-mini', api_key=secret_key)
    request = AIRequest(system='', user='Echo test')
    with pytest.raises(InvalidProviderResponse, match='secret API key detected in model response text'):
        adapter.generate(request)


def test_explicit_fallback_routing():
    # Setup router with primary (groq) and secondary (mistral)
    class FailingTransport:
        def generate(self, req):
            raise ProviderUnavailable('Groq service temporarily unavailable.')

    class WorkingFallbackTransport:
        def generate(self, req):
            return AIResponse('mistral', 'mistral-small-latest', 'Fallback response from Mistral.')

    models = (
        Model('jobs.groq', 'groq', 'llama-3.3-70b-versatile', Cost.FREE),
        Model('jobs.mistral', 'mistral', 'mistral-small-latest', Cost.FREE)
    )
    reg = ModelRegistry((Provider('groq'), Provider('mistral')), models)
    assignment = Assignment('jobs', 'jobs-worker', ('jobs.groq', 'jobs.mistral'))
    transports = {'jobs.groq': FailingTransport(), 'jobs.mistral': WorkingFallbackTransport()}

    router = ModelRouter(reg, assignment, transports, capability='jobs.execute', allow_unscoped=True)
    req = AIRequest(system='Sys', user='User', max_tokens=32, purpose='jobs.connection_test')
    res = router.generate(req)

    assert res.provider == 'mistral'
    assert res.fallback_used is True
    assert 'Fallback response from Mistral' in res.text
