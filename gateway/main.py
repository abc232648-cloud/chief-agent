from __future__ import annotations

import os
import sys

from .errors import GatewayError, PaidRouteBlocked
from .models import AIRequest
from .providers import MistralFreeProvider, QwenFreeProvider
from .router import FreeOnlyGateway
from model_registry.routing import legacy_job_router, ModelRouter
from config.runtime import load_environment


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().upper()
    if raw not in {"TRUE", "FALSE"}:
        raise ValueError(f"{name} must be TRUE or FALSE")
    return raw == "TRUE"


def build_gateway(*, store=None) -> ModelRouter:
    """Build the reusable gateway; validates safety config before providers are touched."""
    free_only = _env_bool("FREE_ONLY", True)
    mistral_paid_allowed = _env_bool("MISTRAL_PAID_ALLOWED", False)
    if not free_only:
        raise PaidRouteBlocked("STOP: FREE_ONLY must be TRUE")
    if mistral_paid_allowed:
        raise PaidRouteBlocked("STOP: MISTRAL_PAID_ALLOWED must be FALSE")

    from .availability import AvailableProvider
    qwen_model = os.getenv("QWEN_MODEL", "qwen/qwen3.6-27b")
    mistral_model = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
    router = legacy_job_router(
        AvailableProvider(QwenFreeProvider, qwen_model, "GROQ_API_KEY", "Groq", store=store),
        AvailableProvider(MistralFreeProvider, mistral_model, "MISTRAL_API_KEY", "Mistral", store=store),
        store=store,
    )
    assignment = router.registry.assignment(router.default)
    for identity in assignment.models:
        if router.registry.eligible(identity, assignment):
            transport = router.transports.get(identity)
            if isinstance(transport, AvailableProvider):
                transport.inspect()
    return router


def smoke_test() -> int:
    gateway = build_gateway()
    response = gateway.generate(AIRequest("You are a connectivity test.", "Reply with exactly: GATEWAY_OK"))
    print(f"provider={response.provider}")
    print(f"model={response.model}")
    print(f"fallback_used={response.fallback_used}")
    print(f"text={response.text}")
    return 0


def main() -> int:
    try:
        load_environment()
        return smoke_test()
    except (GatewayError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
