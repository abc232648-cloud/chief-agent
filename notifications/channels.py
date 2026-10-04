from __future__ import annotations

from pathlib import Path
from typing import Protocol
from .models import Notification, PeriodSummary
from .render import render_action_notification_txt, render_summary_html, render_summary_txt


class NotificationChannel(Protocol):
    def send_notification(self, notification: Notification) -> None: ...
    def send_summary(self, summary: PeriodSummary) -> None: ...


class LocalInboxChannel:
    """Zero-cost local notification inbox. Safe default; no network required."""

    def __init__(self, outbox_dir: str | Path = "notifications/outbox"):
        self.outbox_dir = Path(outbox_dir)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def send_notification(self, notification: Notification) -> None:
        stamp = notification.created_at.replace(":", "-").replace("+", "_")
        path = self.outbox_dir / f"action_{stamp}.txt"
        path.write_text(render_action_notification_txt(notification), encoding="utf-8")

    def send_summary(self, summary: PeriodSummary) -> None:
        path = self.outbox_dir / f"summary_{summary.period}.txt"
        path.write_text(render_summary_txt(summary), encoding="utf-8")


class SmtpEmailChannel:
    """Optional email channel using the user's own SMTP account; no paid email API is required."""

    def __init__(self, host: str, port: int, username: str, password: str, sender: str, recipient: str, use_tls: bool = True):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.sender, self.recipient, self.use_tls = sender, recipient, use_tls

    def _send(self, subject: str, text: str, html: str | None = None) -> None:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, self.sender, self.recipient
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            if self.use_tls:
                smtp.starttls()
            smtp.login(self.username, self.password)
            smtp.send_message(msg)

    def send_notification(self, notification: Notification) -> None:
        self._send(notification.title, render_action_notification_txt(notification))

    def send_summary(self, summary: PeriodSummary) -> None:
        self._send(f"Chief Agent — {summary.period.title()} Summary", render_summary_txt(summary), render_summary_html(summary))


class ConfiguredSmtpEmailChannel(SmtpEmailChannel):
    """Build the existing SMTP channel from provider-neutral environment settings."""

    @classmethod
    def from_environment(cls, env=None):
        from .email_config import load_email_settings
        import os
        from control.settings import environment
        e = environment(env)
        settings = load_email_settings(e)
        if not settings.configured:
            raise ValueError("Email recipient/account is not configured")
        from private_secrets.service import resolve_configured
        password = resolve_configured(e,'SMTP_PASSWORD',consumer='system.email',domain='system')
        if not password:
            raise ValueError("SMTP_PASSWORD is not configured")
        return cls(settings.host, settings.port, settings.username, password,
                   settings.sender, settings.recipient, settings.use_tls)
