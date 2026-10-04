import json
from pathlib import Path
from types import SimpleNamespace,ModuleType
import sys
import pytest
from config.source_config import ApprovedSource,SourceConfigError,load_sources
from database.store import Store
from worker.browser_worker import BrowserJobWorker
from browser.playwright_reader import PlaywrightReader,BrowserReadConfig,BrowserReadError

def test_start_urls_preserve_existing_domain_boundary():
    for url in ['http://example.test/jobs','https://outside.test/jobs','https://example.test.attacker.test/jobs']:
        with pytest.raises(SourceConfigError):ApprovedSource('test',('example.test',),start_urls=(url,)).validate()
    ApprovedSource('test',('example.test',),start_urls=('https://www.example.test/jobs',)).validate()

def test_configured_sources_have_concrete_listing_urls():
    sources=load_sources(Path(__file__).resolve().parents[1]/'config/sources.json')
    assert {s.name for s in sources}=={'linkedin','upwork'}
    assert all(s.start_urls and all('/jobs/' in u or '/freelance-jobs/' in u for u in s.start_urls) for s in sources)

def test_extraction_batches_text_and_deduplicates_overlap(tmp_path):
    requests=[]
    class Gateway:
        def generate(self,req):
            requests.append(req)
            return SimpleNamespace(text=json.dumps({'jobs':[{'title':'SOC Analyst','company':'Example','url':'https://linkedin.com/jobs/1'}]}))
    worker=BrowserJobWorker(Store(tmp_path/'db.sqlite'),Gateway())
    jobs=worker._extract_jobs({'text':'SOC Analyst '+('test '*2000),'links':[{'text':'SOC Analyst','url':'https://linkedin.com/jobs/1'}]},'linkedin','https://linkedin.com/jobs')
    assert len(requests)>1 and len(jobs)==1
    assert all(r.max_tokens==768 for r in requests)
    assert all(len(json.loads(r.user)['page_text'])<=4000 for r in requests)
    assert json.loads(requests[0].user)['links'][0]['url']=='https://linkedin.com/jobs/1'

def test_all_ai_skill_requests_fit_observed_per_request_quota():
    from skills.source_discovery import discover_sources
    from skills.ai_job_analysis.skill import analyze_job_with_ai
    from skills.application_generation.skill import generate_application_draft
    class Stop(Exception):pass
    requests=[]
    class Gateway:
        def generate(self,req):requests.append(req);raise Stop()
    for call in [lambda:discover_sources(Gateway(),'Test'),lambda:analyze_job_with_ai(Gateway(),{},{}),lambda:generate_application_draft(Gateway(),{},{})]:
        with pytest.raises(Stop):call()
    assert [r.max_tokens for r in requests]==[512,768,768]

@pytest.mark.parametrize('http_status,canonical,expected',[(403,None,'HTTP 403'),(200,None,None),(200,'https://outside.test/job','allowlist')])
def test_reader_reports_http_blocks_and_handles_absent_canonical(monkeypatch,http_status,canonical,expected):
    class Locator:
        @property
        def first(self):return self
        def count(self):return int(canonical is not None)
        def get_attribute(self,name):return canonical
        def inner_text(self,**kw):return 'Job listing'
        def evaluate_all(self,expr):return []
    class Page:
        url='https://example.test/job'
        def goto(self,*args,**kw):return SimpleNamespace(status=http_status)
        def title(self):return 'Job'
        def locator(self,*args):return Locator()
    class Browser:
        def new_context(self,**kwargs):return SimpleNamespace(new_page=self.new_page,route=lambda *a:None,route_web_socket=lambda *a:None,add_init_script=lambda **kw:None)
        def new_page(self):return Page()
        def close(self):pass
    class Playwright:
        chromium=SimpleNamespace(launch=lambda **kw:Browser())
        def __enter__(self):return self
        def __exit__(self,*args):pass
    module=ModuleType('playwright.sync_api');module.sync_playwright=lambda:Playwright()
    monkeypatch.setitem(sys.modules,'playwright.sync_api',module)
    reader=PlaywrightReader(BrowserReadConfig(allowed_domains=('example.test',)))
    if expected:
        with pytest.raises(BrowserReadError,match=expected):reader.read_url('https://example.test/job')
    else:assert reader.read_url('https://example.test/job').url=='https://example.test/job'
