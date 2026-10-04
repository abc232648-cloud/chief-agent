from __future__ import annotations

from .errors import GatewayError, PaidRouteBlocked, ProviderUnavailable
from .models import AIRequest, AIResponse


class FreeOnlyGateway:
    """Qwen-first gateway with Mistral fallback and no paid route."""

    def __init__(self, qwen, mistral, *, free_only: bool = True, mistral_paid_allowed: bool = False):
        if not free_only:
            raise PaidRouteBlocked("STOP: FREE_ONLY must be TRUE")
        if mistral_paid_allowed:
            raise PaidRouteBlocked("STOP: MISTRAL_PAID_ALLOWED must be FALSE")
        self.qwen = qwen
        self.mistral = mistral

    def generate(self, request: AIRequest) -> AIResponse:
        try:
            return self.qwen.generate(request)
        except ProviderUnavailable:
            pass
        try:
            response = self.mistral.generate(request)
            response.fallback_used = True
            return response
        except ProviderUnavailable as exc:
            raise GatewayError("STOP: No approved free AI route is available") from exc
