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

    from private_secrets.service import resolve_configured
    groq_key = resolve_configured(os.environ,'GROQ_API_KEY',consumer='jobs.gateway',domain='jobs').strip()
    mistral_key = resolve_configured(os.environ,'MISTRAL_API_KEY',consumer='jobs.gateway',domain='jobs').strip()
    if not groq_key:
        raise GatewayError("STOP: GROQ_API_KEY is required")
    if not mistral_key:
        raise GatewayError("STOP: MISTRAL_API_KEY is required")

    qwen_model = os.getenv("QWEN_MODEL", "qwen/qwen3.6-27b")
    mistral_model = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
    return legacy_job_router(
        QwenFreeProvider(groq_key, qwen_model),
        MistralFreeProvider(mistral_key, mistral_model),
        store=store,
    )


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
