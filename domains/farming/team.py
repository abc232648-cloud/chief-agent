"""Role-scoped Farm Staff directory projection for the Staff PWA.

This view does not infer attendance or physical presence. Supervisor membership is
derived only from authoritative task supervision relationships. General Managers
may see enabled Farm-scoped Staff identities. Workers see only themselves.
"""
import json

from operations.time_integrity import aware_utc, utc_now
from . import setup, tasks


def _identity_rows(con):
    return con.execute(
        'SELECT id,username,role,domains,enabled FROM human_identities ORDER BY username,id'
    ).fetchall()


def _farm_staff(con):
    result = {}
    for row in _identity_rows(con):
        domains = set(json.loads(row['domains']))
        if not row['enabled'] or not ({'farming', '*'} & domains):
            continue
        if row['role'] in {'Owner', 'Administrator'}:
            continue
        result[row['id']] = {
            'user_id': row['id'],
            'username': row['username'],
            'chief_role': row['role'],
            'farm_role': tasks._farm_role_for_human(con, row['id'], row['role']),
            'enabled': True,
        }
    return result


def _task_stats(all_tasks, user_id, *, supervisor_id=None):
    assigned = [task for task in all_tasks if task['assigned_to'] == user_id]
    supervised = [task for task in all_tasks if supervisor_id and task['supervisor_id'] == supervisor_id and task['assigned_to'] == user_id]
    relevant = supervised if supervisor_id else assigned
    now = utc_now()
    active = [task for task in relevant if task['status'] not in tasks.TERMINAL_STATES]
    return {
        'assigned_task_count': len(relevant),
        'active_task_count': len(active),
        'awaiting_verification_count': sum(task['status'] == 'AWAITING_VERIFICATION' for task in relevant),
        'needs_correction_count': sum(task['status'] == 'NEEDS_CORRECTION' for task in relevant),
        'escalated_task_count': sum(task['status'] == 'ESCALATED' for task in relevant),
        'overdue_task_count': sum(
            task['status'] not in tasks.TERMINAL_STATES and aware_utc(task['due_at']) < now
            for task in relevant
        ),
    }


def overview(store, principal):
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'read')
        role = setup.role(con, principal)
        directory = _farm_staff(con)
        all_tasks = list(tasks._snapshots(tasks.rows(con)).values())

        if role in {'OWNER', 'GENERAL_MANAGER'}:
            member_ids = list(directory)
            scope = 'FARM_DIRECTORY'
            source = 'Enabled Farm-scoped Staff identities.'
        elif role == 'SUPERVISOR':
            member_ids = sorted({
                task['assigned_to'] for task in all_tasks
                if task['supervisor_id'] == principal.id and task['assigned_to'] in directory
            })
            scope = 'SUPERVISED_TASK_ASSIGNMENTS'
            source = 'Workers appear only when an authoritative Chief task names this supervisor.'
        else:
            member_ids = [principal.id] if principal.id in directory else []
            scope = 'SELF'
            source = 'Worker self context only.'

        members = []
        for user_id in member_ids:
            member = directory[user_id]
            supervisor_scope = principal.id if role == 'SUPERVISOR' else None
            members.append({
                **member,
                **_task_stats(all_tasks, user_id, supervisor_id=supervisor_scope),
                'attendance_status': 'NOT_CONNECTED',
            })

        if role == 'SUPERVISOR':
            verification_ids = [
                task['task_id'] for task in all_tasks
                if task['supervisor_id'] == principal.id and task['status'] == 'AWAITING_VERIFICATION'
            ]
        elif role in {'OWNER', 'GENERAL_MANAGER'}:
            verification_ids = [task['task_id'] for task in all_tasks if task['status'] == 'AWAITING_VERIFICATION']
        else:
            verification_ids = []

        return {
            'farm_role': role,
            'scope': scope,
            'scope_source': source,
            'members': members,
            'member_count': len(members),
            'verification_task_ids': verification_ids,
            'verification_count': len(verification_ids),
            'attendance_available': False,
            'notice': (
                'Team membership is derived from Chief identity and task-assignment records. '
                'No attendance, physical presence, or shift state is inferred by this endpoint.'
            ),
        }
