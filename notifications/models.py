from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class NotificationSeverity(str, Enum):
    INFO = "INFO"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    URGENT = "URGENT"


@dataclass(frozen=True)
class ActionItem:
    title: str
    description: str
    reason: str
    source: str | None = None
    job_id: str | None = None
    deadline: str | None = None
    priority: int = 50


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    severity: NotificationSeverity = NotificationSeverity.INFO
    action_items: tuple[ActionItem, ...] = ()
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class PeriodSummary:
    period: str
    generated_at: str
    completed: tuple[str, ...] = ()
    pending_actions: tuple[ActionItem, ...] = ()
    blocked_items: tuple[str, ...] = ()
    notable_items: tuple[str, ...] = ()
