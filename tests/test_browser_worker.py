import json
from types import SimpleNamespace

from database.store import Store
from worker.browser_worker import BrowserJobWorker

class FakeGateway:
    def generate(self, request):
        if "extract job listings" in request.system:
            return SimpleNamespace(text=json.dumps({"jobs":[{"title":"SOC Analyst","company":"Acme","description":"Remote cybersecurity role","location":"Remote","remote":True,"compensation":"$50k","url":"https://linkedin.com/jobs/1"}]}))
        analysis={"summary":"Good security fit","role_fit":"Good","fit_reasons":["Security role"],"missing_requirements":[],"scam_concerns":[],"evidence_flags":[],"recommended_action":"CONSIDER","confidence":0.8}
        summary="1. Summary\nGood security fit\n2. Role fit\nGood\n3. Fit reasons\nSecurity role\n4. Missing requirements\nNone\n5. Scam concerns\nNone\n6. Evidence flags\nNone\n7. Recommended action\nCONSIDER\n8. Confidence\n0.8"
        return SimpleNamespace(text=json.dumps({"analysis":analysis,"summary_text":summary}), provider="fake", model="fake", fallback_used=False)

def test_http_is_quarantined(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    w=BrowserJobWorker(store, FakeGateway(), config_path='config/sources.json')
    out=w.verify_target('http://linkedin.com/jobs/1')
    assert out['status']=='HTTP_QUARANTINED'

def test_unknown_https_source_is_review(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    w=BrowserJobWorker(store, FakeGateway(), config_path='config/sources.json')
    out=w.verify_target('https://example.com/jobs')
    assert out['status']=='REVIEW'

def test_discovery_persists_extracted_job(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    class Reader:
        def read_url(self,url):
            return SimpleNamespace(url=url,payload={'title':'Jobs','text':'SOC Analyst Remote $50k'})
    w=BrowserJobWorker(store, FakeGateway(), config_path='config/sources.json', reader_factory=lambda domains: Reader())
    out=w.discover(['https://linkedin.com/jobs'], {}, {})
    assert out['discovered']==1
    assert store.jobs()[0]['title']=='SOC Analyst'


def test_duplicate_job_is_retained_but_marked_duplicate(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    class Reader:
        def read_url(self, url):
            return SimpleNamespace(url=url, payload={'title': 'Jobs', 'text': 'SOC Analyst Remote $50k'})
    class Gateway(FakeGateway):
        def generate(self, request):
            if "extract job listings" in request.system:
                import json as _json
                source_url = _json.loads(request.user)['source_url']
                return SimpleNamespace(text=_json.dumps({'jobs':[{
                    'title':'SOC Analyst','company':'Acme','description':'Remote cybersecurity role',
                    'location':'Remote','remote':True,'compensation':'$50k','url':source_url
                }]}))
            return super().generate(request)
    w = BrowserJobWorker(store, Gateway(), config_path='config/sources.json', reader_factory=lambda domains: Reader())
    first = w.discover(['https://linkedin.com/jobs/1'], {}, {})
    second = w.discover(['https://linkedin.com/jobs/1?utm_source=test'], {}, {})
    assert first['discovered'] == 1
    assert second['discovered'] == 1
    jobs = store.jobs()
    assert len(jobs) == 2
    assert sum(j['status'] == 'DUPLICATE' for j in jobs) == 1
    assert len(store.jobs(include_duplicates=False)) == 1
