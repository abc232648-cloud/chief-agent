from pathlib import Path
import pytest
from control.files import read_registered
from database.store import Store


def register(store,path):
    store.add_report('fixture',str(path))
    return store.reports()[0]['id']


def test_report_in_private_state_not_application_folder(tmp_path,monkeypatch):
    state=tmp_path/'state';state.mkdir();app=tmp_path/'app';app.mkdir()
    monkeypatch.setenv('CHIEF_STATE_ROOT',str(state));store=Store(state/'db.sqlite')
    folder=state/'notifications/outbox';folder.mkdir(parents=True)
    report=folder/'audit.txt';report.write_text('Synthetic audit')
    identity=register(store,report)
    assert read_registered(store,app,'reports',identity)==(b'Synthetic audit','.txt')
    identity=register(store,'notifications/outbox/audit.txt')
    assert read_registered(store,app,'reports',identity)[0]==b'Synthetic audit'


def test_other_installation_and_private_files_remain_blocked(tmp_path,monkeypatch):
    state=tmp_path/'state';state.mkdir();app=tmp_path/'app';app.mkdir()
    monkeypatch.setenv('CHIEF_STATE_ROOT',str(state));store=Store(state/'db.sqlite')
    for file in (tmp_path/'other/logs/report.txt',state/'secret.txt'):
        file.parent.mkdir(parents=True,exist_ok=True);file.write_text('not a report')
        with pytest.raises(PermissionError):read_registered(store,app,'reports',register(store,file))


def test_intermediate_symlink_blocked(tmp_path,monkeypatch):
    monkeypatch.setenv('CHIEF_STATE_ROOT',str(tmp_path));store=Store(tmp_path/'db.sqlite')
    real=tmp_path/'elsewhere';real.mkdir();(real/'x.txt').write_text('x')
    try:(tmp_path/'logs').symlink_to(real,target_is_directory=True)
    except OSError:pytest.skip('Symlink creation unavailable on this host')
    with pytest.raises(PermissionError):read_registered(store,tmp_path,'reports',register(store,tmp_path/'logs/x.txt'))


def test_bad_download_displays_error_instead_of_saving_json(dashboard):
    from playwright.sync_api import sync_playwright, expect
    from browser_navigation import navigate
    file=dashboard.app.ROOT/'secret.txt';file.write_text('private fixture')
    dashboard.store.add_report('Blocked fixture',str(file))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page();downloads=[]
        page.on('download',lambda d:downloads.append(d))
        page.goto(dashboard.url);navigate(page,'reports')
        page.get_by_role('link',name='Download TXT',exact=True).click()
        expect(page.locator('#reportPreview')).to_contain_text('outside its permitted storage area')
        assert downloads==[]
        browser.close()
