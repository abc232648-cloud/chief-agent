from __future__ import annotations
import os
import shutil
import subprocess
from .models import Notification, PeriodSummary
from .render import render_action_notification_txt, render_summary_txt


class DesktopNotificationChannel:
    """Cross-platform best-effort desktop notifications with no Python dependency."""
    def __init__(self, linux_command: str = "notify-send"):
        self.linux_command = linux_command

    def _linux(self, title: str, body: str) -> bool:
        if not shutil.which(self.linux_command):
            return False
        try:
            env=os.environ.copy()
            if not env.get('DBUS_SESSION_BUS_ADDRESS') and hasattr(os,'getuid'):
                from pathlib import Path
                bus=Path('/run/user')/str(os.getuid())/'bus'
                if bus.exists(): env['DBUS_SESSION_BUS_ADDRESS']='unix:path='+str(bus)
            result=subprocess.run([self.linux_command, title, body], check=False, timeout=5, env=env, capture_output=True)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _windows(self, title: str, body: str) -> bool:
        # Windows 10/11: use the built-in Windows.UI.Notifications API through
        # PowerShell. No module, package manager, or third-party service required.
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return False
        safe_title = title.replace("'", "''")[:200]
        safe_body = body.replace("'", "''")[:1000]
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
            "$xml = [Windows.Data.Xml.Dom.XmlDocument]::new(); "
            f"$xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{safe_title}</text><text>{safe_body}</text></binding></visual></toast>'); "
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Job Worker').Show($toast)"
        )
        try:
            result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                                    check=False, timeout=8, capture_output=True, text=True)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _send(self, title: str, body: str) -> bool:
        if os.name == "nt":
            return self._windows(title, body) or self._linux(title, body)
        return self._linux(title, body)

    def send_notification(self, notification: Notification):
        return self._send(notification.title, render_action_notification_txt(notification)[:1000])

    def send_summary(self, summary: PeriodSummary):
        return self._send(f"Chief Agent — {summary.period.title()} Summary", render_summary_txt(summary)[:1000])
