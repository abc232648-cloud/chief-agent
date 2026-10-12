from types import SimpleNamespace
import traceback
import pytest
from gateway.errors import ProviderUnavailable
from gateway.models import AIRequest
from gateway.providers import QwenFreeProvider,MistralFreeProvider,_sdk_call


@pytest.mark.parametrize('provider_class',[QwenFreeProvider,MistralFreeProvider])
@pytest.mark.parametrize('status',[401,429,503])
def test_direct_provider_errors_drop_sdk_payload_and_preserve_status(provider_class,status):
    class Failure(Exception):status_code=status
    def fail(**kwargs):raise Failure('SYNTHETIC_PRIVATE_KEY private prompt response')
    provider=object.__new__(provider_class);provider.model='synthetic-model'
    provider.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail),complete=fail))
    with pytest.raises(ProviderUnavailable) as caught:provider.generate(AIRequest('system','private prompt'))
    error=caught.value
    assert error.status_code==status and error.__cause__ is None and error.__context__ is None
    assert 'SYNTHETIC_PRIVATE_KEY' not in ''.join(traceback.format_exception(error))
    assert 'private prompt response' not in str(error)


def test_client_initialization_failure_is_also_sanitized():
    def fail():raise ValueError('SYNTHETIC_PRIVATE_KEY')
    with pytest.raises(ProviderUnavailable) as caught:_sdk_call(fail,'Provider initialization unavailable.')
    assert caught.value.__context__ is None and 'SYNTHETIC_PRIVATE_KEY' not in str(caught.value)
