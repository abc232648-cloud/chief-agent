"""Validated immutable capability snapshot. AgentRegistry remains independent."""
from dataclasses import asdict
import json
from pathlib import PurePosixPath
from types import MappingProxyType

from .contracts import Capability, Component, ComponentKind, Consumer, Dependency, Maturity, Mode, Node, version
from .graph import DependencyGraph


def _strings(values, label, *, nonempty=False):
    if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError(label + ' must be an immutable tuple of nonempty strings.')
    if len(set(values)) != len(values) or (nonempty and not values):
        raise ValueError(label + ' must contain unique declarations and may not be empty where required.')


def _tests(values, *, nonempty=False):
    _strings(values, 'Tests', nonempty=nonempty)
    for value in values:
        path = PurePosixPath(value)
        if (path.is_absolute() or len(path.parts) < 2 or path.parts[0] != 'tests'
                or '..' in path.parts or '\\' in value or ':' in value
                or not path.name.startswith('test_') or path.suffix != '.py' or str(path) != value):
            raise ValueError('Tests must be canonical project-relative test module paths.')


class CapabilityRegistry:
    def __init__(self, components, capabilities):
        components, capabilities = tuple(components), tuple(capabilities)
        catalog, declarations = {}, {}
        for component in components:
            if not isinstance(component, Component):
                raise ValueError('Expected a component declaration.')
            Node('component', component.id)
            version(component.version)
            if not isinstance(component.kind, ComponentKind):
                raise ValueError('Unknown component kind.')
            if component.id in catalog:
                raise ValueError('Duplicate component: ' + component.id)
            _tests(component.tests)
            _strings(component.permissions, 'Component permissions')
            for permission in component.permissions:
                Node('capability', permission)
                if '.' not in permission or (component.kind == ComponentKind.DOMAIN and not permission.startswith(component.id + '.')):
                    raise ValueError('Domain permissions must belong to their own namespace.')
            catalog[component.id] = component
        for capability in capabilities:
            if not isinstance(capability, Capability):
                raise ValueError('Expected a capability declaration.')
            Node('capability', capability.id)
            version(capability.version)
            if capability.id in declarations:
                raise ValueError('Duplicate capability: ' + capability.id)
            declarations[capability.id] = capability
        for capability in capabilities:
            owner = catalog.get(capability.owner)
            if owner is None or owner.kind not in (ComponentKind.CORE, ComponentKind.DOMAIN):
                raise ValueError('Capability owner must be a known Core or domain component.')
            if not capability.id.startswith(owner.id + '.'):
                raise ValueError('Capability ID must use its declared owner namespace.')
            if not isinstance(capability.maturity, Maturity) or not isinstance(capability.mode, Mode):
                raise ValueError('Unknown capability maturity or mode.')
            for label in ('description', 'inputs', 'outputs', 'failure_behavior'):
                if not isinstance(getattr(capability, label), str) or not getattr(capability, label).strip():
                    raise ValueError('Missing contract field: ' + label)
            for label in ('permissions', 'frameworks', 'data_access', 'models', 'health_dependencies', 'side_effects', 'overrides', 'audit'):
                _strings(getattr(capability, label), label)
            _tests(capability.tests, nonempty=True)
            if not set(capability.permissions) <= set(owner.permissions):
                raise ValueError('Capability declares permissions outside its owner grants.')
            if any(name not in catalog for name in capability.health_dependencies):
                raise ValueError('Unknown health dependency.')
            if not isinstance(capability.consumers, tuple):
                raise ValueError('Consumers must be immutable.')
            seen = set()
            for consumer in capability.consumers:
                if not isinstance(consumer, Consumer) or type(consumer.required) is not bool:
                    raise ValueError('Invalid consumer declaration.')
                if consumer.component not in catalog or consumer.component in seen:
                    raise ValueError('Unknown or duplicate consumer.')
                seen.add(consumer.component)
                _tests(consumer.tests, nonempty=consumer.required)
                if not set(capability.permissions) <= set(catalog[consumer.component].permissions):
                    raise ValueError('Consumer lacks declared capability permissions.')
        nodes = {Node('component', c.id): c for c in components}
        nodes.update({Node('capability', c.id): c for c in capabilities})
        for item in (*components, *capabilities):
            if not isinstance(item.dependencies, tuple):
                raise ValueError('Dependencies must be immutable.')
            seen = set()
            for dependency in item.dependencies:
                if not isinstance(dependency, Dependency) or not isinstance(dependency.node, Node):
                    raise ValueError('Invalid dependency declaration.')
                if dependency.node not in nodes or dependency.node in seen:
                    raise ValueError('Unknown or duplicate dependency.')
                seen.add(dependency.node)
                if not dependency.accepts(nodes[dependency.node].version):
                    raise ValueError('Incompatible dependency: ' + dependency.node.key)
        graph = DependencyGraph(components, capabilities)
        self._components = MappingProxyType(dict(sorted(catalog.items())))
        self._capabilities = MappingProxyType(dict(sorted(declarations.items())))
        self._graph = graph

    @property
    def components(self):
        return self._components

    @property
    def capabilities(self):
        return self._capabilities

    @property
    def graph(self):
        return self._graph

    def describe(self):
        # Detached JSON values: callers cannot mutate the validated snapshot.
        return json.loads(json.dumps({
            'components': [asdict(c) for c in self.components.values()],
            'capabilities': [asdict(c) for c in self.capabilities.values()]}))

    def required_consumers(self, changes):
        affected = set(self.graph.impact(changes))
        return tuple(sorted({consumer.component for capability in self.capabilities.values()
                             for consumer in capability.consumers if consumer.required and (
                                 Node('capability', capability.id) in affected
                                 or Node('component', consumer.component) in affected)}))
