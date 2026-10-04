"""Desired intent contracts, deliberately independent of observed health."""
from dataclasses import dataclass
from capabilities.contracts import Mode, Node


@dataclass(frozen=True)
class DesiredState:
    node: Node
    mode: Mode
    revision: int
    authority: str
    supported_modes: tuple[Mode, ...]
    legacy_controls: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class TransitionPreview:
    node: Node
    mode: Mode
    token: str
    affected: tuple[Node, ...]
    requires_confirmation: bool


class StaleTransition(ValueError):
    pass
