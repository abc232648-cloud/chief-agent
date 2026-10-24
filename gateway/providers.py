from __future__ import annotations
import json
import os
from pathlib import Path
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .errors import InvalidProviderResponse, ProviderUnavailable, GatewayError
from .models import AIRequest, AIResponse


def _sdk_call(callback, message):
    status = None
    try:
        from networking.dns_policy import ensure_process_policy
        ensure_process_policy()
        return callback()
    except Exception as exc:
        value = getattr(exc, 'status_code', None)
        if type(value) is int and 100 <= value <= 599:
            status = value
    raise ProviderUnavailable(message, status_code=status) from None


def _messages(request: AIRequest):
    return [
        {"role": "system", "content": request.system},
        {"role": "user", "content": request.user},
    ]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


CHAT_ENDPOINTS = {
    'groq': 'https://api.groq.com/openai/v1/chat/completions',
    'mistral': 'https://api.mistral.ai/v1/chat/completions',
    'openai': 'https://api.openai.com/v1/chat/completions',
    'anthropic': 'https://api.anthropic.com/v1/messages',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
    'deepseek': 'https://api.deepseek.com/chat/completions',
    'xai': 'https://api.x.ai/v1/chat/completions',
    'openrouter': 'https://openrouter.ai/api/v1/chat/completions',
    'ollama': 'http://127.0.0.1:11434/api/chat',
}


class HttpProviderAdapter:
    """Unified HTTP adapter for all 9 supported Option B providers."""

    def __init__(self, provider: str, model: str, *, api_key: str | None = None,
                 key_resolver=None, endpoint: str | None = None, max_token_budget: int | None = None):
        self.provider = provider.lower()
        if self.provider not in CHAT_ENDPOINTS:
            raise ValueError(f'Unsupported provider: {provider}')
        self.model = model
        self.api_key = api_key
        self.key_resolver = key_resolver
        self.endpoint = endpoint or CHAT_ENDPOINTS[self.provider]
        self.max_token_budget = max_token_budget
        self.total_tokens_consumed = 0

    @property
    def name(self) -> str:
        return self.provider

    def _resolve_key(self) -> str | None:
        if self.api_key is not None:
            return self.api_key
        if self.key_resolver is not None:
            return self.key_resolver()
        return None

    def generate(self, request: AIRequest) -> AIResponse:
        if not isinstance(request, AIRequest):
            raise TypeError('AIRequest is required.')

        # Spending and token limits enforcement
        tokens_requested = request.max_tokens or 512
        if self.max_token_budget is not None:
            if self.total_tokens_consumed + tokens_requested > self.max_token_budget:
                raise GatewayError('STOP: Provider spending or token budget limit exceeded.')

        key = self._resolve_key()
        if self.provider != 'ollama' and not key:
            raise ProviderUnavailable(f'{self.provider.capitalize()} API key is required.')

        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        url = self.endpoint

        # Construct payload and headers per provider specification
        if self.provider in {'groq', 'mistral', 'openai', 'deepseek', 'xai', 'openrouter'}:
            headers['Authorization'] = f'Bearer {key}'
            payload = {
                'model': self.model,
                'messages': _messages(request),
                'temperature': request.temperature,
            }
            if request.max_tokens is not None:
                payload['max_tokens'] = request.max_tokens
            if self.provider == 'groq':
                payload['reasoning_format'] = 'hidden'
            data_bytes = json.dumps(payload).encode('utf-8')

        elif self.provider == 'anthropic':
            headers['x-api-key'] = key
            headers['anthropic-version'] = '2023-06-01'
            payload = {
                'model': self.model,
                'system': request.system,
                'messages': [{'role': 'user', 'content': request.user}],
                'temperature': request.temperature,
                'max_tokens': request.max_tokens or 1024,
            }
            data_bytes = json.dumps(payload).encode('utf-8')

        elif self.provider == 'gemini':
            headers['x-goog-api-key'] = key
            url = url.format(model=self.model)
            payload = {
                'contents': [{'role': 'user', 'parts': [{'text': request.user}]}],
                'generationConfig': {
                    'temperature': request.temperature,
                }
            }
            if request.system:
                payload['systemInstruction'] = {'parts': [{'text': request.system}]}
            if request.max_tokens is not None:
                payload['generationConfig']['maxOutputTokens'] = request.max_tokens
            data_bytes = json.dumps(payload).encode('utf-8')

        elif self.provider == 'ollama':
            parsed = urllib.parse.urlsplit(url)
            if parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
                raise GatewayError('STOP: Local runtime must target loopback (127.0.0.1) only.')
            if key:
                headers['Authorization'] = f'Bearer {key}'
            payload = {
                'model': self.model,
                'messages': _messages(request),
                'stream': False,
                'options': {
                    'temperature': request.temperature,
                }
            }
            if request.max_tokens is not None:
                payload['options']['num_predict'] = request.max_tokens
            data_bytes = json.dumps(payload).encode('utf-8')

        try:
            # Install process DNS policy if available
            try:
                from networking.dns_policy import ensure_process_policy
                ensure_process_policy()
            except ImportError:
                pass

            req = urllib.request.Request(url, data=data_bytes, headers=headers)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(req, timeout=15) as resp:
                resp_bytes = resp.read(1024 * 1024 + 1)
                if resp.status != 200 or len(resp_bytes) > 1024 * 1024:
                    raise InvalidProviderResponse(f'{self.provider.capitalize()} returned invalid HTTP response.')
                parsed_resp = json.loads(resp_bytes.decode('utf-8'))

            # Extract generated content per provider response schema
            if self.provider in {'groq', 'mistral', 'openai', 'deepseek', 'xai', 'openrouter'}:
                choices = parsed_resp.get('choices')
                if not choices or not isinstance(choices, list):
                    raise InvalidProviderResponse(f'{self.provider.capitalize()} response missing choices.')
                text = choices[0].get('message', {}).get('content')
            elif self.provider == 'anthropic':
                content = parsed_resp.get('content')
                if not content or not isinstance(content, list):
                    raise InvalidProviderResponse('Anthropic response missing content blocks.')
                text = content[0].get('text')
            elif self.provider == 'gemini':
                candidates = parsed_resp.get('candidates')
                if not candidates or not isinstance(candidates, list):
                    raise InvalidProviderResponse('Gemini response missing candidate list.')
                parts = candidates[0].get('content', {}).get('parts', [])
                text = parts[0].get('text') if parts else None
            elif self.provider == 'ollama':
                text = parsed_resp.get('message', {}).get('content')
            else:
                text = None

            if not text or not isinstance(text, str) or not text.strip():
                raise InvalidProviderResponse(f'{self.provider.capitalize()} returned empty content.')

            # Check secret leakage
            if key and key in text:
                raise InvalidProviderResponse('Security violation: secret API key detected in model response text.')

            self.total_tokens_consumed += tokens_requested
            return AIResponse(self.provider, self.model, text)

        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise ProviderUnavailable(f'{self.provider.capitalize()} HTTP error {code}', status_code=code) from None
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, socket.timeout) as exc:
            raise ProviderUnavailable(f'{self.provider.capitalize()} connection failed.') from None
        except (InvalidProviderResponse, GatewayError):
            raise
        except Exception as exc:
            raise InvalidProviderResponse(f'{self.provider.capitalize()} response parsing failed.') from None


# Backward-compatible classes
class QwenFreeProvider(HttpProviderAdapter):
    def __init__(self, api_key: str, model: str):
        super().__init__('groq', model, api_key=api_key)


class MistralFreeProvider(HttpProviderAdapter):
    def __init__(self, api_key: str, model: str):
        super().__init__('mistral', model, api_key=api_key)
