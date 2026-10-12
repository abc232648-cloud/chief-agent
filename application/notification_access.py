"""Application-owned human and Farm assignment checks for notification transport."""
import hashlib
import json
from identity.contracts import Principal
from identity.service import IdentityService
from notifications.bus import NotificationContext


def context(store, human_id, session_id, surface, *, expired=False):
    if surface not in {'owner', 'staff'}:
        raise PermissionError('Unknown notification workspace.')
    service = IdentityService(store); service.require_ready()
    if not expired:
        service.refresh(Principal(human_id, session_id, '', (), ''))
    with store._connect() as con:
        row = con.execute('SELECT s.human_id,s.revoked,u.enabled,u.role,u.domains FROM human_sessions s JOIN human_identities u ON u.id=s.human_id WHERE s.id=?', (session_id,)).fetchone()
        if not row or row['revoked'] or not row['enabled'] or row['human_id'] != human_id:
            raise PermissionError('Notification permission is revoked.')
        domains = tuple(json.loads(row['domains'])); role = row['role']
        if 'work.read' not in service.roles.get(role, ()):
            raise PermissionError('Notification access is unavailable.')
        farm_role = None; assignment_revision = None
        if surface == 'owner':
            if role not in {'Owner', 'Administrator'}:
                raise PermissionError('Owner workspace access required.')
        else:
            if role not in {'Manager', 'Worker'} or not {'farming', '*'} & set(domains):
                raise PermissionError('Staff Farm access required.')
            from domains.farming import setup, tasks
            assignments = [r['payload'] for r in setup.rows(con) if r['payload']['operation'] == 'assign_role' and r['payload']['human_id'] == human_id]
            assignment_revision = assignments[-1]['event_id'] if assignments else None
            farm_role = {'GENERAL_MANAGER': 'Manager', 'SUPERVISOR': 'Supervisor', 'WORKER': 'Worker'}.get(tasks._farm_role_for_human(con, human_id, role))
            if farm_role is None: raise PermissionError('Staff assignment unavailable.')
    exposure = getattr(store, '_module_exposure', None)
    if exposure is None: raise PermissionError('Module exposure unavailable.')
    if surface == 'staff':
        from .farm_release import require_farm_runtime
        require_farm_runtime()
        exposure.installed_surface('farming', kind='staff')
    ctx = NotificationContext(surface, human_id, role, domains, farm_role)
    stamp = hashlib.sha256(json.dumps([role, sorted(domains), farm_role, assignment_revision]).encode()).hexdigest()
    return ctx, stamp


def visible(store, event, ctx):
    if event.source_agent == 'farming':
        from .farm_release import require_farm_runtime
        require_farm_runtime()
    if event.source_agent != 'system':
        store._module_exposure.installed_surface(event.source_agent, kind=ctx.surface)
    if ctx.surface != 'staff': return True
    if event.source_agent != 'farming': return False
    from domains.farming import tasks, incidents
    modules = {'farm-task': tasks, 'farm-incident': incidents}
    module = modules.get(event.object_type)
    if module is None: return False  # No financial or unscoped Staff payloads.
    with store._connect() as con:
        item = module._snapshots(module.rows(con)).get(event.object_id)
    role = {'Manager': 'GENERAL_MANAGER', 'Supervisor': 'SUPERVISOR', 'Worker': 'WORKER'}[ctx.staff_role]
    return bool(item and module._can_view(role, ctx.principal_id, item))
