"""Actual production clients, synthetic identities, no external actions."""
import re
import pytest
from playwright.sync_api import expect
from tests.test_a10_paired_clients import paired, sign_in, wait_async, set_network_unavailable
from tests.test_farm_tasks import staff


from browser_conditions import wait_for_browser_condition

def change_person(context, origin, username, prefix='staff'):
    for page in context.pages:
        page.close()
    context.clear_cookies()
    return sign_in(context, origin, prefix, username=username, install_clock=False)[0]


def test_manager_assignment_worker_completion_supervisor_review_owner_visibility(paired):
    d, origin, context = paired
    _, worker_id, _, supervisor_id, *_ = staff(d)
    page = change_person(context, origin, 'task-manager')
    page.get_by_role('link', name='Tasks', exact=True).click()
    page.get_by_role('button', name='New task', exact=True).click(timeout=5000)
    page.get_by_label('Task title', exact=True).fill('A10 water inspection')
    page.get_by_label('Instructions', exact=True).fill('Inspect the synthetic water line.')
    page.get_by_label('Location', exact=False).fill('Synthetic house')
    page.get_by_label(re.compile('^Assign to')).select_option(worker_id)
    page.get_by_label(re.compile(r'^Supervisor \(optional\)')).select_option(supervisor_id)
    with page.expect_response(lambda r: r.url.endswith('/api/farm/tasks') and r.request.method == 'POST') as creation:
        page.get_by_role('button', name='Create task in Chief', exact=True).click()
    assert creation.value.status == 200
    expect(page.get_by_role('heading', name='A10 water inspection', exact=True)).to_be_visible()

    page = change_person(context, origin, 'task-other')
    page.get_by_role('link', name='Tasks', exact=True).click()
    expect(page.get_by_role('heading', name='My tasks', exact=True)).to_be_visible()
    expect(page.get_by_role('heading', name='A10 water inspection', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='New task', exact=True)).to_have_count(0)
    assert page.evaluate("async()=> (await (await fetch('/api/farm/tasks')).json()).tasks.length") == 0

    page = change_person(context, origin, 'task-worker')
    page.get_by_role('link', name='Tasks', exact=True).click()
    page.get_by_role('button', name='Acknowledge', exact=True).click()
    expect(page.get_by_text('Acknowledged', exact=True)).to_be_visible()
    page.get_by_role('button', name='Start task', exact=True).click()
    expect(page.get_by_text('In progress', exact=True)).to_be_visible()
    page.get_by_role('button', name='Submit completion', exact=True).click()
    page.get_by_label('Completion note to send to Chief', exact=True).fill('Inspected synthetic water line.')
    page.locator('.task-intent-panel').get_by_role('button', name='Submit completion', exact=True).click()
    expect(page.locator('.task-status')).to_have_text('Awaiting verification')
    expect(page.get_by_role('button', name='Verify', exact=True)).to_have_count(0)

    page = change_person(context, origin, 'task-supervisor')
    page.get_by_role('link', name='Tasks', exact=True).click()
    expect(page.get_by_role('button', name='New task', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Verify', exact=True).click()
    page.get_by_label('Verification note', exact=True).fill('Synthetic completion checked.')
    page.get_by_role('button', name='Verify completion', exact=True).click()
    expect(page.locator('.task-status')).to_have_text('Verified')
    result = page.evaluate("async()=> (await (await fetch('/api/farm/tasks')).json()).tasks[0]")
    assert result['status'] == 'VERIFIED'
    assert result['verification_note'] == 'Synthetic completion checked.'

    page = change_person(context, origin, 'fixture-owner', 'owner')
    panel = page.get_by_role('region', name='Authoritative Farm operations')
    expect(panel).to_contain_text('Chief tasks: 1')
    assert page.evaluate("async()=> (await (await fetch('/api/farm/tasks')).json()).tasks[0].status") == 'VERIFIED'


def test_actual_photo_upload_integrity_and_cross_worker_denial(paired):
    from tests.test_farm_tasks import create_task
    from tests.test_farm_photos import png
    from domains.farming import tasks
    d, origin, context = paired
    _, worker_id, _, _, _, manager, *_ = staff(d)
    creation = create_task(worker_id, evidence_required=['PHOTO'])
    tasks.append(d.store, manager, creation)
    page = change_person(context, origin, 'task-worker')
    page.get_by_role('link', name='Tasks', exact=True).click()
    with page.expect_response(lambda r: r.url.endswith('/api/farm/photos') and r.request.method == 'POST') as upload:
        page.locator('input[type=file]').set_input_files({'name':'synthetic.png','mimeType':'image/png','buffer':png()})
    assert upload.value.status == 200, upload.value.json()
    photo = upload.value.json()['record']
    assert photo['work_id'] == creation['task_id']
    actual = page.evaluate("""async id => {const r=await fetch('/api/farm/photos/'+id);const data=await r.arrayBuffer();
        const digest=await crypto.subtle.digest('SHA-256',data);
        return {bytes:data.byteLength,sha256:Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,'0')).join('')}}""", photo['id'])
    assert actual == {'bytes':photo['bytes'],'sha256':photo['sha256']}
    page.get_by_role('link', name='More', exact=True).click()
    expect(page.get_by_text('No queued records for this authenticated identity.', exact=True)).to_be_visible()
    page = change_person(context, origin, 'task-other')
    assert page.evaluate("async id=>(await fetch('/api/farm/photos/'+id)).status",photo['id']) == 403
    assert tasks.overview(d.store, manager)['tasks'][0]['status'] == 'ASSIGNED'


def fill_report(page, origin):
    page.goto(origin + '/staff/#/report')
    page.get_by_placeholder('e.g. House 1', exact=True).fill('Synthetic house')
    page.get_by_placeholder('Describe only what you saw, counted, measured, or were told to record.').fill('Private queued observation A10')


def test_offline_queue_is_not_visible_or_replayed_by_next_worker(paired):
    d, origin, context = paired
    staff(d)
    page = change_person(context, origin, 'task-worker')
    fill_report(page, origin)
    set_network_unavailable(d, context, True)
    page.get_by_role('button',name='Send report to Chief',exact=True).click()
    expect(page.get_by_text('Stored on this device',exact=True)).to_be_visible()
    page.close()
    set_network_unavailable(d, context, False)
    page = change_person(context, origin, 'task-other')
    page.get_by_role('link',name='More',exact=True).click()
    expect(page.get_by_text('No queued records for this authenticated identity.',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='Send queued to Chief',exact=True)).to_be_disabled()
    assert page.evaluate("async()=> (await (await fetch('/api/farm/worker-reports')).json()).reports.length") == 0
    page = change_person(context, origin, 'task-worker')
    page.get_by_role('link',name='More',exact=True).click()
    page.get_by_role('button',name='Send queued to Chief',exact=True).click()
    expect(page.get_by_text('No queued records for this authenticated identity.',exact=True)).to_be_visible()
    reports = page.evaluate("async()=> (await (await fetch('/api/farm/worker-reports')).json()).reports")
    assert len(reports) == 1
    assert reports[0]['payload']['summary'] == 'Private queued observation A10'


def test_storage_failure_never_claims_saved_report(paired):
    d, origin, context = paired
    page, _ = sign_in(context,origin,'staff',install_clock=False)
    fill_report(page,origin)
    # Browser storage fault only. API/database remain real; no fake success receipt.
    page.evaluate("() => {IDBDatabase.prototype.transaction = function(){throw new DOMException('Synthetic quota failure','QuotaExceededError')}}")
    set_network_unavailable(d, context, True)
    page.get_by_role('button',name='Send report to Chief',exact=True).click()
    expect(page.get_by_role('alert').first).to_be_visible()
    expect(page.get_by_text('Stored on this device',exact=True)).to_have_count(0)
    expect(page.get_by_text('Received by Chief',exact=True)).to_have_count(0)
    expect(page.get_by_placeholder('Describe only what you saw, counted, measured, or were told to record.')).to_have_value('Private queued observation A10')
    set_network_unavailable(d, context, False)


def test_unknown_offline_payload_version_is_retained_without_submission(paired):
    d, origin, context = paired
    page, _ = sign_in(context, origin, 'staff',install_clock=False)
    fill_report(page, origin)
    set_network_unavailable(d, context, True)
    page.get_by_role('button',name='Send report to Chief',exact=True).click()
    expect(page.get_by_text('Stored on this device',exact=True)).to_be_visible()
    # Simulate a queue written by a future client. Do not reinterpret its payload.
    page.evaluate("""async () => {
      const request = r => new Promise((resolve,reject)=>{r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});
      const db = await request(indexedDB.open('chief_staff_pwa'));
      try {
        const rows = await request(db.transaction('queue').objectStore('queue').getAll());
        if(rows.length!==1 || !rows[0].encryptedPayload)throw new Error('Expected one encrypted queued report');
        const row=rows[0], saved=await request(db.transaction('crypto_keys').objectStore('crypto_keys').get(row.namespace));
        const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
        const aad=v=>new TextEncoder().encode(`CHIEF_OFFLINE:${row.environment}:${row.ownerUserId}:v${v}:queue:${row.id}:${row.kind}`);
        const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes(row.encryptedPayload.iv),additionalData:aad(row.payloadVersion)},saved.key,bytes(row.encryptedPayload.ciphertext));
        const iv=crypto.getRandomValues(new Uint8Array(12));
        const cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:aad(99)},saved.key,plain);
        const b64=b=>btoa(String.fromCharCode(...new Uint8Array(b)));
        const tx=db.transaction('queue','readwrite');
        tx.objectStore('queue').put({...row,payloadVersion:99,encryptedPayload:{...row.encryptedPayload,iv:b64(iv),ciphertext:b64(cipher)}});
        await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error)});
      } finally {db.close()}
    }""")
    page.close()
    set_network_unavailable(d, context, False)
    page = context.new_page()
    page.goto(origin+'/staff/#/more')
    page.get_by_role('button',name='Send queued to Chief',exact=True).click()
    expect(page.locator('.offline-record')).to_contain_text('needs attention')
    assert page.evaluate("async()=> (await (await fetch('/api/farm/worker-reports')).json()).reports.length") == 0
    assert page.evaluate("""() => new Promise((resolve,reject)=>{const r=indexedDB.open('chief_staff_pwa');
      r.onsuccess=()=>{const db=r.result;const q=db.transaction('queue').objectStore('queue').getAll();
        q.onsuccess=()=>{db.close();resolve(q.result.length===1 && q.result[0].payloadVersion===99 && !!q.result[0].encryptedPayload)};
        q.onerror=()=>reject(q.error)};r.onerror=()=>reject(r.error)})""")


def test_actual_service_worker_update_preserves_queued_report(paired, tmp_path):
    import shutil
    d, origin, context = paired
    page, _ = sign_in(context,origin,'staff',install_clock=False)
    wait_async(page, "async()=>!!(await navigator.serviceWorker.getRegistration('/staff/'))?.active")
    fill_report(page,origin)
    set_network_unavailable(d, context, True)
    page.get_by_role('button',name='Send report to Chief',exact=True).click()
    expect(page.get_by_text('Stored on this device',exact=True)).to_be_visible()
    page.close()
    set_network_unavailable(d, context, False)
    # Serve a new static-shell version, retaining the actual built application.
    page = context.new_page()
    page.goto(origin+'/staff/#/more')
    expect(page.locator('.offline-record')).to_have_count(1)
    wait_async(page, "async()=>!!(await navigator.serviceWorker.getRegistration('/staff/'))?.active")
    wait_for_browser_condition(page, '!!navigator.serviceWorker.controller')
    bundle = tmp_path/'next-staff-build'
    shutil.copytree(d.a10_bundles['/staff/'],bundle)
    sw = bundle/'sw.js'
    sw.write_text(re.sub(r"const BUILD_ID = '[^']*';", "const BUILD_ID = 'a10-update';",sw.read_text()))
    d.a10_bundles['/staff/'] = bundle
    page.evaluate("async()=>{const r=await navigator.serviceWorker.getRegistration('/staff/');await r.update()}")
    try:
        expect(page.get_by_role('button',name='Apply available app update',exact=True)).to_be_visible(timeout=15000)
    except AssertionError:
        raise AssertionError(page.evaluate("async()=>({registrations:(await navigator.serviceWorker.getRegistrations()).map(r=>({scope:r.scope,waiting:r.waiting?.state,installing:r.installing?.state,active:r.active?.state})),next:(await(await fetch('/staff/sw.js')).text()).includes('a10-update')})")) from None
    with page.expect_navigation(wait_until='domcontentloaded'):
        page.get_by_role('button',name='Apply available app update',exact=True).click(timeout=15000)
    expect(page.locator('.offline-record')).to_have_count(1)
    wait_async(page, "async()=> (await caches.keys()).includes('chief-staff-shell-a10-update')")
    page.get_by_role('button',name='Send queued to Chief',exact=True).click()
    expect(page.get_by_text('No queued records for this authenticated identity.',exact=True)).to_be_visible()
    assert page.evaluate("async()=> (await (await fetch('/api/farm/worker-reports')).json()).reports.length") == 1


def test_reassigned_task_photo_is_retained_but_not_uploaded(paired):
    from domains.farming import tasks
    from tests.test_farm_tasks import create_task, event
    from tests.test_farm_photos import png
    d, origin, context = paired
    _, worker_id, other_id, _, _, manager, *_ = staff(d)
    creation = create_task(worker_id,evidence_required=['PHOTO'])
    tasks.append(d.store,manager,creation)
    page = change_person(context,origin,'task-worker')
    page.get_by_role('link',name='Tasks',exact=True).click()
    expect(page.locator('input[type=file]')).to_have_count(1)
    set_network_unavailable(d, context, True)
    page.locator('input[type=file]').set_input_files({'name':'synthetic.png','mimeType':'image/png','buffer':png()})
    expect(page.get_by_role('status')).to_contain_text('Photo stored on this device.')
    page.close()
    tasks.append(d.store,manager,event('REASSIGN',creation['task_id'],creation['event_id'],assigned_to=other_id,supervisor_id=None,reason='Synthetic reassignment'))
    set_network_unavailable(d, context, False)
    page = context.new_page()
    page.goto(origin+'/staff/#/more')
    page.get_by_role('button',name='Send queued to Chief',exact=True).click()
    expect(page.locator('.offline-record')).to_contain_text('needs attention')
    with d.store._connect() as con:
        assert con.execute("SELECT count(*) FROM domain_records WHERE domain='farming' AND kind='farm_photo_v1'").fetchone()[0] == 0
