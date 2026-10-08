from __future__ import annotations

from .ledger_adapter import JobCommandProcessor
from notifications.job_attention import record_job_attention


class JobPushCommandProcessor(JobCommandProcessor):
    """Job processor with best-effort PWA attention delivery.

    Notification delivery is observational only: it must never change command
    execution, policy decisions, approvals, or recorded worker outcomes.
    """

    def _attention(self, title: str, body: str, severity: str, *, related_page: str = 'actions') -> None:
        try:
            record_job_attention(
                self.store,
                title,
                body,
                severity,
                related_page=related_page,
            )
        except Exception:
            # Existing local/email/system notification paths and command state
            # remain authoritative even if Web Push state/configuration is broken.
            pass

    def process_command(self, command_id, instruction):
        try:
            result = super().process_command(command_id, instruction)
        except Exception:
            self._attention(
                'Job Agent stopped on an error',
                f'Command #{command_id} failed. Review the recorded Job Agent activity before retrying.',
                'URGENT',
                related_page='applicationArchive',
            )
            raise

        approvals = [item for item in result.get('approvals', ()) if item is not None]
        blocked = [
            item for item in result.get('results', ())
            if isinstance(item, dict) and item.get('status') == 'BLOCKED'
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
