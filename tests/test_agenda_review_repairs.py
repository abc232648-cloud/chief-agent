"""Synthetic independent review reproductions; candidate source is unchanged."""
from domains.farming import clarifications as c, journal, bookkeeping as books
from tests.test_farm_clarifications import feed, create, respond, accept
from tests.test_farm_journal import record
from tests.test_farm_bookkeeping import entry


def test_unrelated_egg_collection_should_not_invalidate_feed_answer(dashboard):
    d = dashboard
    old = feed(d)
    item = create(d, 'feed')
    respond(d, item, 'correct', {'quantity': '3', 'unit': 'kg', 'observed_at': old['observed_at']})
    journal.append(d.store, d.credentials['principal'], record(kind='eggs_collected', quantity='10', location=old['location']))
    # A different observation must not erase the ability to review this unchanged feed record.
    accept(d, item)


def test_sale_with_line_items_should_have_usable_price_correction(dashboard):
    d = dashboard
    sale = entry(amount_minor=100000, details={'category': 'EGG_SALES', 'contact_id': None, 'due_on': None,
        'items': [{'description': 'Synthetic egg sale', 'amount_minor': 100000}]})
    books.append(d.store, d.credentials['principal'], sale)
    item = create(d, 'price')
    respond(d, item, 'correct', {'amount_minor': 90000, 'currency': 'NGN', 'items': [{'description':'Synthetic egg sale','amount_minor':90000}]})
    # The new UI offers this sale for correction but cannot submit corrected line items.
    accept(d, item)


def test_payment_added_after_answer_requires_fresh_review(dashboard):
    import pytest
    d=dashboard;owner=d.credentials['principal'];sale=entry()
    books.append(d.store,owner,sale);item=create(d,'price')
    respond(d,item,'correct',{'amount_minor':9000,'currency':'NGN'})
    books.append(d.store,owner,entry(kind='PAYMENT_CLAIM',reference=sale['event_id'],amount_minor=1000,receipt_ref='late-synthetic'))
    with pytest.raises(ValueError,match='Source changed'):accept(d,item)
    assert books.overview(d.store,owner)['balances'][0]['amount_minor']==10000


def test_itemised_correction_requires_balanced_nonempty_breakdown(dashboard):
    import pytest
    d=dashboard;owner=d.credentials['principal']
    sale=entry(details={'category':'EGG_SALES','contact_id':None,'due_on':None,'items':[{'description':'Synthetic eggs','amount_minor':10000}]})
    books.append(d.store,owner,sale);item=create(d,'price')
    for items in ([],[{'description':'Synthetic eggs','amount_minor':10000}]):
        with pytest.raises(ValueError):respond(d,item,'correct',{'amount_minor':9000,'currency':'NGN','items':items})
    with d.store._connect() as con:assert len(books.rows(con))==1


def test_browser_itemised_sale_correction(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;owner=d.credentials['principal']
    sale=entry(details={'category':'EGG_SALES','contact_id':None,'due_on':None,'items':[{'description':'Synthetic eggs','amount_minor':10000}]})
    books.append(d.store,owner,sale);create(d,'price')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1280,'height':900});page.goto(d.url)
            page.locator('nav .domainNav[data-domain="farming"] > summary').click()
            page.get_by_role('button',name='Poultry records',exact=True).click()
            host=page.locator('#farmClarifications');host.locator(':scope > summary').click();page.set_viewport_size({'width':390,'height':844});page.locator('#sidebarToggle').click()
            host.locator('details.item > summary').click()
            card=host.locator('details.item');card.get_by_label('Answer',exact=True).select_option('correct')
            card.get_by_label('Sale total (currency amount, not kobo)',exact=True).fill('90')
            card.get_by_label('Item 1 amount',exact=True).fill('90')
            card.get_by_role('button',name='Save answer for review',exact=True).click()
            expect(card.locator(':scope > summary')).to_contain_text('Owner review needed')
            card.locator(':scope > summary').click()
            card.get_by_role('button',name='Accept saved answer',exact=True).click()
            expect(card.locator(':scope > summary')).to_contain_text('Accepted')
            with d.store._connect() as con:
                rows=books.rows(con);sales=[r['payload'] for r in books.active(rows) if r['payload']['kind']=='SALE']
                assert len(sales)==1 and sales[0]['amount_minor']==9000
                assert sales[0]['details']['items'][0]['amount_minor']==9000
                assert rows[0]['payload']==books.validate(sale)
        finally:browser.close()


def test_browser_historical_question_preserves_unit_metadata(dashboard):
    import json
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;old=feed(d);owner=d.credentials['principal']
    journal.append(d.store,owner,record(kind='eggs_collected',quantity='10',location=old['location']))
    item=create(d,'feed')
    # Reproduce an authentic v2 source reference, which covered the whole family.
    with d.store._connect() as con:
        row=con.execute('SELECT id,data_json FROM domain_records WHERE kind=?',(c.KIND,)).fetchone()
        historical=json.loads(row[1]);historical['version']=2
        historical['payload']['source']['revision']=c._digest(journal.read_rows(con))
        con.execute('UPDATE domain_records SET data_json=? WHERE id=?',(json.dumps(historical),row[0]))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1280,'height':900});page.goto(d.url)
            page.locator('nav .domainNav[data-domain="farming"] > summary').click()
            page.get_by_role('button',name='Poultry records',exact=True).click()
            host=page.locator('#farmClarifications');host.locator(':scope > summary').click()
            host.locator('details.item > summary').click()
            expect(host.get_by_label('Unit',exact=True)).to_have_value('kg')
            host.get_by_label('Answer',exact=True).select_option('correct')
            expect(host.get_by_label('Observation time (include timezone, for example +01:00)',exact=True)).to_have_value(journal.validate(old)['observed_at'])
        finally:browser.close()
