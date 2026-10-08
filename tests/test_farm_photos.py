import base64
import struct
import uuid
import zlib
import pytest
from domains.farming import photos,staff,tasks
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_staff import event
from tests.test_farm_tasks import create_task, staff as task_staff
from tests.test_identity_http import request


def png(width=1,height=1):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\x12\x34\x56\xff'))+chunk(b'IEND',b'')


def test_private_photo_roundtrip_retry_integrity_and_scope(dashboard):
    d=dashboard;s=IdentityService(d.store);owner=d.credentials['principal']
    uid=s.create_user(owner,'photo-worker',PASSWORD,'Worker',('farming',))
    raw,worker=s.login('photo-worker',PASSWORD)
    incident=event();staff.append(d.store,worker,incident)
    p={'event_id':str(uuid.uuid4()),'work_id':incident['event_id'],'image_base64':base64.b64encode(png()).decode()}
    assert photos.append(d.store,worker,p)['status']=='RECORDED'
    assert photos.append(d.store,worker,p)['status']=='ALREADY_RECORDED'
    assert 'image_base64' not in photos.listing(d.store,worker,incident['event_id'])[0]
    assert request(d,'/api/farm/photos/'+p['event_id'],raw=raw)[2]==png()
    s.create_user(owner,'other-photo-worker',PASSWORD,'Worker',('farming',))
    other,_=s.login('other-photo-worker',PASSWORD)
    assert request(d,'/api/farm/photos/'+p['event_id'],raw=other)[0]==403
    assert request(d,'/api/farm/photos/'+p['event_id'])[0]==401
    s.disable_user(owner,uid)
    assert request(d,'/api/farm/photos/'+p['event_id'],raw=raw)[0]==401


def test_photo_backend_accepts_authoritative_field_task_ids_only_in_scope(dashboard):
    d=dashboard
    _,worker_id,_,_,manager_raw,manager,worker_raw,worker,other,_,_=task_staff(d)
    task=create_task(worker_id);tasks.append(d.store,manager,task)
    payload={'event_id':str(uuid.uuid4()),'work_id':task['task_id'],'image_base64':base64.b64encode(png()).decode()}
    code,_,receipt=request(d,'/api/farm/photos','POST',payload,worker_raw)
    assert code==200 and receipt['status']=='RECORDED'
    assert receipt['record']['id']==payload['event_id'] and receipt['record']['work_id']==task['task_id']
    assert receipt['record']['sha256'] and receipt['record']['bytes']==len(png())
    code,_,listing=request(d,'/api/farm/photos?work_id='+task['task_id'],raw=worker_raw)
    assert code==200 and listing==[receipt['record']]
    with pytest.raises(PermissionError):photos.listing(d.store,other,task['task_id'])


@pytest.mark.parametrize('raw',[b'<svg onload="alert(1)"></svg>',png()+b'extra',png(1600),b'\x89PNG\r\n\x1a\n',png()[:-1]+b'x'])
def test_invalid_photos_rejected(raw):
    with pytest.raises(ValueError):photos.normalize_png(base64.b64encode(raw).decode())


def test_browser_upload_converts_to_bounded_metadata_free_png(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;staff.append(d.store,d.credentials['principal'],event())
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            upload=page.locator('#farmStaffItems input[type=file]').first
            upload.set_input_files({'name':'synthetic.png','mimeType':'image/png','buffer':png()})
            expect(page.locator('#farmStaffItems')).to_contain_text('Photo attached. Metadata was not retained.')
            page.get_by_role('button',name='View photos',exact=True).first.click()
            expect(page.locator('#farmStaffItems img')).to_have_count(1)
            page.wait_for_function("()=>document.querySelector('#farmStaffItems img')?.naturalWidth>0")
        finally:browser.close()
