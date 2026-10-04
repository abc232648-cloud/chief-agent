"""Server-side human authorization of the existing private dashboard surface."""
from dataclasses import asdict
from http.cookies import SimpleCookie
import hmac,json,os,ipaddress
from urllib.parse import urlparse
from identity.service import IdentityService
from identity.contracts import AuthenticationRequired,ReauthenticationRequired,SetupRequired


def private_bind(host):
    if host=='localhost':return host
    address=ipaddress.ip_address(host)
    if address.is_unspecified or address.is_multicast or not (address.is_loopback or address.is_private):raise ValueError('F requires an explicit loopback/private bind, not public or wildcard exposure.')
    return host


def model_registry(store):
    from model_registry.registry import ModelRegistry
    from model_registry.contracts import Model,Provider,Cost
    from domains.farming.live_ai import configuration
    farm = configuration(store)
    extra = (Model('farming.qwen','groq',farm['model'] if farm and farm.get('model') else 'qwen/unconfigured',Cost.FREE if farm and farm.get('model') else Cost.UNKNOWN),)
    return ModelRegistry((Provider('groq'),Provider('mistral')),
        (Model('jobs.qwen','groq',os.getenv('QWEN_MODEL','qwen/qwen3.6-27b'),Cost.FREE),Model('jobs.mistral','mistral',os.getenv('MISTRAL_MODEL','mistral-small-latest'),Cost.LEGACY_UNRESOLVED))+extra,
        store=store,legacy_pair=('jobs.qwen','jobs.mistral'))


def action_domain(store,identity):
    row=store.get_action(int(identity))
    if row:
        payload=json.loads(row.get('payload_json') or '{}')
        if 'command_id' in payload:
            with store._connect() as con:
                request=con.execute('SELECT domain FROM domain_requests WHERE command_id=?',(payload['command_id'],)).fetchone()
                if request:return request[0]
    return 'jobs' # Existing action table is Job-owned; domain requests override legacy routing.


def requirement(method,path,body,store):
    read=method in {'GET','HEAD'};parts=path.strip('/').split('/')
    if path.startswith('/api/domains/'):
        return ('work.read' if read else 'work.request',parts[2],path,False)
    if path.startswith('/api/actions/'):
        identity=parts[-1]
        return ('work.approve',action_domain(store,identity),'action:'+identity,True)
    if path.startswith('/api/agent-controls/'):
        pause=body=={'running':False}
        return ('safety.pause' if pause else 'controls.manage',parts[-1],path,not pause)
    if path.startswith('/api/applications/') and path.endswith('/retry'):
        return ('work.approve','jobs','application:'+parts[2],True)
    if path.startswith('/api/facts/') and method=='POST' and body.get('status')=='USER_CONFIRMED':
        return ('work.approve','jobs','fact:'+parts[2],True)
    if path=='/api/general-schedules' or path.startswith('/api/general-schedules/'):
        return ('installation.manage',None,None,False)
    if path=='/api/integrations/n8n/check':
        return ('installation.manage',None,None,False)
    if path.startswith(('/api/general-schedules','/api/integrations/','/api/settings/','/api/model-controls','/api/model-policy','/api/model-setup','/api/component-controls')):
        return ('installation.manage',None,None,not read)
    if path in {'/api/audit','/api/audit/generate','/api/reports','/api/chief','/api/state','/api/domains','/api/system-health','/api/summary-settings','/api/notification-preferences','/api/notifications'} or path.startswith(('/api/reports/','/api/notifications/')):
        return ('audit.read' if read else 'installation.manage',None,None,not read)
    if parts[:2]==['api','auth']:
        return ('identity.manage',None,None,True)
    if len(parts)>1 and parts[1] in {'actions','jobs','applications','sources','site-access','cvs','profiles','facts','scheduler','schedules','session-control','command'}:
        permission='work.read' if read else 'work.delete' if method=='DELETE' or body.get('remove') is True else 'work.request' if parts[1]=='command' else 'work.manage'
        if parts[1]=='site-access' and not read:permission='installation.manage'
        return (permission,'jobs',path,permission in {'work.delete','installation.manage'})
    return ('UNSUPPORTED',None,None,True)


def intercept(handler,store):
    path=urlparse(handler.path).path;method=handler.command
    if path=='/farm-sw.js':handler.serve_static(handler_root(handler)/'static/farm-sw.js','application/javascript; charset=utf-8');return True
    if path=='/work-login':handler.serve_static(handler_root(handler)/'ui/work-login.html','text/html; charset=utf-8',csp=True);return True
    if path=='/login':handler.serve_static(handler_root(handler)/'static/login.html','text/html; charset=utf-8',csp=True);return True
    if path.startswith('/static/'):return False
    service=IdentityService(store,target_guard=lambda con,actor,domains:farm_user_management_guard(store,actor,domains,con=con))
    try:
        cookie=SimpleCookie();cookie.load(handler.headers.get('Cookie',''))
        raw=cookie['chief_session'].value if 'chief_session' in cookie else ''
        if path=='/api/auth/login' and method=='POST':
            if handler.headers.get('Origin') != handler.scheme+'://'+handler.headers.get('Host',''):raise PermissionError('Same-origin sign-in required.')
            body=handler.read_body();raw,principal=service.login(body.get('username'),body.get('password'))
            secure='; Secure' if handler.secure_transport else ''
            handler.json({'status':'SIGNED_IN','csrf':service.csrf(raw)},extra_headers=(('Set-Cookie','chief_session='+raw+'; HttpOnly; SameSite=Strict; Path=/'+secure),));return True
        try:principal=service.authenticate(raw)
        except AuthenticationRequired:
            if path in {'/', '/work'}:
                handler.send_response(303);handler.send_header('Location','/work-login' if path=='/work' else '/login');handler.send_header('Content-Length','0');handler.end_headers();return True
            raise
        handler.human=principal
        if path == '/' and principal.role not in {'Owner', 'Administrator'}:
            handler.send_response(303);handler.send_header('Location','/work');handler.send_header('Cache-Control','no-store');handler.send_header('Content-Length','0');handler.end_headers();return True
        if path == '/work' and method in {'GET','HEAD'}:
            handler.serve_static(handler_root(handler)/'ui/work.html','text/html; charset=utf-8',csp=True);return True
        if path in {'/chief', '/dashboard.html'} and principal.role not in {'Owner','Administrator'}:
            raise PermissionError('Chief administration is not available to this role.')
        if path=='/api/auth/session' and method in {'GET','HEAD'}:
            handler.json({'principal':asdict(principal),'csrf':service.csrf(raw),'instance_mode':os.environ.get('CHIEF_INSTANCE_MODE','preview').lower()});return True
        if method not in {'GET','HEAD'}:
            if handler.headers.get('Origin') != handler.scheme+'://'+handler.headers.get('Host',''):raise PermissionError('Same-origin control request required.')
            if not hmac.compare_digest(handler.headers.get('X-Chief-CSRF',''),service.csrf(raw)):raise PermissionError('CSRF validation failed.')
        body=handler.read_body() if method=='POST' else {}
        if os.environ.get('CHIEF_INSTANCE_MODE','preview').lower()=='preview' and method not in {'GET','HEAD'} and (path=='/api/settings/email/test' or (path.startswith('/api/applications/') and path.endswith('/retry'))):
            raise PermissionError('Preview cannot send email or perform external submission actions.')
        if path=='/api/auth/activity' and method=='POST':
            if body!={}:raise ValueError('Activity uses server time; no client fields are accepted.')
            service.activity(raw);handler.json({'status':'ACTIVITY_RECORDED'});return True
        from .farm_api import dispatch as farm_dispatch
        if farm_dispatch(handler,store,principal,path,method,body):return True
        from .ui_api import dispatch as ui_dispatch
        if ui_dispatch(handler,store,service,principal,path,method):return True
        if path=='/api/auth/reauthenticate' and method=='POST':
            raw,principal=service.reauthenticate(principal,body.get('password'),raw=raw)
            secure='; Secure' if handler.secure_transport else ''
            handler.json({'status':'REAUTHENTICATED','csrf':service.csrf(raw)},extra_headers=(('Set-Cookie','chief_session='+raw+'; HttpOnly; SameSite=Strict; Path=/'+secure),));return True
        if path=='/api/auth/logout' and method=='POST':
            secure='; Secure' if handler.secure_transport else ''
            service.revoke(principal);handler.json({'status':'SIGNED_OUT'},extra_headers=(('Set-Cookie','chief_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'+secure),));return True
        if path=='/api/auth/users' and method=='POST':
            try:
                service.user_management(principal,sensitive=True)
                from .composition import default_registry
                domains=body.get('domains',())
                if not isinstance(domains,list) or not all(isinstance(d,str) for d in domains):raise ValueError('Choose explicit domain access.')
                if not set(domains)<={d['id'] for d in default_registry().describe()}|{'*'}:raise ValueError('Unknown domain scope.')
                farm_user_management_guard(store, principal, domains)
                identity=service.create_user(principal,body.get('username',''),body.get('password'),body.get('role'),domains)
            finally:body.pop('password',None)
            handler.json({'id':identity});return True
        if path=='/api/auth/users' and method in {'GET','HEAD'}:
            users = service.list_users(principal)
            visible = []
            for user in users:
                try: farm_user_management_guard(store, principal, user['domains'])
                except PermissionError: continue
                visible.append(user)
            management=service.user_management(principal)
            scopes=[]
            for scope in management['domains']:
                try:farm_user_management_guard(store,principal,[scope])
                except PermissionError:continue
                scopes.append(scope)
            management={**management,'domains':scopes,'roles':management['roles'] if scopes else []}
            handler.json({'users':visible,'management':management});return True
        if path.startswith('/api/auth/users/') and path.endswith('/disable') and method=='POST':
            with store._connect() as con:
                target=con.execute('SELECT domains FROM human_identities WHERE id=?',(path.split('/')[-2],)).fetchone()
            if target: farm_user_management_guard(store, principal, json.loads(target['domains']))
            service.disable_user(principal,path.split('/')[-2]);handler.json({'status':'DISABLED'});return True
        if path in {'/api/auth/delegations','/api/auth/emergencies'} and method=='POST':
            emergency=path.endswith('emergencies')
            identity=service.grant(principal,body.get('recipient'),body.get('domain'),resource=body.get('resource'),seconds=body.get('seconds',900),emergency=emergency,reason_ref=body.get('reason_ref','operator-review'))
            handler.json({'id':identity});return True
        if (path.startswith('/api/auth/delegations/') or path.startswith('/api/auth/emergencies/')) and method=='DELETE':
            service.revoke_grant(principal,path.rsplit('/',1)[1],emergency='/emergencies/' in path);handler.json({'status':'REVOKED'});return True
        if path=='/':return False
        permission,domain,resource,sensitive=requirement(method,path,body,store)
        if permission=='UNSUPPORTED':
            handler.json({'status':'NOT_FOUND'},404);return True
        try:authority=service.authorize(principal,permission,domain,resource,sensitive=sensitive)
        except PermissionError:
            service.event(principal,permission,domain,None,'DENIED');raise
        service.event(principal,permission,domain,resource,'AUTHORIZED',authority)
        if path.startswith('/api/model-setup'):
            from model_registry.setup import ModelSetup
            from pathlib import Path
            setup=ModelSetup(Path(os.environ['CHIEF_STATE_ROOT']))
            if method=='POST':
                peer=ipaddress.ip_address(handler.client_address[0])
                if not (handler.secure_transport or (peer.is_loopback and urlparse('http://'+handler.headers.get('Host','')).hostname in {'localhost','127.0.0.1','::1'})):
                    raise PermissionError('API key setup requires local loopback or a TLS connection.')
            if path=='/api/model-setup' and method in {'GET','HEAD'}:
                handler.json({'registrations':setup.list()});return True
            if path=='/api/model-setup' and method=='POST':
                try:record=setup.add(body,actor=principal.id)
                finally:body.pop('api_key',None)
                service.event(principal,'MODEL_REGISTERED',None,record['id'],'SAVED_INACTIVE')
                handler.json(record,201);return True
            parts=path.strip('/').split('/')
            if len(parts)==4 and parts[3]=='check' and method=='POST':
                if body!={'confirmed':True}:raise ValueError('Explicit confirmation is required for a provider connection check.')
                if os.environ.get('CHIEF_INSTANCE_MODE','preview').lower()=='preview':raise PermissionError('External connection checks are disabled in preview.')
                result=setup.check_connection(parts[2])
                service.event(principal,'MODEL_CONNECTION_CHECK',None,parts[2],result['status'])
                handler.json(result);return True
            handler.json({'status':'NOT_FOUND'},404);return True
        if path=='/api/actions' and method in {'GET','HEAD'}:
            handler.json([row for row in store.actions() if action_domain(store,row['id'])=='jobs']);return True
        if path.startswith('/api/actions/') and method=='POST' and str(body.get('status','')).upper()=='APPROVED':
            try:service.approve_action(principal,int(path.rsplit('/',1)[1]),domain)
            except ValueError as exc:handler.json({'status':'REJECTED','reason':str(exc)},409);return True
            handler.json({'status':'APPROVED','action_id':int(path.rsplit('/',1)[1]),'message':'Identified human approval recorded; existing worker checks still apply.'});return True
        if path=='/api/model-controls' and method=='POST':
            model_registry(store).set_global_state(body.get('model'),body.get('state'),actor=principal.id);handler.json({'status':'UPDATED'});return True
        if path=='/api/model-controls' and method in {'GET','HEAD'}:
            registry=model_registry(store);handler.json({m:registry.state(m).value for m in registry.models});return True
        if path=='/api/model-policy' and method=='POST':
            from model_registry.contracts import InstallationPolicy
            policy=InstallationPolicy(tuple(body.get('allowed_models',())),free_only=body.get('free_only',True),legacy_job_compatibility=body.get('legacy_job_compatibility',True))
            model_registry(store).set_installation_policy(policy,actor=principal.id);handler.json({'status':'UPDATED'});return True
        return False
    except SetupRequired as exc:handler.json({'status':'SETUP_REQUIRED','reason':str(exc)},503);return True
    except ReauthenticationRequired as exc:handler.json({'status':'REAUTHENTICATION_REQUIRED','reason':str(exc)},428);return True
    except AuthenticationRequired as exc:handler.json({'status':'AUTHENTICATION_REQUIRED','reason':str(exc)},401);return True


def handler_root(handler):
    from pathlib import Path
    return Path(__file__).resolve().parents[1]


def farm_user_management_guard(store, principal, domains, *, con=None):
    if principal.role in {'Owner','Administrator'} or not ({'farming','*'} & set(domains)):
        return
    from domains.farming import setup
    if con is not None:
        setup.authorize(store, con, principal, 'finance')
    else:
        with store._connect() as connection:
            setup.authorize(store, connection, principal, 'finance')
