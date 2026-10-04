from datetime import datetime, timezone
import json
import pytest
from capabilities.contracts import Node,Mode
from database.component_state import ComponentState
from domains.farming import setup,journal,assistant,live_ai,bookkeeping
from tests.test_farm_setup import entity
from tests.test_farm_journal import record
from tests.test_farm_bookkeeping import entry


def test_lagos_opening_date_accepts_after_midnight_observation(dashboard):
    d=dashboard;p=d.credentials['principal'];e={**entity(),'opened_on':'2026-01-01'}
    setup.append(d.store,p,e)
    result=journal.append(d.store,p,record(entity_id=e['entity_id'],location=e['name'],observed_at='2026-01-01T00:30:00+01:00'))
    assert result['record']['payload']['observed_at'].startswith('2025-12-31T23:30:00')
    with pytest.raises(ValueError,match='predates'):
        journal.append(d.store,p,record(entity_id=e['entity_id'],location=e['name'],observed_at='2025-12-31T23:59:00+01:00'))


def test_entity_today_uses_lagos_calendar_not_utc(dashboard,monkeypatch):
    monkeypatch.setattr(setup,'utc_now',lambda:datetime(2026,1,1,23,30,tzinfo=timezone.utc))
    setup.append(dashboard.store,dashboard.credentials['principal'],{**entity(),'opened_on':'2026-01-02'})
    with pytest.raises(ValueError):setup.append(dashboard.store,dashboard.credentials['principal'],{**entity(),'opened_on':'2026-01-03'})


def test_ai_context_is_explicitly_bounded_not_a_false_total(dashboard,monkeypatch):
    d=dashboard;p=d.credentials['principal']
    item={'id':'synthetic-task','kind':'TASK','state':'OPEN','text':'測'*2000,'due_at':None,'overdue':False}
    monkeypatch.setattr(assistant.staff,'overview',lambda *args:{'items':[item]*1000,'open_count':1000,'daily_report_schedule':'NOT_CONFIGURED'})
    _,data=assistant.context(d.store,p)
    assert data['context_is_excerpt'] and data['unresolved_work_count']==1000
    assert len(data['tasks'])==10 and all(r['text_truncated'] and len(r['text'])==180 for r in data['tasks'])
    assert len(json.dumps(data).encode())<16000
    answer=assistant.ask(d.store,p,{'question':'How many tasks are pending?'})
    assert '1000 unresolved' in answer['answer']


def test_declared_ai_dependencies_are_all_guarded_without_application_imports():
    from application.composition import default_catalogs
    graph=default_catalogs().capabilities.graph
    guarded={Node('component',n) for n in live_ai.CONTROL_COMPONENTS}|{Node('capability',live_ai.CAPABILITY)}
    assert set(graph.dependencies(Node('component','farm-assistant'))) <= guarded


@pytest.mark.parametrize('node',[Node('component','farm-assistant'),Node('capability','farming.assistant.ask'),Node('component','chief.model_registry')])
def test_ai_component_denial_uses_persisted_state(dashboard,node):
    d=dashboard;p=d.credentials['principal']
    with d.store._connect() as con:ComponentState(d.store).put(con,node,Mode.DISABLED,actor=p.id,reason='Synthetic disable')
    with pytest.raises(PermissionError):live_ai.guard(d.store,p)


def test_finance_reference_selection_is_visible_and_never_saves(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;p=d.credentials['principal'];sale=entry(kind='SALE')
    bookkeeping.append(d.store,p,sale)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)));page.goto(d.url+'/work')
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            page.wait_for_function("() => document.getElementById('farmMoneyHistory').children.length>0 || document.getElementById('workError').textContent")
            assert not errors,errors
            assert page.locator('#workError').inner_text()==''
            page.get_by_text('Financial history',exact=True).click()
            page.locator('#farmMoneyHistory').get_by_role('button',name='Use as reference',exact=True).click()
            expect(page.locator('#farmMoneyReference')).to_have_value(sale['event_id'])
            assert len(bookkeeping.overview(d.store,p)['records'])==1
        finally:browser.close()
