import json
import time
import pytest
from database.store import Store
from browser.site_access import SiteAccess, site_url
from browser.playwright_reader import BrowserReadError
from worker.browser_worker import BrowserJobWorker


def test_pause_resume_revoke_and_persisted_timer(tmp_path):
    store=Store(tmp_path/'worker.db'); access=SiteAccess(store)
    access.add('https://example.test/jobs', 'Example')
    with pytest.raises(BrowserReadError): access.assert_active('example.test')
    access.change('example.test','public')
    access.change('example.test','pause',1)
    assert SiteAccess(store).get('example.test')['status']=='PAUSED'
    with pytest.raises(BrowserReadError): access.assert_active('example.test')
    with store._connect() as con:
        con.execute('UPDATE site_access SET resume_at=?',(time.time()-1,))
    assert access.get('example.test')['status']=='ACTIVE'
    access.folder.mkdir(); access.path('example.test').write_text('{"cookies":[]}')
    access.change('example.test','revoke')
    assert not access.path('example.test').exists()
    with pytest.raises(ValueError): access.change('example.test','resume')
    with pytest.raises(BrowserReadError): access.assert_active('example.test')


def test_paused_configured_domain_cannot_fall_back_to_public_reader(tmp_path):
    access=SiteAccess(Store(tmp_path/'worker.db'))
    access.add('https://linkedin.com/jobs')
    access.change('linkedin.com','public'); access.change('linkedin.com','pause')
    worker=BrowserJobWorker(access.store,None)
    assert worker.verify_target('https://www.linkedin.com/jobs/123')['status']=='PAUSED'
    assert all(s.name!='linkedin' for s in worker.active_sources())


def test_recommendation_never_resumes_access_and_requires_new_reason(tmp_path,monkeypatch):
    sent=[]
    monkeypatch.setattr('notifications.site_alerts.send_site_alert',lambda *args:sent.append(args))
    access=SiteAccess(Store(tmp_path/'worker.db'))
    access.add('https://example.test/jobs'); access.change('example.test','revoke')
    assert access.recommend('https://example.test/jobs','New relevant SOC roles')
    access.change('example.test','dismiss')
    assert not access.recommend('https://example.test/jobs','New relevant SOC roles')
    assert access.get('example.test')['dismissed']==1
    assert access.recommend('https://example.test/jobs','New entry-level remote roles')
    assert access.get('example.test')['status']=='REVOKED' and len(sent)==2


@pytest.mark.parametrize('url',['http://example.test','https://u:p@example.test','https://127.0.0.1','https://localhost','https://example.test:8765'])
def test_invalid_site_urls(url):
    with pytest.raises(ValueError): site_url(url)


def test_www_url_uses_parent_domain_for_normal_site_cookies(tmp_path):
    access=SiteAccess(Store(tmp_path/'db'))
    row=access.add('https://www.example.test/jobs')
    assert row['domain']=='example.test'
    access.change('example.test','public')
    assert access.for_url('https://example.test/jobs')['status']=='ACTIVE'


def test_login_expiry_and_revision_invalidate_inflight_reads(tmp_path):
    access=SiteAccess(Store(tmp_path/'db'))
    access.add('https://example.test'); row=access.change('example.test','public')
    access.change('example.test','pause'); access.change('example.test','resume')
    with pytest.raises(BrowserReadError): access.assert_active('example.test',row['revision'])
    access.change('example.test','login')
    with access.store._connect() as con: con.execute('UPDATE site_access SET login_deadline=0')
    assert access.get('example.test')['status']=='LOGIN_REQUIRED'


def test_cross_site_control_request_cannot_change_access(dashboard):
    import urllib.request,urllib.error
    request=urllib.request.Request(dashboard.url+'/api/site-access',data=json.dumps({'url':'https://example.test'}).encode(),headers={'Content-Type':'application/json','Origin':'https://untrusted.example'})
    with pytest.raises(urllib.error.HTTPError) as result:urllib.request.urlopen(request)
    assert result.value.code==403
    assert SiteAccess(dashboard.store).all()==[]


def test_recommendation_preserves_user_search_url(tmp_path,monkeypatch):
    monkeypatch.setattr('notifications.site_alerts.send_site_alert',lambda *args:None)
    access=SiteAccess(Store(tmp_path/'db'))
    access.add('https://example.test/jobs?q=security','My search')
    access.change('example.test','public')
    access.recommend('https://example.test/','A useful site')
    assert access.get('example.test')['url']=='https://example.test/jobs?q=security'
    assert access.get('example.test')['name']=='My search'


def test_paused_read_command_cannot_report_completed(tmp_path):
    from worker.command_processor import CommandProcessor
    from types import SimpleNamespace
    access=SiteAccess(Store(tmp_path/'db'));access.add('https://linkedin.com/jobs');access.change('linkedin.com','pause')
    class Gateway:
        def generate(self,request):return SimpleNamespace(text=json.dumps({'summary':'Read','actions':[{'action':'read_job_listing','payload':{'url':'https://linkedin.com/jobs'}}]}))
    cid=access.store.queue_command('Read')
    CommandProcessor(access.store,Gateway()).process_command(cid,'Read')
    assert access.store.commands()[0]['status']=='FAILED'


def test_login_from_existing_homepage_preserves_configured_job_search(tmp_path):
    access=SiteAccess(Store(tmp_path/'db'));access.add('https://upwork.com');access.change('upwork.com','public')
    worker=BrowserJobWorker(access.store,None)
    expected=next(s.start_urls for s in worker.sources if s.name=='upwork')
    assert next(s.start_urls for s in worker.active_sources() if s.domains==('upwork.com',))==expected
    access.add('https://upwork.com/freelance-jobs/custom-search/')
    assert next(s.start_urls for s in worker.active_sources() if s.domains==('upwork.com',))==('https://upwork.com/freelance-jobs/custom-search/',)
