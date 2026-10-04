"""Deliver actionable website recommendations through existing channels."""
import os
from pathlib import Path
from .channels import LocalInboxChannel, ConfiguredSmtpEmailChannel
from .desktop import DesktopNotificationChannel
from .models import Notification, NotificationSeverity


def send_site_alert(store, domain, reason):
    body = f'{domain}: {reason}\nOpen Sources to review, sign in, or dismiss. Existing access restrictions remain in effect.'
    store.add_notification('Website needs your review', body, 'ACTION_REQUIRED',domain='jobs',related_page='sources')
    notification = Notification('Website needs your review', body, NotificationSeverity.ACTION_REQUIRED)
    channels = [('local inbox', LocalInboxChannel(store.path.resolve().parent / 'site-notifications')),
                ('VM desktop', DesktopNotificationChannel())]
    try:
        channels.append(('email', ConfiguredSmtpEmailChannel.from_environment()))
    except ValueError:
        pass
    from database.store_extensions import add_audit
    for name, channel in channels:
        try:
            result = channel.send_notification(notification)
            status = 'UNAVAILABLE' if result is False else 'SENT'
        except Exception:
            status = 'FAILED'
        add_audit(store, 'notification', 'Website alert: '+name, status=status, data={'domain': domain})
