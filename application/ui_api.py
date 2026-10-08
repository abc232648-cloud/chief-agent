"""Authenticated presentation boundary; never grants agent or action authority."""
from .composition import default_registry
from control.agents import AgentControls

SYSTEM_PAGES=('components','capabilities','models','policies','runtime','integrations','devices','updates','settings')
DOMAIN_PAGES=('evidence','ledger','runbooks')

def permitted(service,principal,permission,domain=None):
    try:service.authorize(principal,permission,domain,sensitive=False);return True
    except PermissionError:return False

def context(service,principal):
    registry=default_registry()
    domains=[]
    for definition in registry.describe():
        domain=definition['id']
        if permitted(service,principal,'work.read',domain):
            permissions={p:permitted(service,principal,p,domain) for p in ('work.read','work.request','work.manage','work.approve','work.delete','controls.manage','safety.pause')}
            domains.append({**definition,'permissions':permissions})
    global_permissions={p:permitted(service,principal,p) for p in ('audit.read','installation.manage','identity.manage')}
    jobs=any(d['id']=='jobs' for d in domains)
    pages=['overview','domains','agentDetail'] if principal.role in {'Owner','Administrator'} else []
    if jobs:pages+=['actions','scheduler']
    for d in domains:pages += [p[0] for p in d.get('pages',())]
    if domains and principal.role in {'Owner','Administrator'}:pages+=list(DOMAIN_PAGES)
    if global_permissions['audit.read']:pages+=['notifications','reports','audit','health']
    if global_permissions['installation.manage']:pages+=list(SYSTEM_PAGES)+['ai']
    try:management=service.user_management(principal)
    except PermissionError:management={'roles':[],'domains':[]}
    if principal.role not in {'Owner','Administrator'} and 'farming' in management['domains']:
        from .auth_routes import farm_user_management_guard
        try:farm_user_management_guard(service.store,principal,['farming'])
        except PermissionError:
            management={**management,'domains':[d for d in management['domains'] if d!='farming']}
            if not management['domains']:management={**management,'roles':[]}
    if management['roles']:pages+=['users']
    return {'role':principal.role,'domains':domains,'pages':sorted(set(pages)),'global_permissions':global_permissions,'user_management':management,
            'authority_note':'Visibility is advisory. Every API operation checks current server-side authority.'}

def overview(store,ctx):
    allowed={d['id'] for d in ctx['domains']}
    data=AgentControls(store,default_registry()).overview()
    agents=[a for a in data['agents'] if a['id'] in allowed]
    # Aggregate counts, worker messages and notifications are not shared across scopes.
    counts={'actions':0,'notifications':0}
    if 'jobs' in allowed:
        from .auth_routes import action_domain
        counts['actions']=sum(action_domain(store,a['id'])=='jobs' for a in store.actions())
    if ctx['global_permissions']['audit.read']:counts['notifications']=store.counts()['notifications']
    return {'agents':agents,'counts':counts,'worker_connected':data['worker_connected'],
            'service_started_at':data['service_started_at'] if ctx['global_permissions']['audit.read'] else None}

def job_state(store, *, include_notifications=False):
    from .auth_routes import action_domain
    with store._connect() as con:
        commands=[dict(r) for r in con.execute("SELECT c.* FROM commands c LEFT JOIN domain_requests d ON d.command_id=c.id WHERE COALESCE(d.domain,'jobs')='jobs' ORDER BY c.id DESC LIMIT 10")]
    counts={'jobs':len(store.jobs()),'applications':len(store.applications()),
            'actions':sum(action_domain(store,a['id'])=='jobs' for a in store.actions()),
            'notifications':store.counts()['notifications'] if include_notifications else 0}
    return {'counts':counts,'commands':commands,'worker':{'status':'UNKNOWN','message':'Use System Health for shared worker observations; domain activity appears in your workspace.','updated_at':None}}

def dispatch(handler,store,service,principal,path,method):
    if not path.startswith('/api/ui/'):return False
    if method not in {'GET','HEAD'}:
        handler.json({'status':'READ_ONLY','reason':'This presentation endpoint does not perform actions.'},405);return True
    if path=='/api/ui/context':handler.json(context(service,principal));return True
    if path=='/api/ui/overview':
        if principal.role not in {'Owner','Administrator'}:raise PermissionError('Chief overview is restricted to administration roles.')
        handler.json(overview(store,context(service,principal)));return True
    if path=='/api/ui/job-state':
        service.authorize(principal,'work.read','jobs',sensitive=False)
        handler.json(job_state(store,include_notifications=permitted(service,principal,'audit.read')));return True
    if path=='/api/ui/job-feed':
        service.authorize(principal,'work.read','jobs',sensitive=False)
        from .job_pwa_feed import recent_job_feed
        handler.json(recent_job_feed(store));return True
    if path=='/api/ui/approval-access':
        from .auth_routes import action_domain
        service.authorize(principal,'work.read','jobs',sensitive=False)
        access={}
        for action in store.actions():
            if action_domain(store,action['id'])!='jobs':continue
            try:
                service.authorize(principal,'work.approve','jobs','action:'+str(action['id']),sensitive=False)
                access[str(action['id'])]=True
            except PermissionError:access[str(action['id'])]=False
        handler.json(access);return True
    parts=path.strip('/').split('/')
    if len(parts)==4 and parts[2] in DOMAIN_PAGES:
        if principal.role not in {'Owner','Administrator'}:raise PermissionError('Foundation records are not part of this work interface.')
        domain=parts[3]
        # Registered scope only; no inferred cross-domain grants from shared mechanics.
        if domain not in {d['id'] for d in default_registry().describe()}:raise PermissionError('Domain unavailable.')
        service.authorize(principal,'work.read',domain,sensitive=False)
        from .ui_views import domain_view
        data=domain_view(store,parts[2],domain)
        service.event(principal,'UI_READ',domain,parts[2],'AUTHORIZED')
        handler.json(data);return True
    if len(parts)==3 and parts[2] in SYSTEM_PAGES:
        service.authorize(principal,'installation.manage',sensitive=False)
        from .ui_views import system_view
        data=system_view(store,parts[2])
        service.event(principal,'UI_READ',None,parts[2],'AUTHORIZED')
        handler.json(data);return True
    handler.json({'status':'NOT_FOUND'},404);return True
