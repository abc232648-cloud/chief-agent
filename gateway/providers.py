from __future__ import annotations

from .errors import InvalidProviderResponse, ProviderUnavailable
from .models import AIRequest, AIResponse


def _sdk_call(callback, message):
    status=None
    try:
        return callback()
    except Exception as exc:
        value=getattr(exc,'status_code',None)
        if type(value) is int and 100<=value<=599:status=value
    # Raise outside the handler: raw SDK messages, requests and credentials are
    # not retained in a chained exception or printed by direct adapter callers.
    raise ProviderUnavailable(message,status_code=status) from None


def _messages(request: AIRequest):
    return [
        {"role": "system", "content": request.system},
        {"role": "user", "content": request.user},
    ]


class QwenFreeProvider:
    name = "qwen"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("GROQ_API_KEY is required")
        self.model = model
        try:
            from groq import Groq
        except ImportError as exc:
            raise ProviderUnavailable("Groq SDK is not installed") from exc
        self.client = _sdk_call(lambda:Groq(api_key=api_key), 'Groq client could not be initialized.')

    def generate(self, request: AIRequest) -> AIResponse:
        def call():
            kwargs = {"model": self.model, "messages": _messages(request), "temperature": request.temperature}
            if request.max_tokens is not None:
                kwargs["max_tokens"] = request.max_tokens
            kwargs["reasoning_format"] = "hidden"

            response = self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content if response.choices else None
        text = _sdk_call(call, 'Qwen is unavailable.')
        if not text:
            raise InvalidProviderResponse("Qwen returned empty content")
        return AIResponse(self.name, self.model, text)


class MistralFreeProvider:
    name = "mistral"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("MISTRAL_API_KEY is required")
        self.model = model
        try:
            from mistralai.client import Mistral
        except ImportError as exc:
            raise ProviderUnavailable("Mistral SDK is not installed") from exc
        self.client = _sdk_call(lambda:Mistral(api_key=api_key), 'Mistral client could not be initialized.')

    def generate(self, request: AIRequest) -> AIResponse:
        def call():
            kwargs = {"model": self.model, "messages": _messages(request), "temperature": request.temperature}
            if request.max_tokens is not None:
                kwargs["max_tokens"] = request.max_tokens
            response = self.client.chat.complete(**kwargs)
            return response.choices[0].message.content if response.choices else None
        text = _sdk_call(call, 'Mistral is unavailable.')
        if not text:
            raise InvalidProviderResponse("Mistral returned empty content")
        return AIResponse(self.name, self.model, text)
