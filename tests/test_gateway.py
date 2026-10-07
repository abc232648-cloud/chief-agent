import pytest
from gateway.errors import GatewayError, PaidRouteBlocked, ProviderUnavailable
from gateway.models import AIRequest, AIResponse
from gateway.router import FreeOnlyGateway

class Fake:
    def __init__(self, result): self.result = result
    def generate(self, request):
        if isinstance(self.result, Exception): raise self.result
        return self.result

def req(): return AIRequest("test", "test")

def test_qwen_primary():
    r = FreeOnlyGateway(Fake(AIResponse("qwen", "q", "ok")), Fake(AIResponse("mistral", "m", "fallback"))).generate(req())
    assert r.provider == "qwen"

def test_mistral_fallback():
    r = FreeOnlyGateway(Fake(ProviderUnavailable()), Fake(AIResponse("mistral", "m", "fallback"))).generate(req())
    assert r.provider == "mistral" and r.fallback_used

def test_stop():
    with pytest.raises(GatewayError, match="STOP"):
        FreeOnlyGateway(Fake(ProviderUnavailable()), Fake(ProviderUnavailable())).generate(req())

def test_paid_flags_block_before_provider_use():
    with pytest.raises(PaidRouteBlocked, match="FREE_ONLY"):
        FreeOnlyGateway(Fake(None), Fake(None), free_only=False)
    with pytest.raises(PaidRouteBlocked, match="MISTRAL_PAID_ALLOWED"):
        FreeOnlyGateway(Fake(None), Fake(None), mistral_paid_allowed=True)

def test_build_gateway_without_keys_starts_but_ai_request_stops(monkeypatch):
    import gateway.main as main
    for name in ('GROQ_API_KEY','MISTRAL_API_KEY','GROQ_API_KEY_REF','MISTRAL_API_KEY_REF'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('FREE_ONLY','TRUE')
    monkeypatch.setenv('MISTRAL_PAID_ALLOWED','FALSE')
    gateway=main.build_gateway()
    with pytest.raises(GatewayError,match='STOP'):
        gateway.generate(req())

def test_build_gateway_guardrails_stop_before_provider(monkeypatch):
    import gateway.main as main
    monkeypatch.setenv("FREE_ONLY", "FALSE")
    monkeypatch.setenv("MISTRAL_PAID_ALLOWED", "FALSE")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral")
    try:
        main.build_gateway()
    except Exception as exc:
        assert str(exc) == "STOP: FREE_ONLY must be TRUE"
    else:
        raise AssertionError("build_gateway() should stop")

def test_main_returns_one_on_gateway_stop(monkeypatch, capsys):
    import gateway.main as main
    monkeypatch.setattr(main, "smoke_test", lambda: (_ for _ in ()).throw(main.GatewayError("STOP: test")))
    assert main.main() == 1
    assert "STOP: test" in capsys.readouterr().err


def test_real_mistral_sdk_import_surface():
    """Exercise the real installed SDK surface; provider mocks cannot catch SDK drift."""
    sdk = pytest.importorskip("mistralai")
    from mistralai.client import Mistral
    from gateway.providers import MistralFreeProvider
    assert Mistral is not None
    provider = MistralFreeProvider("test-key", "mistral-small-latest")
    assert isinstance(provider.client, Mistral)
