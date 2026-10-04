import json
from datetime import datetime,timezone
from urllib.parse import parse_qs
from .agents import AgentControls
from . import notifications
from database.store_extensions import add_audit


def get(handler,store,root,path,query,*,registry,services=None):
    if path == '/api/general-schedules':
        from .schedules import list_schedules
        handler.json({'items':list_schedules(store),'domains':list(registry.domains),'actions':['in_app_reminder']});return True
    if path == '/api/integrations/n8n/handshakes':
        from integrations.n8n_handoff import history
        handler.json({'items':history(store)});return True
    if path == '/api/integrations/n8n':
        from integrations.n8n import describe
        handler.json(describe(store));return True
    params={k:v[-1] for k,v in parse_qs(query).items()}
    if path in ('/api/component-controls','/api/system-health') and services is not None:
        handler.json(services.controls.describe() if path=='/api/component-controls' else services.health.describe());return True
    if path=='/api/summary-settings':
        from notifications.report import summary_settings
        handler.json(summary_settings(store,registry=registry));return True
    if path=='/api/chief':
        overview=AgentControls(store,registry).overview();overview['counts']=store.counts()
        overview['job_counts']={'jobs':overview['counts']['jobs'],'applications':overview['counts']['applications']}
        handler.json(overview);return True
    if path=='/api/notification-preferences':handler.json(notifications.preferences(store));return True
    if path=='/api/notifications':handler.json(notifications.list_notifications(store,params));return True
    if path=='/api/session-control':
        import os
        handler.json({'sign_in_enabled':os.getenv('CHIEF_ALLOW_INTERACTIVE_LOGIN','FALSE')=='TRUE','message':'Real website sign-in is reserved for Folio. Website access controls are under Job Agent → Sources.'});return True
    parts=path.strip('/').split('/')
    if len(parts)==4 and parts[0]=='api' and parts[1] in ('reports','cvs') and parts[3] in ('download','preview'):
        from .files import read_registered,report_text,pdf_report,MIMES
        data,suffix=read_registered(store,root,parts[1],parts[2])
        if parts[1]=='reports':
            text=report_text(data,suffix)
            if parts[3]=='preview':handler.json({'text':text[:500000],'truncated':len(text)>500000});return True
            fmt=params.get('format','txt')
            if fmt=='pdf':data=pdf_report(text);suffix='.pdf'
            elif fmt=='txt':data=text.encode();suffix='.txt'
            elif fmt=='json':data=json.dumps({'report_id':parts[2],'text':text},ensure_ascii=False).encode();suffix='.json'
            else:raise ValueError('Supported report exports: TXT, PDF, JSON.')
        elif parts[3]=='preview':raise ValueError('Use the registered document download or its confirmed fact preview.')
        handler.send_response(200);handler.send_header('Content-Type',MIMES.get(suffix,'application/json'))
        handler.send_header('Content-Disposition','attachment; filename="'+parts[1]+'-'+parts[2]+suffix+'"')
        handler.send_header('Content-Length',str(len(data)));handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers()
        if handler.command!='HEAD':handler.wfile.write(data)
        return True
    return False


def post(handler,store,root,path,body,*,registry,services=None):
    if path=='/api/general-schedules' or path.startswith('/api/general-schedules/'):
        from .schedules import create, change
        from identity.context import current_human
        actor=current_human()
        if actor is None:raise PermissionError('Signed-in schedule owner required.')
        result=create(store,registry,actor.id,body) if path=='/api/general-schedules' else change(store,path.rsplit('/',1)[-1],body)
        add_audit(store,'scheduler','Changed general reminder schedule',actor=actor.id,data={'schedule_id':result['id'],'revision':result['revision']})
        handler.json(result);return True
    if path == '/api/integrations/n8n/handshake':
        from integrations.n8n_handoff import run
        from identity.context import current_human
        handler.json(run(store,registry,current_human(),body));return True
    if path in {'/api/integrations/n8n', '/api/integrations/n8n/check'}:
        from integrations.n8n import check, set_enabled
        if path.endswith('/check'):
            if body != {}:raise ValueError('Connection check takes no fields.')
            handler.json(check(store))
        else:
            if set(body) != {'enabled'}:raise ValueError('Supply only enabled.')
            handler.json(set_enabled(store, body['enabled']))
        return True
    parts=path.strip('/').split('/')
    if path in ('/api/component-controls/preview','/api/component-controls/transition') and services is not None:
        from capabilities.contracts import Mode,Node
        from dataclasses import asdict
        required={'kind','id','mode'}
        permitted=required if path.endswith('/preview') else required|{'token','reason','confirmed'}
        if not isinstance(body,dict) or not required<=set(body) or set(body)-permitted:
            raise ValueError('Supply the declared component transition fields only.')
        node=Node(body['kind'],body['id']);mode=Mode(body['mode'])
        preview=services.controls.preview(node,mode)
        if path.endswith('/preview'):
            handler.json(asdict(preview));return True
        if body.get('token')!=preview.token:
            raise ValueError('Stale component preview; review impact again.')
        from identity.context import current_human
        human=current_human()
        result=services.controls.transition(preview,actor=human.id if human else 'user',reason=body.get('reason'),confirmed=body.get('confirmed',False))
        handler.json(asdict(result));return True
    if path=='/api/summary-settings':
        from notifications.report import summary_settings
        handler.json(summary_settings(store,body,registry=registry));return True
    if path.startswith('/api/agent-controls/'):
        handler.json(AgentControls(store,registry).change(parts[-1],body));return True
    if path=='/api/notification-preferences':handler.json(notifications.preferences(store,body));return True
    if path.startswith('/api/notifications/'):
        handler.json(notifications.update(store,parts[-1],body));return True
    if path=='/api/cvs':
        from .files import upload_cv
        handler.json(upload_cv(store,root,body));return True
    if path=='/api/facts':
        text=str(body.get('text','')).strip()
        if not text or len(text)>2000:raise ValueError('Fact text must contain 1–2000 characters.')
        identity=store.add_candidate_fact({'text':text,'source_type':'USER_ENTERED','source_detail':str(body.get('source_detail',''))[:2000]})
        add_audit(store,'candidate','Added proposed fact',actor='user',data={'fact_id':identity})
        handler.json({'status':'PROPOSED','id':identity});return True
    if len(parts)==4 and parts[1]=='facts' and parts[3]=='edit':
        text=str(body.get('text','')).strip()
        if not text or len(text)>2000:raise ValueError('Fact text must contain 1–2000 characters.')
        AgentControls(store,registry)
        with store._connect() as con:
            row=con.execute('SELECT * FROM candidate_facts WHERE id=?',(int(parts[2]),)).fetchone()
            if not row:raise ValueError('Fact does not exist.')
            con.execute('INSERT INTO fact_history(fact_id,text,status) VALUES(?,?,?)',(row['id'],row['text'],row['status']))
            con.execute("UPDATE candidate_facts SET text=?,status='PROPOSED',confirmed_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(text,row['id']))
        add_audit(store,'candidate','Edited fact; reconfirmation required',actor='user',data={'fact_id':int(parts[2])})
        handler.json({'status':'PROPOSED','message':'Reconfirm the revised fact. Existing submissions retain their historical snapshots.'});return True
    if path=='/api/schedules' or path.startswith('/api/schedules/'):
        from monitoring import seed_monitoring
        seed_monitoring(store)
        if path=='/api/schedules':
            if body.get('task_type') not in ('job_market_refresh','profile_refresh'):raise ValueError('Choose a supported Job Agent schedule.')
            days=body.get('interval_days')
            if type(days) is not int or not 1<=days<=365:raise ValueError('Interval must be 1–365 days.')
            identity=store.add_monitoring_task({'task_type':body['task_type'],'interval_days':days,'notes':str(body.get('notes',''))[:500]})
        else:
            identity=int(parts[-1]);allowed={'enabled','interval_days','next_due_at','remove'}
            if not body or set(body)-allowed:raise ValueError('Invalid schedule controls.')
            if 'enabled' in body and type(body['enabled']) is not bool:raise ValueError('Enabled must be true/false.')
            if 'interval_days' in body and (type(body['interval_days']) is not int or not 1<=body['interval_days']<=365):raise ValueError('Interval must be 1–365 days.')
            if 'next_due_at' in body:
                due=datetime.fromisoformat(str(body['next_due_at']).replace('Z','+00:00'))
                if due.tzinfo is None:raise ValueError('Due time must include a timezone.')
                body['next_due_at']=due.astimezone(timezone.utc).isoformat()
            with store._connect() as con:
                row=con.execute('SELECT status FROM monitoring_tasks WHERE id=?',(identity,)).fetchone()
                if not row or row['status']=='REMOVED':raise ValueError('Schedule does not exist.')
                if body.get('remove') is True:con.execute("UPDATE monitoring_tasks SET enabled=0,status='REMOVED',updated_at=CURRENT_TIMESTAMP WHERE id=?",(identity,))
                else:
                    changes={k:v for k,v in body.items() if k!='remove'}
                    if not changes:raise ValueError('No schedule changes supplied.')
                    con.execute('UPDATE monitoring_tasks SET '+','.join(k+'=?' for k in changes)+',updated_at=CURRENT_TIMESTAMP WHERE id=?',[*changes.values(),identity])
        add_audit(store,'scheduler','User changed schedule',actor='user',data={'schedule_id':identity})
        handler.json({'status':'UPDATED','id':identity});return True
    if path=='/api/settings/email':
        from .settings import save
        handler.json(save(body));add_audit(store,'settings','Updated email settings',actor='user');return True
    if path=='/api/settings/email/test':
        from notifications.channels import ConfiguredSmtpEmailChannel
        try:ConfiguredSmtpEmailChannel.from_environment()._send('Chief Agent test notification','You requested this email delivery test from Chief Agent.')
        except Exception:
            handler.json({'status':'FAILED','reason':'Email test failed. Check the mail server, credentials and provider requirements.'},400);return True
        handler.json({'status':'SENT','message':'Test email accepted by the configured mail server.'});return True
    return False
