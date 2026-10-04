from __future__ import annotations

from .channels import NotificationChannel
from .models import ActionItem, Notification, NotificationSeverity, PeriodSummary


class NotificationCenter:
    def __init__(self, channels: list[NotificationChannel]):
        self.channels = channels

    def action_required(self, title: str, body: str, items: list[ActionItem], urgent: bool = False) -> Notification:
        notification = Notification(
            title=title,
            body=body,
            severity=NotificationSeverity.URGENT if urgent else NotificationSeverity.ACTION_REQUIRED,
            action_items=tuple(sorted(items, key=lambda x: x.priority, reverse=True)),
        )
        for channel in self.channels:
            try:
                channel.send_notification(notification)
            except Exception:
                # One delivery channel must not prevent the remaining channels
                # (especially the local zero-cost inbox) from receiving the alert.
                continue
        return notification

    def summary(self, summary: PeriodSummary) -> None:
        for channel in self.channels:
            try:
                channel.send_summary(summary)
            except Exception:
                continue
