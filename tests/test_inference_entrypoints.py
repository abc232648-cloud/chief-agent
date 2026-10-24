import ast
from pathlib import Path

import pytest

from gateway.entrypoints import ENTRY_POINTS
from gateway.models import AIRequest
from model_registry.contracts import Assignment
from model_registry.routing import ModelRouter
from tests.test_model_routing import Fake, registry


def test_production_inference_inventory_has_no_unlabeled_calls():
    root = Path(__file__).resolve().parents[1]
    found = []
    for directory in ('application', 'domains', 'gateway', 'model_registry', 'worker', 'skills'):
        for path in (root / directory).rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, 'id', getattr(node.func, 'attr', ''))
                if name != 'AIRequest':
                    continue
                args = {k.arg: k.value for k in node.keywords}
                assert 'purpose' in args, str(path)
                purpose = ast.literal_eval(args['purpose'])
                assert purpose in ENTRY_POINTS, str(path)
                assert 0 < ast.literal_eval(args['max_tokens']) <= ENTRY_POINTS[purpose].max_output_tokens
                found.append(purpose)
    assert sorted(found) == sorted(ENTRY_POINTS)


@pytest.mark.parametrize('purpose', ENTRY_POINTS)
def test_each_purpose_routes_only_to_its_reviewed_consumer(purpose):
    entry = ENTRY_POINTS[purpose]
    transport = Fake('synthetic', 'one')
    router = ModelRouter(registry(), Assignment(entry.domain, entry.agent, ('one',)),
                         {'one': transport}, capability=entry.capability, allow_unscoped=True)
    request = AIRequest('synthetic', 'synthetic', max_tokens=entry.max_output_tokens, purpose=purpose)
    assert router.generate(request).text == 'ok'
    assert len(transport.calls) == 1


@pytest.mark.parametrize('purpose', ['farming.ask', 'farming.connection_test', 'not.registered', [], True])
def test_wrong_domain_or_unregistered_purpose_never_reaches_transport(purpose):
    transport = Fake('synthetic', 'one')
    router = ModelRouter(registry(), Assignment('jobs', 'jobs-worker', ('one',)),
                         {'one': transport}, capability='jobs.execute', allow_unscoped=True)
    with pytest.raises(PermissionError):
        router.generate(AIRequest('s', 'u', max_tokens=30, purpose=purpose))
    assert not transport.calls


@pytest.mark.parametrize('limit', [None, 0, -1, True, 513, 10.5])
def test_missing_invalid_or_excessive_output_limit_never_reaches_transport(limit):
    transport = Fake('synthetic', 'one')
    router = ModelRouter(registry(), Assignment('jobs', 'jobs-worker', ('one',)),
                         {'one': transport}, capability='jobs.execute', allow_unscoped=True)
    with pytest.raises(ValueError):
        router.generate(AIRequest('s', 'u', max_tokens=limit, purpose='jobs.plan'))
    assert not transport.calls


def test_purpose_label_cannot_grant_missing_worker_context():
    router = ModelRouter(registry(), Assignment('jobs', 'jobs-worker', ('one',)), {}, capability='jobs.execute')
    with pytest.raises(PermissionError, match='context'):
        router.generate(AIRequest('s', 'u', max_tokens=512, purpose='jobs.plan'))


def test_inventory_is_immutable():
    with pytest.raises(TypeError):
        ENTRY_POINTS['unreviewed'] = ENTRY_POINTS['jobs.plan']
