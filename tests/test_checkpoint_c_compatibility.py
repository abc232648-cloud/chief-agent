from contextlib import closing
import json
import sqlite3
from urllib.request import Request,urlopen
from itertools import product
import pytest
from checkpoint_c_fixture import migrated,unmigrated
from capabilities.contracts import Mode,Node
from control.agents import AgentControls
from domains.runtime import DomainRuntime
from domains.jobs import make_processor
from worker.action_gate import ActionRequest,PolicyGate
from domains.jobs.policy_adapter import JobComparisonGate
from database.migrations import legacy_digest


@pytest.mark.parametrize('enabled,autostart,running',list(product([False,True],repeat=3)))
def test_legacy_startup_boolean_semantics_unchanged(migrated,enabled,autostart,running):
    controls=migrated.controls
    controls.change('jobs',dict(enabled=enabled,autostart=autostart,running=running))
    assert controls.allowed('jobs')==(enabled and running)
    controls.startup()
    assert controls.allowed('jobs')==(enabled and autostart)
    with migrated.store._connect() as con:
        assert con.execute("SELECT outcome FROM agent_runs WHERE domain='jobs'").fetchone()[0]=='INTERRUPTED'


def test_new_controls_do_not_resume_paused_work(migrated):
    assert not migrated.controls.allowed('jobs')
    controls=migrated.services.controls
    node=Node('component','jobs-worker')
    controls.transition(controls.preview(node,Mode.ENABLED),actor='test',reason='test')
    assert not migrated.controls.allowed('jobs')
    assert migrated.controls.claim_command() is None


@pytest.mark.parametrize('injected',[False,True])
def test_default_legacy_farm_dispatch_and_data_remain_usable(migrated,injected):
    runtime=DomainRuntime(migrated.store,registry=migrated.registry,components=migrated.services.controls if injected else None)
    data=runtime.overview('farming')
    assert len(data['records'])==1 and len(data['reminders'])==1
    node=Node('component','farming-recorder')
    controls=migrated.services.controls
    preview=controls.preview(node,Mode.MAINTENANCE)
    assert Node('component','chief.internal_reporting') in preview.affected
    with pytest.raises(ValueError,match='Confirm the affected consumers'):
        controls.transition(preview,actor='test',reason='test')
    controls.transition(preview,actor='test',reason='test',confirmed=True)
    assert not AgentControls(migrated.store,migrated.registry).allowed('farming')
    with pytest.raises(PermissionError):runtime.overview('farming')


def test_job_factory_injects_comparison_gate_and_records_audit(migrated):
    processor=make_processor(migrated.store,object())
    assert isinstance(processor.gate,JobComparisonGate)
    assert type(processor.gate).execute is PolicyGate.execute
    assert processor.application_executor.gate is processor.gate
    assert processor.final_submission_executor.gate is processor.gate
    processor.gate.decide(ActionRequest('submit_application',{}))
    with migrated.store._connect() as con:
        record=con.execute("SELECT status,data_json FROM audit_log WHERE category='policy_comparison'").fetchone()
    assert record['status']=='MATCH'
    assert json.loads(record['data_json'])['candidate']=='ASK'


def test_additive_schema_can_be_read_with_b5_style_sql_but_modes_need_reconciliation(migrated):
    with closing(sqlite3.connect(migrated.store.path)) as con:
        assert legacy_digest(con)==migrated.before
        assert con.execute("SELECT enabled,autostart,running FROM agent_controls WHERE domain='jobs'").fetchone()==(1,0,0)
    # A code rollback ignores new controls: stopping services/reconciliation is mandatory.
    migrated.controls.change('jobs',{'running':True})
    controls=migrated.services.controls;node=Node('component','jobs-worker')
    controls.transition(controls.preview(node,Mode.DISABLED),actor='test',reason='test')
    assert not migrated.controls.allowed('jobs')
    with closing(sqlite3.connect(migrated.store.path)) as con:
        assert con.execute("SELECT enabled AND running FROM agent_controls WHERE domain='jobs'").fetchone()[0]==1


def test_additive_api_and_original_domain_control_api(dashboard,migrated):
    dashboard.app.STORE=migrated.store
    dashboard.secure_store(migrated.store)
    dashboard.app.CONTROL_SERVICES=migrated.services
    def request(path,body=None):
        data=json.dumps(body).encode() if body is not None else None
        with urlopen(Request(dashboard.url+path,data=data,headers={'Content-Type':'application/json'})) as response:
            return json.load(response)
    assert isinstance(request('/api/system-health'),list)
    assert isinstance(request('/api/component-controls'),list)
    body={'kind':'component','id':'jobs-worker','mode':'MAINTENANCE'}
    preview=request('/api/component-controls/preview',body)
    changed=request('/api/component-controls/transition',body|{'token':preview['token'],'reason':'synthetic','confirmed':True})
    assert changed['mode']=='MAINTENANCE'
    with migrated.store._connect() as con:
        assert con.execute("SELECT actor FROM component_modes WHERE id='jobs-worker'").fetchone()[0]==dashboard.credentials['principal'].id
        assert con.execute("SELECT actor FROM audit_log WHERE category='component_control' ORDER BY id DESC LIMIT 1").fetchone()[0]==dashboard.credentials['principal'].id
    legacy=request('/api/agent-controls/jobs',{'running':True})
    assert legacy['status']=='UPDATED'
    assert not migrated.controls.allowed('jobs')
