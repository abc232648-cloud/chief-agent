import json, urllib.request

def test_dashboard_serves_and_queues_command(dashboard):
    assert urllib.request.urlopen(dashboard.url+'/').status==200
    req=urllib.request.Request(dashboard.url+'/api/command',data=json.dumps({'instruction':'show pending approvals'}).encode(),headers={'Content-Type':'application/json'},method='POST')
    body=json.load(urllib.request.urlopen(req))
    assert body['status']=='QUEUED' and body['command_id']
    state=json.load(urllib.request.urlopen(dashboard.url+'/api/state'))
    assert state['counts']['commands']==1
    assert dashboard.store.commands()[0]['instruction']=='show pending approvals'
