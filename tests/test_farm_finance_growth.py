"""Synthetic measured report growth, not installed-host capacity certification."""
import json
import time
import tracemalloc
import sqlite3
from contextlib import contextmanager
import pytest
from domains.farming import bookkeeping as books, finance
from tests.test_farm_bookkeeping import entry


@pytest.mark.parametrize('size',[100,1000,10000])
def test_report_full_totals_and_measured_resources(dashboard,size):
    d=dashboard;owner=d.credentials['principal']
    with d.store._connect() as con:
        for index in range(size):
            p=entry(event_id=f'growth-sale-{index:08d}',amount_minor=12345)
            row={'version':1,'payload':p,'actor_id':owner.id,'session_id':owner.session_id,'received_at':'2026-01-01T00:00:00Z','status':'HUMAN_REPORTED'}
            con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',('farming',books.KIND,json.dumps(row),time.time()))
            receipt={'operation':'receipt','event_id':f'growth-receipt-{index:08d}','reference':p['event_id'],'image_base64':'synthetic-placeholder','sha256':'0'*64,'actor_id':owner.id,'received_at':'2026-01-01T00:00:00Z'}
            con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',('farming',finance.RECEIPTS,json.dumps(receipt),time.time()))
    tracemalloc.start();start=time.perf_counter()
    result=finance.report(d.store,owner)
    elapsed=time.perf_counter()-start;_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert result['summaries']['NGN']['sales']==result['summaries']['NGN']['receivable']==str(size*12345)
    assert result['record_count']==size and len(result['records'])==100
    assert len(result['balances'])==len(result['receipts'])==100
    assert len(json.dumps(result).encode())<150000  # This synthetic fixture's response budget.
    assert all('image_base64' not in row for row in result['receipts'])
    if size>100:
        next_page=finance.report(d.store,owner,offset=100)
        assert next_page['revision']==result['revision'] and next_page['summaries']==result['summaries']
        assert not {r['payload']['event_id'] for r in result['records']} & {r['payload']['event_id'] for r in next_page['records']}
    print(json.dumps({'measurement':'farm_finance_report','records':size,'receipt_metadata_rows':size,'seconds':round(elapsed,4),'peak_traced_bytes':peak,'json_bytes':len(json.dumps(result).encode()),'receipt_bytes':'metadata placeholders; not an image-decode benchmark'}))


def test_finance_write_failure_rolls_back_and_same_id_can_retry(dashboard,monkeypatch):
    d=dashboard;owner=d.credentials['principal'];payload=entry();original=d.store._connect
    inserted=False
    class Connection:
        def __init__(self,con):self.con=con
        def execute(self,sql,*args):
            nonlocal inserted
            if 'INSERT INTO human_security_events' in sql:
                assert inserted, 'Fault must occur after the financial row was inserted.'
                raise sqlite3.OperationalError('database or disk is full')
            result=self.con.execute(sql,*args)
            if 'INSERT INTO domain_records' in sql:inserted=True
            return result
        def __getattr__(self,name):return getattr(self.con,name)
    @contextmanager
    def failing():
        with original() as con:yield Connection(con)
    with monkeypatch.context() as patch:
        patch.setattr(d.store,'_connect',failing)
        with pytest.raises(sqlite3.OperationalError):books.append(d.store,owner,payload)
    with original() as con:assert books.rows(con)==[]
    assert books.append(d.store,owner,payload)['status']=='RECORDED'
    assert books.append(d.store,owner,payload)['status']=='ALREADY_RECORDED'


def test_statement_fetches_all_pages_without_truncating_balances(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;owner=d.credentials['principal']
    with d.store._connect() as con:
        for index in range(101):
            row={'version':1,'payload':entry(counterparty=f'Statement party {index:03d}'),
                 'actor_id':owner.id,'session_id':owner.session_id,'received_at':'2026-01-01T00:00:00Z','status':'HUMAN_REPORTED'}
            con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',('farming',books.KIND,json.dumps(row),time.time()))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            expect(page.locator('#financeLedger')).not_to_contain_text('Statement party 000')
            page.get_by_role('button',name='Prepare statement',exact=True).click()
            expect(page.locator('#financePrint')).to_contain_text('Statement party 000')
            expect(page.locator('#financePrint')).to_contain_text('Statement party 100')
            expect(page.locator('#financePrint p')).to_have_count(103)
        finally:browser.close()


def test_daily_brief_keeps_overdue_debt_beyond_first_page(dashboard):
    from domains.farming import brief
    d=dashboard;owner=d.credentials['principal']
    overdue=entry(details={'category':'OTHER','contact_id':None,'due_on':'2025-01-01','items':[]})
    with d.store._connect() as con:
        for payload in [overdue]+[entry() for _ in range(100)]:
            row={'version':1,'payload':payload,'actor_id':owner.id,'session_id':owner.session_id,
                 'received_at':'2026-01-01T00:00:00Z','status':'HUMAN_REPORTED'}
            con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',('farming',books.KIND,json.dumps(row),time.time()))
    assert overdue['event_id'] not in {r['id'] for r in finance.report(d.store,owner)['balances']}
    view=brief.overview(d.store,owner)
    assert overdue['event_id'] in {a['id'] for a in view['alerts'] if a['type']=='OVERDUE_FINANCE'}
