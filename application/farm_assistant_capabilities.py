"""Human-only text assistance; no grant to the legacy Farm recorder."""
from capabilities.contracts import Capability, Component, ComponentKind, Consumer, Dependency, Maturity, Mode, Node

TESTS = ('tests/test_farm_live_ai.py',)


def components():
    return (Component('farm-assistant', ComponentKind.SERVICE,
        dependencies=(Dependency(Node('component', 'chief.model_registry')),), tests=TESTS),)


def capabilities():
    return (Capability(id='farming.assistant.ask', owner='farming', version='1.0.0',
        description='Human-requested Farm Qwen guidance with no action authority.',
        maturity=Maturity.EXPERIMENTAL, mode=Mode.ENABLED,
        dependencies=(Dependency(Node('component', 'chief.model_registry')),),
        consumers=(Consumer('farm-assistant', True, TESTS),), permissions=(),
        frameworks=('Server-side human scope and component/model controls.',),
        data_access=('Authorized Farm context only; no Job data or credential payloads.',),
        models=('farming.qwen',), tests=TESTS, health_dependencies=('chief.gateway',),
        inputs='Authenticated question and permitted Farm context.', outputs='Unverified text guidance.',
        side_effects=('Explicit provider inference request; metadata-only audit.',),
        failure_behavior='Fail closed; no fallback, tool execution or inferred approval.',
        overrides=('Global model disable and human revocation override in-flight responses.',),
        audit=('Human security event references without prompts or answers.',)),)
