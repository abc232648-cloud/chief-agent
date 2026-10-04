"""Dependency-aware transitions; only explicitly guarded adapters can change mode."""
from dataclasses import asdict
import hashlib
import json
from types import MappingProxyType

from capabilities.contracts import ComponentKind, Mode, Node
from database.component_state import ComponentState
from .component_contracts import DesiredState, TransitionPreview, StaleTransition
from .legacy_components import domain_state


class ComponentControls:
    def __init__(self, store, catalog, *, supported, guarded_consumers):
        self.store, self.catalog = store, catalog
        self.supported = MappingProxyType({node: tuple(modes) for node, modes in supported.items()})
        self.guarded_consumers = frozenset(guarded_consumers)
        self.state = ComponentState(store)
        for node, modes in self.supported.items():
            self.catalog.graph.dependencies(node)
            if not modes or any(mode not in (Mode.ENABLED, Mode.DISABLED, Mode.MAINTENANCE, Mode.SHADOW) for mode in modes):
                raise ValueError('Invalid supported mode contract.')

    def desired(self, node, con=None):
        if con is None:
            with self.store._connect() as current:
                return self.desired(node, current)
        self.catalog.graph.dependencies(node)
        if node.kind == 'component' and self.catalog.components[node.id].kind == ComponentKind.DOMAIN:
            return domain_state(con, node)
        row = next((r for r in self.state.rows(con) if (r['kind'], r['id']) == (node.kind, node.id)), None)
        return DesiredState(node, Mode(row['mode']) if row else Mode.ENABLED,
                            row['revision'] if row else 0, 'component_modes' if row else 'legacy_default',
                            self.supported.get(node, ()))

    def _preview(self, con, node, mode):
        if not isinstance(mode, Mode) or mode not in self.supported.get(node, ()):
            raise ValueError('Unsupported transition; legacy domains use their existing control API.')
        affected = self.catalog.graph.impact((node,))
        consumers = {n for n in affected if n.kind == 'component' and n != node}
        if mode != Mode.ENABLED and not consumers <= self.guarded_consumers:
            raise ValueError('Dependency impact includes a consumer without a safe control adapter.')
        rows = self.state.rows(con)
        legacy = [tuple(r) for r in con.execute('SELECT * FROM agent_controls ORDER BY domain')]
        token = hashlib.sha256(json.dumps([self.catalog.describe(), rows, legacy, node.key, mode.value,
                                           [(n.key, [m.value for m in modes]) for n, modes in sorted(self.supported.items())],
                                           sorted(n.key for n in self.guarded_consumers)], sort_keys=True).encode()).hexdigest()
        return TransitionPreview(node, mode, token, affected, mode != Mode.ENABLED and len(affected) > 1)

    def preview(self, node, mode):
        with self.store._connect() as con:
            con.execute('BEGIN')
            return self._preview(con, node, mode)

    def transition(self, preview, *, actor, reason, confirmed=False):
        if not isinstance(preview, TransitionPreview) or not isinstance(actor, str) or not actor.strip() or len(actor)>200:
            raise ValueError('A preview and explicit actor are required.')
        if not isinstance(reason, str) or not reason.strip() or len(reason)>1000:
            raise ValueError('A bounded transition reason is required.')
        if type(confirmed) is not bool:
            raise ValueError('Confirmation must be boolean.')
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = self._preview(con, preview.node, preview.mode)
            if current != preview:
                raise StaleTransition('Control/catalog state changed; obtain a fresh impact preview.')
            if current.requires_confirmation and not confirmed:
                raise ValueError('Confirm the affected consumers before applying this transition.')
            self.state.put(con, preview.node, preview.mode, actor=actor, reason=reason)
            return self.desired(preview.node, con)

    def allowed(self, agent_id, capability=None, con=None):
        if con is None:
            with self.store._connect() as current:
                return self.allowed(agent_id, capability, current)
        nodes = [Node('component', agent_id)]
        if capability is not None:
            nodes.append(Node('capability', capability))
        for node in tuple(nodes):
            nodes.extend(self.catalog.graph.dependencies(node))
        # SHADOW is not enabled for current side-effecting adapters. Never infer an executor.
        return all(self.state.mode(con, node) == Mode.ENABLED for node in set(nodes))

    def describe(self):
        with self.store._connect() as con:
            return [asdict(self.desired(node, con)) for node in self.catalog.graph.nodes]
