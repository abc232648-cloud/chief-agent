import json
from types import SimpleNamespace
import pytest
from browser.scrapy_collect import extract, validate_target, collect
from browser.playwright_reader import BrowserReadError
from config.source_config import load_sources, SourceConfigError
from database.store import Store
from worker.browser_worker import BrowserJobWorker
from test_browser_worker import FakeGateway

WHEN = '2026-01-01T00:00:00+00:00'
URL = 'https://careers.example.com/jobs'


def body(url=URL):
    posting = {'@type': 'JobPosting', 'title': 'SOC Analyst',
               'hiringOrganization': {'name': 'Example'},
               'description': '<p>Monitor security alerts.</p>', 'url': url}
    return ('<script type="application/ld+json">'+json.dumps(posting)+'</script>').encode()


def test_jobposting_preserves_evidence_and_missing_values():
    result = extract('scrapy_jobposting', URL, body(), WHEN)
    assert result['jobs'][0]['description'] == 'Monitor security alerts.'
    assert result['jobs'][0]['compensation'] is None
    assert result['receipt']['authority'] == 'NONE'
    assert result['receipt']['scope'] == 'SINGLE_RESPONSE'
    assert len(result['receipt']['response_sha256']) == 64


@pytest.mark.parametrize('data', [b'<p>No jobs</p>', b'<input type="password">', b'x'*2000001],
                         ids=['no-job-posting','password-field','oversized-body'])
def test_unsupported_or_large_html_is_explicit_failure(data):
    with pytest.raises(ValueError):extract('scrapy_jobposting', URL, data, WHEN)


@pytest.mark.parametrize('target', ['http://careers.example.com', 'https://127.0.0.1', 'https://user:pass@careers.example.com', 'https://linkedin.com/jobs'])
def test_unsafe_target_rejected(target):
    with pytest.raises(ValueError):validate_target('scrapy_jobposting', target)


def test_official_board_recipes():
    gh = {'jobs': [{'id': 1, 'title':'SOC Analyst','absolute_url':URL, 'content':'Read alerts'}]}
    result = extract('scrapy_greenhouse', 'https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true', json.dumps(gh).encode(), WHEN)
    assert result['jobs'][0]['provider_id'] == '1'
    lever = [{'id':'a','text':'SOC Analyst','hostedUrl':URL,'descriptionPlain':'Read alerts'}]
    result = extract('scrapy_lever', 'https://api.lever.co/v0/postings/example?mode=json', json.dumps(lever).encode(), WHEN)
    assert result['jobs'][0]['provider'] == 'lever'
    with pytest.raises(ValueError):validate_target('scrapy_lever', URL)


def config(tmp_path, collector='scrapy_jobposting'):
    path=tmp_path/'sources.json'
    path.write_text(json.dumps({'sources':[{'name':'Example','domains':['careers.example.com'], 'collector':collector}]}))
    return path


def test_backend_config_rejects_unknown(tmp_path):
    with pytest.raises(SourceConfigError):load_sources(config(tmp_path, 'auto_anything'))


def test_discovery_routes_through_pipeline_and_deduplicates(tmp_path, monkeypatch):
    store=Store(tmp_path/'db.sqlite')
    calls=[]
    def fake(adapter,url):
        calls.append(url)
        return extract(adapter,url,body(url),WHEN)
    monkeypatch.setattr('browser.scrapy_collect.collect',fake)
    worker=BrowserJobWorker(store,FakeGateway(),config_path=config(tmp_path))
    first=worker.discover([URL],{}, {})
    second=worker.discover([URL+'?utm_source=test'],{}, {})
    assert not first['errors'] and not second['errors']
    assert first['jobs'][0]['scam_status']
    assert first['jobs'][0]['dedupe_key']
    assert second['jobs'][0]['status']=='DUPLICATE'
    assert len(calls)==2
    assert len(store.jobs(include_duplicates=False))==1


def test_unapproved_source_never_collects(tmp_path,monkeypatch):
    monkeypatch.setattr('browser.scrapy_collect.collect',lambda *a:pytest.fail('Unapproved fetch'))
    worker=BrowserJobWorker(Store(tmp_path/'db.sqlite'),FakeGateway(),config_path=config(tmp_path))
    assert worker.discover(['https://unknown.example/jobs'],{}, {})['quarantined']


def test_failure_does_not_fall_back_to_browser(tmp_path,monkeypatch):
    def fail(*a):raise BrowserReadError('Blocked')
    monkeypatch.setattr('browser.scrapy_collect.collect',fail)
    worker=BrowserJobWorker(Store(tmp_path/'db.sqlite'),FakeGateway(),config_path=config(tmp_path),reader_factory=lambda *a:pytest.fail('Silent fallback'))
    result=worker.discover([URL],{}, {})
    assert result['errors'] and not result['jobs']


def test_subprocess_strips_secrets_and_propagates_failure(monkeypatch):
    class Proxy:
        url='http://127.0.0.1:4321'
        def __init__(self,*a):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr('browser.scrapy_collect.EgressProxy',Proxy)
    monkeypatch.setenv('GROQ_API_KEY','PRIVATE')
    def run(*a,**kw):
        assert 'GROQ_API_KEY' not in kw['env']
        assert kw['timeout']==65
        return SimpleNamespace(returncode=0,stdout='{"status":"BLOCKED"}')
    monkeypatch.setattr('browser.scrapy_collect.subprocess.run',run)
    with pytest.raises(BrowserReadError):collect('scrapy_jobposting',URL)


@pytest.mark.parametrize('robots_status,robots_text,page_status,expected', [
    (200, 'User-agent: *\nDisallow: /', 200, 'BLOCKED'),
    (403, '', 200, 'BLOCKED'),
    (302, '', 200, 'BLOCKED'),
    (404, '', 200, 'OK'),
    (200, 'User-agent: *\nAllow: /', 302, 'BLOCKED'),
])
def test_actual_scrapy_reactor_with_fixture_transport(robots_status, robots_text, page_status, expected):
    """Real crawler/middleware/robots/parse lifecycle; synthetic transport, no live website."""
    import subprocess, sys, os
    script = '''
import json,sys
import scrapy.crawler
from scrapy.http import HtmlResponse
class FixtureHandler:
    lazy = False
    @classmethod
    def from_crawler(cls,crawler): return cls()
    async def download_request(self,request):
        robots=request.url.endswith('/robots.txt')
        return HtmlResponse(request.url, status=ROBOTS_STATUS if robots else PAGE_STATUS,
                            body=ROBOTS_BODY if robots else PAGE_BODY, encoding='utf-8', request=request)
    async def close(self):pass
RealProcess=scrapy.crawler.CrawlerProcess
class FixtureProcess(RealProcess):
    def __init__(self,settings):
        settings['DOWNLOAD_HANDLERS']={'https':'__main__.FixtureHandler'}
        super().__init__(settings)
scrapy.crawler.CrawlerProcess=FixtureProcess
from browser.scrapy_runner import main
main()
'''
    values = f'ROBOTS_STATUS={robots_status}\nPAGE_STATUS={page_status}\nROBOTS_BODY={robots_text.encode()!r}\nPAGE_BODY={body()!r}\n'
    process=subprocess.run([sys.executable,'-c',values+script],input=json.dumps({'url':URL,'adapter':'scrapy_jobposting','proxy':'http://127.0.0.1:4321'}),text=True,capture_output=True,timeout=25)
    assert process.returncode==0,process.stderr
    assert json.loads(process.stdout)['status']==expected,process.stderr
