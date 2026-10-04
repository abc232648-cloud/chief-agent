from pathlib import Path

from notifications.channels import LocalInboxChannel
from notifications.models import ActionItem, PeriodSummary
from notifications.center import NotificationCenter


def test_action_notification_is_written(tmp_path: Path):
    channel = LocalInboxChannel(tmp_path)
    center = NotificationCenter([channel])
    center.action_required(
        "Action required",
        "A legitimate new platform needs registration.",
        [ActionItem("Register platform", "Create an account.", "The worker cannot apply until registration is complete.")],
    )
    files = list(tmp_path.glob("action_*.txt"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "Register platform" in text
    assert "Why:" in text


def test_period_summary_is_written(tmp_path: Path):
    channel = LocalInboxChannel(tmp_path)
    channel.send_summary(PeriodSummary(period="weekly", generated_at="now"))
    text = (tmp_path / "summary_weekly.txt").read_text()
    assert "WEEKLY CHIEF AGENT SUMMARY" in text
    assert "Actions for you" in text


def test_action_items_sorted_by_priority(tmp_path: Path):
    channel = LocalInboxChannel(tmp_path)
    center = NotificationCenter([channel])
    center.action_required(
        "Actions",
        "Do these things.",
        [
            ActionItem("Low", "x", "x", priority=1),
            ActionItem("High", "x", "x", priority=99),
        ],
    )
    text = next(tmp_path.glob("action_*.txt")).read_text()
    assert text.index("High") < text.index("Low")

def test_desktop_channel_cross_platform_methods_exist():
    from notifications.desktop import DesktopNotificationChannel
    channel = DesktopNotificationChannel()
    assert callable(channel._windows)
    assert callable(channel._linux)
