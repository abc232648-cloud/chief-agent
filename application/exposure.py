"""Server-owned bindings for existing module routes; browser fields grant nothing."""


def for_store(store):
    # Retain one catalog per store so drift cannot silently replace registrations.
    if not hasattr(store, '_module_exposure'):
        from .control_services import compose_control_services
        store._module_exposure = compose_control_services(store).exposure
    if store._module_exposure is None:
        raise PermissionError('Module exposure is unavailable.')
    return store._module_exposure


def guard(store, service, principal, method, path, body):
    from .auth_routes import requirement
    parts = path.strip('/').split('/')
    kind = None
    if path.startswith('/api/farm/'):
        domain = 'farming'
        kind = 'owner' if principal.role in {'Owner', 'Administrator'} else 'staff'
    elif path in {'/api/ui/job-feed', '/api/ui/job-state', '/api/ui/approval-access'}:
        domain = 'jobs'
        if path == '/api/ui/job-feed':
            kind = 'companion'
    elif len(parts) == 4 and parts[:2] == ['api', 'ui'] and parts[2] in {'evidence', 'ledger', 'runbooks'}:
        domain = parts[3]
        kind = 'owner'
    elif path.startswith('/api/agent-controls/'):
        return  # Existing control/auth authority must remain usable for recovery.
    else:
        _, domain, _, _ = requirement(method, path, body, store)
    if domain is not None:
        for_store(store).require(domain, service, principal, kind=kind)
