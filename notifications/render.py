from __future__ import annotations

from html import escape
from .models import Notification, PeriodSummary


def render_action_notification_txt(notification: Notification) -> str:
    lines = [notification.title, "=" * len(notification.title), "", notification.body]
    if notification.action_items:
        lines += ["", "ACTION REQUIRED"]
        for i, item in enumerate(notification.action_items, 1):
            lines.append(f"{i}. {item.title}")
            lines.append(f"   What: {item.description}")
            lines.append(f"   Why: {item.reason}")
            if item.source:
                lines.append(f"   Source: {item.source}")
            if item.deadline:
                lines.append(f"   Deadline: {item.deadline}")
    return "\n".join(lines) + "\n"


def render_summary_txt(summary: PeriodSummary) -> str:
    lines = [f"{summary.period.upper()} CHIEF AGENT SUMMARY", "=" * 28, "", f"Generated: {summary.generated_at}"]
    lines += ["", "1. Completed"]
    lines += [f"- {x}" for x in summary.completed] or ["- None"]
    lines += ["", "2. Actions for you"]
    if summary.pending_actions:
        for i, item in enumerate(summary.pending_actions, 1):
            lines.append(f"{i}. {item.title}")
            lines.append(f"   What: {item.description}")
            lines.append(f"   Why: {item.reason}")
            if item.source:
                lines.append(f"   Source: {item.source}")
            if item.deadline:
                lines.append(f"   Deadline: {item.deadline}")
    else:
        lines.append("- None")
    lines += ["", "3. Blocked / unresolved"]
    lines += [f"- {x}" for x in summary.blocked_items] or ["- None"]
    lines += ["", "4. Notable"]
    lines += [f"- {x}" for x in summary.notable_items] or ["- None"]
    return "\n".join(lines) + "\n"


def render_summary_html(summary: PeriodSummary) -> str:
    def lis(items: list[str]) -> str:
        return "".join(f"<li>{escape(x)}</li>" for x in items) or "<li>None</li>"

    action_html = "<li>None</li>"
    if summary.pending_actions:
        action_html = "".join(
            f"<li><strong>{escape(a.title)}</strong><br>{escape(a.description)}<br>Why: {escape(a.reason)}"
            + (f"<br>Source: {escape(a.source)}" if a.source else "")
            + (f"<br>Deadline: {escape(a.deadline)}" if a.deadline else "")
            + "</li>"
            for a in summary.pending_actions
        )
    return f"""<html><body><h1>{escape(summary.period.title())} Chief Agent Summary</h1>
<p>Generated: {escape(summary.generated_at)}</p>
<h2>1. Completed</h2><ul>{lis(list(summary.completed))}</ul>
<h2>2. Actions for you</h2><ol>{action_html}</ol>
<h2>3. Blocked / unresolved</h2><ul>{lis(list(summary.blocked_items))}</ul>
<h2>4. Notable</h2><ul>{lis(list(summary.notable_items))}</ul>
</body></html>"""
