from pathlib import Path
import json
import os
from application.http_transport import ResponseHandler, create_dashboard_server, close_dashboard_server
from urllib.parse import urlparse, unquote
from functools import wraps
from config.runtime import load_environment

load_environment()

from application.composition import default_registry
from database.store import Store
from config.source_config import load_sources
from database.store_extensions import add_audit
from notifications.full_audit import FullAuditReport

REGISTRY = default_registry()
ROOT = Path(__file__).resolve().parent
OUTBOX = ROOT / 'notifications' / 'outbox'
from deployment.instance import load_instance
INSTANCE = load_instance('dashboard')
STORE = INSTANCE.open_store()
OUTBOX = INSTANCE.state_root / 'notifications' / 'outbox'

def private_root():
    return ROOT if INSTANCE.mode=='test' else INSTANCE.state_root
from application.control_services import compose_control_services
CONTROL_SERVICES = compose_control_services(STORE)


# Keep configured sources visible in the dashboard, but do not silently turn them into
# trusted sources: verification_status starts at REVIEW until the source-verification flow approves it.
def seed_sources():
    try:
        for s in load_sources(ROOT / 'config' / 'sources.json'):
            for domain in s.domains:
                # Restarting the dashboard must not replace a user's source decisions.
                with STORE._connect() as con:
                    if con.execute('SELECT 1 FROM sources WHERE id=?', (f'{s.name}:{domain}',)).fetchone():
                        continue
                STORE.add_source({'id': f'{s.name}:{domain}', 'name': s.name, 'url': f'https://{domain}',
                                  'kind': 'platform', 'protocol': 'HTTPS', 'verification_status': 'REVIEW',
                                  'notes': 'Configured source; legitimacy verification still required.'})
    except Exception:
        pass

if not STORE.operational:seed_sources()

STATIC_DIR = ROOT / 'static'
DASHBOARD_HTML = ROOT / 'dashboard.html'


def api_errors(method):
    @wraps(method)
    def wrapped(self):
        try:
            authority=urlparse('http://'+self.headers.get('Host',''))
            host=authority.hostname
            port=authority.port  # Validate port syntax/range before authorization.
            allowed={'localhost','127.0.0.1','::1',os.getenv('DASHBOARD_TRUSTED_HOST','localhost')}
            if (host not in allowed or authority.username or authority.password or authority.path
                    or authority.query or authority.fragment or ',' in authority.netloc
                    or self.headers.get('Sec-Fetch-Site')=='cross-site'):
                self.json({'status':'REJECTED','reason':'This dashboard accepts requests from its configured local host only.'},403);return
            if self.command in ('POST', 'DELETE') and self.headers.get('Origin'):
                origin=urlparse(self.headers['Origin'])
                if self.headers['Origin'] != self.scheme+'://'+self.headers.get('Host',''):
                    self.json({'status':'REJECTED','reason':'Cross-site control requests are not allowed.'},403); return
            from application.auth_routes import intercept
            if intercept(self,STORE):return
            from identity.context import human_context
            with human_context(getattr(self,'human',None)):
                return method(self)
        except (ValueError, TypeError) as exc:
            self.json({'status': 'REJECTED', 'reason': str(exc)}, 400)
        except PermissionError as exc:
            self.json({'status': 'REJECTED', 'reason': str(exc)}, 403)
        except FileNotFoundError as exc:
            self.json({'status':'NOT_FOUND','reason':str(exc)},404)
        except Exception:
            # Do not disclose environment values or server paths in an error response.
            self.log_error('APPLICATION_ERROR')
            self.json({'status': 'FAILED', 'reason': 'Server error. Check the dashboard log.', 'correlation_id':self.correlation_id}, 500)
    return wrapped


class Handler(ResponseHandler):
    def json(self,obj,code=200,extra_headers=()):
        data=json.dumps(obj).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
        for key,value in extra_headers:self.send_header(key,value)
        self.end_headers()
        if self.command != 'HEAD': self.wfile.write(data)
    def read_body(self):
        if hasattr(self,'_parsed_body'):return self._parsed_body
        n=int(self.headers.get('Content-Length','0'))
        if n < 0: raise ValueError('Invalid Content-Length')
        if n > 12*1024*1024:raise ValueError('Request exceeds the 12 MiB limit.')
        if n and self.headers.get_content_type()!='application/json':raise ValueError('Control requests must use application/json.')
        body=json.loads(self.rfile.read(n) or '{}')
        if not isinstance(body,dict): raise ValueError('Request body must be a JSON object')
        self._parsed_body=body
        return body
    def serve_static(self, target: Path, content_type: str, csp: bool = False):
        try:
            data=target.read_bytes()
        except OSError:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        if csp:
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        if self.command != 'HEAD': self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    @api_errors
    def do_GET(self):
        path=urlparse(self.path).path
        from control.api import get
        if get(self,STORE,private_root(),path,urlparse(self.path).query,registry=REGISTRY,services=CONTROL_SERVICES):return
        if path == '/api/domains':
            self.json(REGISTRY.describe()); return
        if path.startswith('/api/domains/') and len(path.strip('/').split('/')) == 3:
            from domains.runtime import DomainRuntime
            self.json(DomainRuntime(STORE,registry=REGISTRY,components=CONTROL_SERVICES.controls).overview(path.rsplit('/',1)[1])); return
        if path=='/':
            self.serve_static(DASHBOARD_HTML, 'text/html; charset=utf-8', csp=True); return
        if path.startswith('/static/'):
            rel=path[len('/static/'):]
            if '/' in rel or not rel or rel.startswith('.'):
                self.send_error(404); return
            target=(STATIC_DIR / rel).resolve()
            if target.parent != STATIC_DIR.resolve() or not target.is_file():
                self.send_error(404); return
            mime={'webmanifest':'application/manifest+json','css':'text/css; charset=utf-8','js':'application/javascript; charset=utf-8','html':'text/html; charset=utf-8','ico':'image/x-icon','png':'image/png','svg':'image/svg+xml'}.get(target.suffix.lstrip('.'),'application/octet-stream')
            self.serve_static(target,mime); return
        if path=='/api/state': self.json({'counts':STORE.counts(),'worker':STORE.worker(),'commands':STORE.commands(10)});return
        if path=='/api/actions': self.json(STORE.actions());return
        if path=='/api/jobs': self.json(STORE.jobs(include_duplicates=False));return
        if path=='/api/applications': self.json(STORE.applications());return
        if path.startswith('/api/applications/'):
            parts=path.strip('/').split('/')
            if len(parts)==4 and parts[-1]=='coverage':
                from domains.jobs.record_coverage import summarize
                detail=STORE.application_detail(parts[2])
                if not detail: self.json({'reason':'Application not found.'},404); return
                self.json(summarize(detail,STORE.candidate_facts())); return
            if len(parts)==4 and parts[-1]=='evidence':
                detail=STORE.application_detail(parts[2])
                if not detail: self.json({'reason':'Application not found.'},404); return
                draft=json.loads(detail.get('draft_json') or '{}')
                facts={str(f['id']):f for f in STORE.candidate_facts()}
                claims=[]
                for claim in draft.get('claims',[]):
                    fact=facts.get(str(claim.get('fact_id')))
                    supported=bool(fact and fact['status']=='USER_CONFIRMED' and fact['text'].strip()==claim.get('text'))
                    claims.append({'text':claim.get('text',''),'fact_id':claim.get('fact_id'),'supported':supported,'fact':fact})
                self.json({'claims':claims,'review_required':True,'message':'Evidence checks support factual accuracy; review relevance, readability, and every form answer before approving submission.'}); return
            if len(parts)==4 and parts[-1]=='recovery':
                aid=parts[2]; attempt=STORE.latest_submission_attempt(aid)
                if not attempt: self.json({'attempt':None,'decision':None}); return
                from worker.recovery import RecoveryManager
                decision=RecoveryManager(STORE).decide(aid,str(attempt.get('outcome','')))
                self.json({'attempt':attempt,'decision':{'action':decision.action,'reason':decision.reason,'attempt_no':decision.attempt_no}}); return
            aid=parts[-1]; detail=STORE.application_detail(aid); self.json(detail or {}, 200 if detail else 404); return
        if path=='/api/sources': self.json(STORE.sources());return
        if path=='/api/site-access':
            from browser.site_access import SiteAccess
            self.json(SiteAccess(STORE).all()); return
        if path=='/api/reports': self.json(STORE.reports());return
        if path=='/api/notifications': self.json(STORE.notifications());return
        if path=='/api/cvs': self.json(STORE.cvs());return
        if path=='/api/profiles': self.json(STORE.profile_links());return
        if path=='/api/facts': self.json(STORE.candidate_facts());return
        if path=='/api/settings/email':
            from notifications.email_config import public_email_config
            self.json(public_email_config());return
        if path=='/api/audit': self.json(STORE.audit(limit=500));return
        if path=='/api/scheduler':
            from scheduler import AutonomousScheduler
            sch=AutonomousScheduler(STORE,registry=REGISTRY)
            self.json({'timezone':str(sch.timezone),'poll_seconds':sch.poll_seconds,'audit_time':f'{sch.audit_hour:02d}:{sch.audit_minute:02d}','tasks':STORE.monitoring_tasks()});return
        self.json({'status':'NOT_FOUND','reason':'Route not found.'},404)
    @api_errors
    def do_POST(self):
        path=urlparse(self.path).path;body=self.read_body()
        from control.api import post
        if post(self,STORE,private_root(),path,body,registry=REGISTRY,services=CONTROL_SERVICES):return
        if path.startswith('/api/domains/'):
            parts=path.strip('/').split('/')
            if len(parts) != 5 or parts[3] != 'actions':
                raise ValueError('Invalid domain action route.')
            from domains.runtime import DomainRuntime
            self.json(DomainRuntime(STORE,registry=REGISTRY,components=CONTROL_SERVICES.controls).queue(parts[2], parts[4], body)); return
        if path=='/api/site-access':
            from browser.site_access import SiteAccess
            self.json(SiteAccess(STORE).add(body.get('url',''), body.get('name',''))); return
        if path.startswith('/api/site-access/'):
            from browser.site_access import SiteAccess
            access=SiteAccess(STORE); domain=unquote(path[len('/api/site-access/'):]); action=body.get('action')
            if action in ('login','save') and os.getenv('CHIEF_ALLOW_INTERACTIVE_LOGIN','FALSE')!='TRUE':raise PermissionError('Website sign-in is disabled in this test environment. Sign in on Folio after deployment.')
            self.json(access.start_login(domain) if action=='login' else access.change(domain, action, body.get('hours'))); return
        if path.startswith('/api/cvs/'):
            cid=path.rsplit('/',1)[1]; STORE.set_cv_active(cid,bool(body.get('active'))); add_audit(STORE,'candidate','Updated CV active state',data={'cv_id':cid,'active':body.get('active')}); self.json({'status':'UPDATED'}); return
        if path=='/api/profiles':
            url=str(body.get('url') or ''); status='REVIEW'
            if not url.startswith('https://'): status='HTTP_QUARANTINED'
            pid=STORE.add_profile_link({'label':str(body.get('label') or url),'url':url,'profile_type':str(body.get('profile_type') or 'other'),'check_interval_days':int(body.get('check_interval_days') or 7),'verification_status':status})
            add_audit(STORE,'profile','Added profile link',details=url,data={'profile_id':pid,'verification_status':status}); self.json({'status':'ADDED','id':pid,'verification_status':status}); return
        if path.startswith('/api/facts/'):
            fid=int(path.rsplit('/',1)[1]); status=str(body.get('status',''))
            try: STORE.update_candidate_fact(fid,status)
            except ValueError as exc: self.json({'status':'REJECTED','reason':str(exc)},400); return
            add_audit(STORE,'candidate','User changed candidate fact status',actor='user',data={'fact_id':fid,'status':status}); self.json({'status':'UPDATED','fact_id':fid,'new_status':status}); return
        if path.startswith('/api/profiles/'):
            pid=int(path.rsplit('/',1)[1]); STORE.update_profile_link(pid, **{k:body[k] for k in ('enabled','label','check_interval_days') if k in body}); add_audit(STORE,'profile','Updated profile link',data={'profile_id':pid,**body}); self.json({'status':'UPDATED'}); return
        if path=='/api/audit/generate':
            p=FullAuditReport(STORE,private_root()/'logs').write_day();STORE.add_report('User-requested audit',str(p)); text=p.read_text(encoding='utf-8'); self.json({'path':str(p),'preview':text[-12000:]}); return
        if path=='/api/command':
            instruction=str(body.get('instruction','')).strip()
            if not instruction:self.json({'status':'REJECTED','reason':'Instruction is empty.'},400);return
            cid=STORE.queue_command(instruction);STORE.set_worker('QUEUED','Dashboard instruction waiting for worker')
            add_audit(STORE,'command','Dashboard command queued',data={'command_id':cid})
            self.json({'status':'QUEUED','command_id':cid,'message':'Instruction queued. It cannot bypass the Policy Gate.'});return
        if path.startswith('/api/applications/') and path.endswith('/retry'):
            parts=path.strip('/').split('/')
            if len(parts)!=4: self.json({'status':'REJECTED','reason':'Invalid application retry path'},400); return
            aid=parts[2]
            detail=STORE.application_detail(aid)
            if not detail: self.json({'status':'NOT_FOUND','reason':'Application not found.'},404); return
            from worker.recovery import RecoveryManager
            rm=RecoveryManager(STORE)
            attempt=STORE.latest_submission_attempt(aid)
            if not attempt: self.json({'status':'NOT_RETRYABLE','reason':'No failed submission attempt is recorded.'},400); return
            decision=rm.decide(aid,str(attempt.get('outcome','')))
            if decision.action!='RETRY': self.json({'status':'NOT_RETRYABLE','reason':decision.reason,'recovery_action':decision.action},409); return
            try: data=json.loads(attempt.get('data_json') or '{}')
            except Exception: data={}
            url=str(data.get('url') or (detail.get('snapshots') or [{}])[0].get('source_url') or detail.get('job_url') or '')
            selector=str(data.get('submit_selector') or '')
            if not url or not selector: self.json({'status':'NOT_RETRYABLE','reason':'The failed attempt does not contain enough submission context to retry safely.'},409); return
            from domains.jobs.ledger_adapter import JobSubmissionExecutor as ReliableFinalSubmissionExecutor
            result=ReliableFinalSubmissionExecutor(STORE,max_retries=rm.max_retries).submit(aid,url,selector,approved=True,expected_form_hash=str(data.get('expected_form_hash') or '') or None)
            add_audit(STORE,'recovery','User requested application retry',actor='user',status=str(result.get('status','FAILED')),details=decision.reason,data={'application_id':aid,'previous_attempt_id':attempt.get('id'),'result':result})
            self.json({'status':'RETRIED','message':'The worker was asked to retry the application.','result':result,'recovery':result.get('recovery',{})}); return
        if path.startswith('/api/actions/'):
            try: aid=int(path.rsplit('/',1)[1]);status=str(body.get('status','')).upper()
            except ValueError:self.json({'status':'REJECTED','reason':'Invalid action id'},400);return
            if status == 'APPROVED':
                with STORE._connect() as con:
                    changed=con.execute("UPDATE actions SET status='APPROVED',resolved_at=CURRENT_TIMESTAMP WHERE id=? AND status='PENDING'",(aid,)).rowcount
                if not changed:
                    self.json({'status':'REJECTED','reason':'Action is missing or is no longer pending.'},409);return
                add_audit(STORE,'policy','User approved action',actor='user',data={'action_id':aid})
                self.json({'status':'APPROVED','action_id':aid,'message':'Approval recorded; waiting for the worker.'});return
            try: STORE.resolve_action(aid,status)
            except ValueError as exc:self.json({'status':'REJECTED','reason':str(exc)},400);return
            self.json({'status':'UPDATED','action_id':aid,'new_status':status});return
        self.json({'status':'NOT_FOUND','reason':'Route not found.'},404)

    @api_errors
    def do_DELETE(self):
        path=urlparse(self.path).path
        if path.startswith('/api/profiles/') and len(path.strip('/').split('/')) == 3:
            pid=int(path.rsplit('/',1)[1])
            STORE.delete_profile_link(pid)
            add_audit(STORE,'profile','Removed profile link',actor='user',data={'profile_id':pid})
            self.json({'status':'DELETED','id':pid});return
        self.json({'status':'NOT_FOUND','reason':'Route not found.'},404)

if __name__=='__main__':
    # DASHBOARD_HOST defaults to loopback; no development-server fallback.
    server = create_dashboard_server(Handler)
    try:server.run()
    finally:close_dashboard_server(server)
