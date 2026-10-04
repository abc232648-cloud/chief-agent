"""Pure regression planning; this is not an updater or a release certificate."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .contracts import Node


@dataclass(frozen=True)
class RegressionPlan:
    id: str
    candidate: str
    changes: tuple[Node, ...]
    affected: tuple[Node, ...]
    required_consumers: tuple[str, ...]
    required_tests: tuple[str, ...]
    optional_tests: tuple[str, ...]

    def validate_test_files(self, root):
        root = Path(root).resolve()
        for name in self.required_tests + self.optional_tests:
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('Missing or out-of-project regression test: ' + name)

    def require_passes(self, plan_id, outcomes):
        """Reject wrong-candidate, missing, skipped, blocked or failed evidence.

        Caller must supply fresh results; this helper cannot attest their origin.
        It checks declared coverage only, not deployment acceptance.
        """
        if plan_id != self.id:
            raise ValueError('Evidence belongs to a different candidate or regression plan.')
        incomplete = [name for name in self.required_tests if outcomes.get(name) != 'PASSED']
        if incomplete:
            raise ValueError('Required consumer regression not passed: ' + ', '.join(incomplete))


def regression_plan(registry, changes, *, candidate):
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError('A candidate identity is required; use the tested source digest.')
    changes = tuple(sorted(set(changes)))
    if not changes:
        raise ValueError('Declare at least one changed capability or component.')
    affected = registry.graph.impact(changes)
    required, optional = set(), set()
    for node in affected:
        item = registry.capabilities[node.id] if node.kind == 'capability' else registry.components[node.id]
        required.update(item.tests)
    for capability in registry.capabilities.values():
        for consumer in capability.consumers:
            if Node('capability', capability.id) in affected or Node('component', consumer.component) in affected:
                (required if consumer.required else optional).update(consumer.tests)
    if not required:
        raise ValueError('No required regression coverage declared for these changes.')
    optional.difference_update(required)
    payload = {'candidate': candidate, 'catalog': registry.describe(),
               'changes': [node.key for node in changes], 'tests': sorted(required), 'optional': sorted(optional)}
    identity = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return RegressionPlan(identity, candidate, changes, affected, registry.required_consumers(changes),
                          tuple(sorted(required)), tuple(sorted(optional)))
