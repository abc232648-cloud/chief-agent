from __future__ import annotations

from .ledger_adapter import JobCommandProcessor


class JobAttentionCommandProcessor(JobCommandProcessor):
    """Emit sanitized Job attention records without coupling execution to delivery.

    Web Push, Owner/Staff notification delivery, and future channels consume the
    notification ledger independently. Notification failures must never change Job
    policy decisions, approvals, execution results, or replay semantics.
    """

    def _attention(self, title: str, body: str, severity: str, *, related_page: str = 'actions') -> None:
        try:
            self.store.add_notification(
                str(title)[:240],
                str(body)[:1000],
                severity,
                domain='jobs',
                related_page=related_page,
            )
        except Exception:
            # The Job result/ledger remains authoritative if notification storage
            # becomes unavailable. Never turn an observation failure into a retry.
            pass

    def process_command(self, command_id, instruction):
        try:
            result = super().process_command(command_id, instruction)
        except Exception:
            self._attention(
                'Job Agent stopped on an error',
                f'Command #{command_id} failed. Review recorded Job Agent activity before retrying.',
                'URGENT',
                related_page='applicationArchive',
            )
            raise

        approvals = [item for item in result.get('approvals', ()) if item is not None]
        blocked = [
            item for item in result.get('results', ())
            if isinstance(item, dict) and str(item.get('status', '')).upper() == 'BLOCKED'
        ]

        if approvals:
            count = len(approvals)
            self._attention(
                'Job Agent approval required',
                f'Command #{command_id} is waiting for {count} user approval' + ('s.' if count != 1 else '.'),
                'ACTION_REQUIRED',
                related_page='actions',
            )

        if blocked:
            count = len(blocked)
            self._attention(
                'Job Agent STOP',
                f'Command #{command_id} hit {count} blocked action' + ('s.' if count != 1 else '.') + ' No blocked action was executed.',
                'URGENT',
                related_page='actions',
            )

        return result
