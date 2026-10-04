import json
from types import SimpleNamespace

from database.store import Store
from skills.source_discovery import discover_sources, verify_discovered_source
from worker.browser_worker import BrowserJobWorker
from worker.command_processor import CommandProcessor


class FakeGateway:
    def generate(self, request):
        if "planning brain" in request.system:
            return SimpleNamespace(text=json.dumps({"summary":"Discover sources","actions":[{"action":"discover_sources","reason":"Find additional sources","payload":{"instruction":"find more job platforms"}}]}))
        return SimpleNamespace(text=json.dumps({"sources":[
            {"name":"LinkedIn","url":"https://linkedin.com/jobs","kind":"platform","reason":"Major job platform"},
            {"name":"Example Careers","url":"https://example.com/careers","kind":"company_careers","reason":"Company career site"},
            {"name":"Bad HTTP","url":"http://bad.example/careers","kind":"company_careers","reason":"Not safe to visit"},
        ]}))


def test_discovered_https_source_stays_review():
    out = verify_discovered_source({"name":"Example","url":"https://example.com/jobs","kind":"platform"})
    assert out["verification_status"] == "REVIEW"
    assert out["protocol"] == "HTTPS"


def test_discovered_http_source_is_quarantined():
    out = verify_discovered_source({"name":"Example","url":"http://example.com/jobs","kind":"platform"})
    assert out["verification_status"] == "HTTP_QUARANTINED"


def test_command_discovers_and_registers_sources(tmp_path):
    store = Store(tmp_path/'db.sqlite')
    cid = store.queue_command('find more job platforms')
    w = BrowserJobWorker(store, FakeGateway(), config_path='config/sources.json')
    cp = CommandProcessor(store, FakeGateway(), browser_worker=w)
    out = cp.process_command(cid, 'find more job platforms')
    assert out['approvals'] == []
    assert len(out['results'][0]['result']['sources']) == 3
    statuses = {x['name']: x['verification_status'] for x in store.sources()}
    assert statuses['Example Careers'] == 'REVIEW'
    assert statuses['Bad HTTP'] == 'HTTP_QUARANTINED'
