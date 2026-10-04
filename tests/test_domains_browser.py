from application.composition import default_registry
from playwright.sync_api import sync_playwright,expect
from browser_navigation import navigate
from domains.runtime import DomainRuntime
from worker.runner import run_once


def test_farm_agent_has_minimal_workspace_and_preserves_worker_records(dashboard):
    runtime=DomainRuntime(dashboard.store, registry=default_registry())
    runtime.queue('farming','record_soil_test',{'plot':'Fixture plot','sample_date':'2026-01-01','ph':6.4,'source':'Test source','confirmed':True})
    assert run_once(runtime)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(dashboard.url);navigate(page,'domains')
        expect(page.locator('#domainList')).to_contain_text('Farm Agent')
        assert page.locator('#domainList input').count()==0
        page.locator('#domainList [data-action="open-agent"][data-id="farming"]').click()
        expect(page.locator('#workspaceName')).to_have_text('Farm Agent')
        # The supplied Farm pilot already has a real records page, not the empty placeholder.
        expect(page.locator('#agentHome')).to_contain_text('Poultry records')
        assert len(runtime.overview('farming')['records'])==1
        expect(page.locator('#agentHome')).to_contain_text('record soil',ignore_case=True)
        assert dashboard.store.candidate_facts()==[]
        assert not errors
        browser.close()
