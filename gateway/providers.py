from __future__ import annotations

from .errors import InvalidProviderResponse, ProviderUnavailable
from .models import AIRequest, AIResponse


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
        self.client = Groq(api_key=api_key)

    def generate(self, request: AIRequest) -> AIResponse:
        try:
            kwargs = {"model": self.model, "messages": _messages(request), "temperature": request.temperature}
            if request.max_tokens is not None:
                kwargs["max_tokens"] = request.max_tokens
            kwargs["reasoning_format"] = "hidden"

            response = self.client.chat.completions.create(**kwargs)
            text = response.choices[0].message.content if response.choices else None
        except Exception as exc:
            raise ProviderUnavailable(f"Qwen unavailable: {exc}") from exc
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
        self.client = Mistral(api_key=api_key)

    def generate(self, request: AIRequest) -> AIResponse:
        try:
            kwargs = {"model": self.model, "messages": _messages(request), "temperature": request.temperature}
            if request.max_tokens is not None:
                kwargs["max_tokens"] = request.max_tokens
            response = self.client.chat.complete(**kwargs)
            text = response.choices[0].message.content if response.choices else None
        except Exception as exc:
            raise ProviderUnavailable(f"Mistral unavailable: {exc}") from exc
        if not text:
            raise InvalidProviderResponse("Mistral returned empty content")
        return AIResponse(self.name, self.model, text)
