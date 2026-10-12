import json
import pytest
from domains.farming import clarifications as c, bookkeeping as books
from tests.test_farm_bookkeeping import entry
from tests.test_farm_clarifications import create, respond, accept, feed


@pytest.mark.parametrize('kind,label,answer', [
    ('SALE','Incoming payment','Owner confirms money received'),
    ('EXPENSE_CLAIM','Outgoing payment','Owner confirms payment made'),
])
def test_payment_wording_matches_direction_without_changing_authority(dashboard,kind,label,answer):
    d=dashboard; owner=d.credentials['principal']; parent=entry(kind=kind)
    books.append(d.store,owner,parent)
    claim=entry(kind='PAYMENT_CLAIM',reference=parent['event_id'],amount_minor=100,receipt_ref='synthetic-receipt')
    books.append(d.store,owner,claim);item=create(d,'transfer')
    current=c.overview(d.store,owner)['items'][0]
    assert current['definition']['label']==label
    assert current['definition']['options']['confirmed']==answer
    respond(d,item,'confirmed');accept(d,item)
    with d.store._connect() as con:
        confirmations=[r['payload'] for r in books.rows(con) if r['payload']['kind']=='CONFIRM_PAYMENT']
    assert len(confirmations)==1 and confirmations[0]['reference']==claim['event_id']


@pytest.mark.parametrize('category,allowed',[('EGG_SALES',True),('BIRD_SALES',False),('OTHER',False)])
def test_egg_answer_only_offered_for_explicit_egg_sales(dashboard,category,allowed):
    d=dashboard;owner=d.credentials['principal']
    sale=entry(details={'category':category,'contact_id':None,'due_on':None,'items':[]})
    books.append(d.store,owner,sale);item=create(d,'price')
    current=c.overview(d.store,owner)['items'][0]
    assert ('small' in current['definition']['options']) is allowed
    data={'amount_minor':sale['amount_minor'],'currency':sale['currency']}
    if allowed: respond(d,item,'small',data)
    else:
        with pytest.raises(ValueError,match='listed'):respond(d,item,'small',data)


def test_version_one_definition_and_answer_history_remain_usable(dashboard):
    d=dashboard;feed(d);item=create(d,'feed');owner=d.credentials['principal']
    with d.store._connect() as con:
        row=con.execute('SELECT id,data_json FROM domain_records WHERE kind=?',(c.KIND,)).fetchone()
        saved=json.loads(row[1]);saved['version']=1;saved['template_definition']['label']='Historical feed question'
        con.execute('UPDATE domain_records SET data_json=? WHERE id=?',(json.dumps(saved),row[0]))
    respond(d,item,'unknown',notes='Keep original question')
    current=c.overview(d.store,owner)['items'][0]
    assert current['template_version']==1 and current['definition']['label']=='Historical feed question'
    assert current['history'][0]==saved and current['answer']['notes']=='Keep original question'
