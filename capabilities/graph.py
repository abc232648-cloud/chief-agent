"""Deterministic dependency traversal; no domain imports or runtime discovery."""
from types import MappingProxyType

from .contracts import Node


class DependencyGraph:
    def __init__(self, components, capabilities):
        edges = {Node('component', c.id): {d.node for d in c.dependencies} for c in components}
        for capability in capabilities:
            edges[Node('capability', capability.id)] = (
                {d.node for d in capability.dependencies}
                | {Node('component', name) for name in capability.health_dependencies})
        for capability in capabilities:
            for consumer in capability.consumers:
                edges[Node('component', consumer.component)].add(Node('capability', capability.id))
        self._edges = MappingProxyType({node: tuple(sorted(deps)) for node, deps in sorted(edges.items())})
        reverse = {node: set() for node in edges}
        for node, dependencies in self._edges.items():
            for dependency in dependencies:
                if dependency not in reverse:
                    raise ValueError('Unknown dependency: ' + dependency.key)
                reverse[dependency].add(node)
        self._reverse = MappingProxyType({node: tuple(sorted(users)) for node, users in reverse.items()})
        # Ownership is an impact relation, not an execution dependency. An owner
        # can consume its own capability without manufacturing a dependency cycle.
        for capability in capabilities:
            reverse[Node('component', capability.owner)].add(Node('capability', capability.id))
        self._impact_reverse = MappingProxyType({node: tuple(sorted(users)) for node, users in reverse.items()})
        self.topological_order()  # Reject cycles before exposing a usable graph.

    @property
    def nodes(self):
        return tuple(self._edges)

    def _require(self, node):
        if node not in self._edges:
            raise ValueError('Unknown graph node: ' + node.key)

    def _walk(self, node, adjacency):
        self._require(node)
        seen = set()
        pending = list(adjacency[node])
        while pending:
            item = pending.pop()
            if item not in seen:
                seen.add(item)
                pending.extend(adjacency[item])
        return tuple(sorted(seen))

    def dependencies(self, node, *, transitive=True):
        self._require(node)
        return self._walk(node, self._edges) if transitive else self._edges[node]

    def dependents(self, node, *, transitive=True):
        self._require(node)
        return self._walk(node, self._reverse) if transitive else self._reverse[node]

    def impact(self, changes):
        result = set()
        for node in changes:
            self._require(node)
            result.add(node)
            result.update(self._walk(node, self._impact_reverse))
        return tuple(sorted(result))

    def topological_order(self):
        # Kahn traversal avoids a recursion limit for long plugin dependency chains.
        remaining = {node: set(deps) for node, deps in self._edges.items()}
        ordered = []
        while remaining:
            ready = sorted(node for node, deps in remaining.items() if not deps)
            if not ready:
                raise ValueError('Dependency cycle involving: ' + ', '.join(n.key for n in sorted(remaining)))
            ordered.extend(ready)
            for node in ready:
                del remaining[node]
            for deps in remaining.values():
                deps.difference_update(ready)
        return tuple(ordered)
