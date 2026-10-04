from control.agents import AgentControls
import json
import time
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest
from agents.registry import AgentRegistry
from application.composition import default_registry
from database.store import Store
from domains.contracts import AgentDefinition, DomainAction, DomainDefinition
from domains.runtime import DomainRuntime
from domains.storage import DomainStorage, deliver_due_reminders
from security.permissions import worker_context, current_context
from worker.runner import run_once


@pytest.fixture
def runtime(tmp_path):
    return DomainRuntime(Store(tmp_path/'worker.db'), registry=default_registry())


def soil(**changes):
    return dict(plot='North field', sample_date='2026-01-01', ph=6.4, source='Measured test fixture', confirmed=True, **changes)


def execute(runtime, action, payload, domain='farming'):
    queued=runtime.queue(domain, action, payload)
    assert run_once(runtime)
    return next(c for c in runtime.store.commands() if c['id']==queued['command_id'])


def test_farm_records_are_separate_and_survive_restart(runtime):
    before=runtime.store.candidate_facts()
    command=execute(runtime,'record_soil_test',soil())
    assert command['status']=='COMPLETED'
    other=DomainRuntime(Store(runtime.store.path), registry=default_registry())
    records=other.overview('farming')['records']
    assert records[0]['data']['ph']==6.4
    assert records[0]['data']['evidence_status']=='USER_RECORDED'
    assert runtime.store.candidate_facts()==before
    assert current_context() is None


@pytest.mark.parametrize('key,value', [('ph',-1),('ph',15),('ph',True),('ph','nan'),('confirmed',False),('source',''),('sample_date','2999-01-01')])
def test_invalid_soil_is_failed_without_record(runtime,key,value):
    payload=soil();payload[key]=value
    command=execute(runtime,'record_soil_test',payload)
    assert command['status']=='FAILED'
    assert runtime.overview('farming')['records']==[]


def test_reminder_delivered_once_after_restart(runtime):
    due=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    command=execute(runtime,'schedule_reminder',{'title':'Check test plot','due_at':due})
    assert command['status']=='COMPLETED'
    with runtime.store._connect() as con:con.execute('UPDATE domain_reminders SET due_at=?',(time.time()-1,))
    assert deliver_due_reminders(Store(runtime.store.path), AgentControls(Store(runtime.store.path), default_registry()))==1
    assert deliver_due_reminders(Store(runtime.store.path), AgentControls(Store(runtime.store.path), default_registry()))==0
    assert [n['body'] for n in runtime.store.notifications()]==['Check test plot']


def test_cancelled_reminder_not_delivered(runtime):
    due=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    execute(runtime,'schedule_reminder',{'title':'Cancel this','due_at':due})
    reminder=runtime.overview('farming')['reminders'][0]
    assert execute(runtime,'cancel_reminder',{'reminder_id':reminder['id']})['status']=='COMPLETED'
    with runtime.store._connect() as con:con.execute('UPDATE domain_reminders SET due_at=0')
    assert deliver_due_reminders(runtime.store, AgentControls(runtime.store, default_registry()))==0


@pytest.mark.parametrize('domain,action',[('unknown','record_soil_test'),('farming','submit_application'),('farming','pay_money'),('farming','discover_jobs')])
def test_domain_cannot_queue_other_actions(runtime,domain,action):
    with pytest.raises((ValueError,PermissionError)):runtime.queue(domain,action,{})
    assert runtime.store.commands()==[]


def test_missing_capability_and_cross_domain_storage(runtime):
    domain,agent=runtime.registry.resolve('farming')
    runtime.registry.agents[agent.id]=replace(agent,capabilities=frozenset())
    with pytest.raises(PermissionError):runtime.queue('farming','record_soil_test',soil())
    with worker_context(agent) as context:
        with runtime.store._connect() as con:
            rid=con.execute("INSERT INTO domain_reminders(domain,title,due_at) VALUES('other','Other',0)").lastrowid
        with pytest.raises(ValueError):DomainStorage(runtime.store,context).cancel_reminder(rid)
        assert DomainStorage(runtime.store,context).overview()['reminders']==[]


def test_job_policy_and_browser_reject_farm_context():
    from worker.action_gate import PolicyGate,ActionRequest
    from policy.rules import Decision
    from browser.playwright_reader import PlaywrightReader
    _,agent=default_registry().resolve('farming')
    with worker_context(agent):
        assert PolicyGate().decide(ActionRequest('discover_jobs',{})).decision==Decision.BLOCK
        with pytest.raises(PermissionError):PlaywrightReader().read_url('https://example.org')
    assert current_context() is None


def test_third_domain_uses_same_dispatch_and_rolls_back_failures(runtime):
    def record(storage,payload):
        storage.record('sample',{'value':'separate'})
        if payload['fail']:raise ValueError('Fixture failure')
        return {'status':'COMPLETED'}
    action=DomainAction('record','Record','demo.records.write',({'name':'fail'},),record)
    runtime.registry.register(DomainDefinition('demo','Demo','test',(action,)),AgentDefinition('demo-worker','demo',frozenset({'demo.records.write','demo.records.read'})))
    assert execute(runtime,'record',{'fail':True},'demo')['status']=='FAILED'
    assert runtime.overview('demo')['records']==[]
    assert execute(runtime,'record',{'fail':False},'demo')['status']=='COMPLETED'
    assert len(runtime.overview('demo')['records'])==1
    assert runtime.overview('farming')['records']==[]


def test_global_forbidden_cannot_be_registered_around(runtime):
    action=DomainAction('pay_money','Pay','demo.pay',(),lambda *a:pytest.fail('Must not execute'))
    runtime.registry.register(DomainDefinition('demo','Demo','test',(action,)),AgentDefinition('demo','demo',frozenset({'demo.pay'})))
    with pytest.raises(PermissionError):runtime.queue('demo','pay_money',{})


def test_existing_commands_route_to_jobs(runtime):
    class Gateway:
        def generate(self,request):return SimpleNamespace(text=json.dumps({'summary':'No action fixture','actions':[]}))
    runtime.gateway=Gateway()
    runtime.store.queue_command('Existing job command')
    assert run_once(runtime)
    assert runtime.store.commands()[0]['status']=='NO_ACTION'


def test_payload_cannot_override_domain(runtime):
    payload=soil();payload['domain']='jobs'
    with pytest.raises(ValueError):runtime.queue('farming','record_soil_test',payload)
