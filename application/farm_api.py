"""Authenticated human record entry; unavailable for production until acceptance."""
import os
from domains.farming import journal, setup, bookkeeping, staff, assistant, photos, brief
from urllib.parse import urlsplit, parse_qs


def dispatch(handler, store, principal, path, method, body):
    parsed = urlsplit(handler.path)
    if parsed.path not in {'/api/farm/tasks','/api/farm/planning','/api/farm/planning/preview','/api/farm/health-records','/api/farm/labour','/api/farm/units','/api/farm/costing','/api/farm/costing/policy','/api/farm/financial-permissions','/api/farm/physical-counts','/api/farm/notification-recipients','/api/farm/brief','/api/farm/brief/schedule','/api/farm/finance','/api/farm/journal', '/api/farm/setup', '/api/farm/bookkeeping', '/api/farm/staff', '/api/farm/assistant', '/api/farm/assistant/configure', '/api/farm/photos'} and not parsed.path.startswith(('/api/farm/photos/','/api/farm/receipts/','/api/farm/finance/history/')):
        return False
    if os.environ.get('CHIEF_INSTANCE_MODE', '').lower() not in {'test', 'preview'}:
        raise PermissionError('Farm pilot is not yet approved for production.')
    if parsed.path == '/api/farm/tasks':
        from domains.farming import tasks
        if method in {'GET', 'HEAD'}:
            q = parse_qs(parsed.query)
            handler.json(tasks.overview(store, principal, offset=int(q.get('offset', ['0'])[0])))
        elif method == 'POST':
            handler.json(tasks.append(store, principal, body))
        else:
            handler.json({'status': 'METHOD_NOT_ALLOWED'}, 405)
        return True
    if parsed.path in {'/api/farm/planning', '/api/farm/planning/preview'}:
        from domains.farming import planning
        if parsed.path.endswith('/preview'):
            if method == 'POST': handler.json(planning.preview(store, principal, body))
            else: handler.json({'status': 'METHOD_NOT_ALLOWED'}, 405)
        elif method in {'GET', 'HEAD'}:
            q = parse_qs(parsed.query)
            handler.json(planning.overview(store, principal, offset=int(q.get('offset', ['0'])[0]), revision=q.get('revision', [None])[0]))
        elif method == 'POST': handler.json(planning.save(store, principal, body))
        else: handler.json({'status': 'METHOD_NOT_ALLOWED'}, 405)
        return True
    if parsed.path == '/api/farm/health-records':
        from domains.farming import health_records
        if method in {'GET','HEAD'}: handler.json(health_records.overview(store,principal,offset=int(parse_qs(parsed.query).get('offset',['0'])[0])))
        elif method == 'POST': handler.json(health_records.append(store,principal,body))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path == '/api/farm/labour':
        from domains.farming import labour
        if method in {'GET','HEAD'}: handler.json(labour.overview(store,principal,offset=int(parse_qs(parsed.query).get('offset',['0'])[0])))
        elif method == 'POST': handler.json(labour.append(store,principal,body))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path == '/api/farm/units':
        from domains.farming import units
        if method in {'GET','HEAD'}: handler.json(units.overview(store,principal))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path in {'/api/farm/costing', '/api/farm/costing/policy'}:
        from domains.farming import costing_policy
        if parsed.path.endswith('/policy'):
            if method in {'GET','HEAD'}: handler.json(costing_policy.overview(store,principal))
            elif method == 'POST': handler.json(costing_policy.configure(store,principal,body))
            else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        elif method in {'GET','HEAD'}:
            q=parse_qs(parsed.query)
            handler.json(costing_policy.report(store,principal,start=q.get('start',[''])[0],end=q.get('end',[''])[0],revision=q.get('revision',[None])[0]))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path == '/api/farm/financial-permissions':
        from domains.farming import financial_permissions
        if method in {'GET', 'HEAD'}: handler.json(financial_permissions.overview(store, principal))
        elif method == 'POST': handler.json(financial_permissions.configure(store, principal, body))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path == '/api/farm/physical-counts':
        from domains.farming import reconciliation
        if method in {'GET', 'HEAD'}: handler.json(reconciliation.overview(store, principal))
        elif method == 'POST': handler.json(reconciliation.append(store, principal, body))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path == '/api/farm/notification-recipients':
        from domains.farming import recipients
        if method in {'GET', 'HEAD'}: handler.json(recipients.overview(store, principal))
        elif method == 'POST': handler.json(recipients.configure(store, principal, body))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path in {'/api/farm/brief', '/api/farm/brief/schedule'}:
        if parsed.path.endswith('/schedule') and method == 'POST':
            handler.json(brief.configure(store, principal, body))
        elif parsed.path == '/api/farm/brief' and method in {'GET', 'HEAD'}:
            handler.json(brief.overview(store, principal, parse_qs(parsed.query).get('day', [''])[0]))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path.startswith('/api/farm/finance/history/'):
        from domains.farming import finance
        if method in {'GET','HEAD'}: handler.json(finance.linked_history(store,principal,parsed.path.rsplit('/',1)[-1]))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path=='/api/farm/finance':
        from domains.farming import finance
        if method in {'GET','HEAD'}:
            q=parse_qs(parsed.query)
            handler.json(finance.report(store,principal,start=q.get('start',[''])[0],end=q.get('end',[''])[0],query=q.get('q',[''])[0],contact_id=q.get('contact',[''])[0],offset=int(q.get('offset',['0'])[0])))
        elif method=='POST':handler.json(finance.append(store,principal,body))
        else:handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path.startswith('/api/farm/receipts/'):
        from domains.farming import finance
        if method not in {'GET','HEAD'}:handler.json({'status':'METHOD_NOT_ALLOWED'},405);return True
        raw=finance.receipt(store,principal,parsed.path.rsplit('/',1)[-1])
        handler.send_response(200);handler.send_header('Content-Type','image/png');handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff');handler.send_header('Content-Length',str(len(raw)));handler.end_headers()
        if method!='HEAD':handler.wfile.write(raw)
        return True
    if parsed.path == '/api/farm/assistant/configure':
        from domains.farming import live_ai
        if method == 'POST': handler.json(live_ai.configure(store, principal, body))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path == '/api/farm/photos':
        if method in {'GET','HEAD'}:
            query=parse_qs(parsed.query);handler.json(photos.listing(store,principal,query.get('work_id',[''])[0]))
        elif method=='POST':handler.json(photos.append(store,principal,body))
        else:handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path.startswith('/api/farm/photos/'):
        if method not in {'GET','HEAD'}:handler.json({'status':'METHOD_NOT_ALLOWED'},405);return True
        raw=photos.image(store,principal,parsed.path.rsplit('/',1)[-1])
        handler.send_response(200);handler.send_header('Content-Type','image/png');handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff');handler.send_header('Content-Length',str(len(raw)));handler.end_headers()
        if method!='HEAD':handler.wfile.write(raw)
        return True
    if parsed.path == '/api/farm/assistant':
        if method in {'GET','HEAD'}:handler.json(assistant.overview(store,principal))
        elif method=='POST':handler.json(assistant.ask(store,principal,body))
        else:handler.json({'status':'METHOD_NOT_ALLOWED'},405)
        return True
    if parsed.path in {'/api/farm/bookkeeping', '/api/farm/staff'}:
        module = staff if parsed.path.endswith('/staff') else bookkeeping
        if method in {'GET', 'HEAD'}: handler.json(module.overview(store, principal))
        elif method == 'POST': handler.json(module.append(store, principal, body))
        else: handler.json({'status':'METHOD_NOT_ALLOWED'}, 405)
        return True
    if method in {'GET', 'HEAD'}:
        if parsed.path == '/api/farm/setup':
            handler.json(setup.overview(store, principal))
        else:
            query = parse_qs(parsed.query)
            handler.json(journal.overview(store, principal, offset=int(query.get('offset', ['0'])[0])))
    elif method == 'POST':
        handler.json((setup if parsed.path == '/api/farm/setup' else journal).append(store, principal, body))
    else:
        handler.json({'status': 'METHOD_NOT_ALLOWED'}, 405)
    return True
