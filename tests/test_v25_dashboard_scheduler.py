import json, urllib.request

def test_dashboard_scheduler_endpoint(dashboard):
    with urllib.request.urlopen(dashboard.url+'/api/scheduler') as response:
        data=json.load(response)
    assert 'timezone' in data and 'tasks' in data
