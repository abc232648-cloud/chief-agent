from pathlib import Path

import pytest

from update_center.acquisition import AcquisitionError, acquire_release
from update_center.release_trust import MAX_ENVELOPE


class Response:
    def __init__(self, url, payload):
        self.url, self.payload = url, payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def geturl(self):
        return self.url
    def read(self, amount):
        return self.payload[:amount]


def test_acquisition_writes_inert_private_pair(monkeypatch, tmp_path):
    def fake_urlopen(request, timeout):
        url = request.full_url
        if url.endswith('envelope.json'):
            return Response(url, b'{"signed":true}')
        return Response(url, b'synthetic-archive')
    monkeypatch.setattr('update_center.acquisition.urlopen', fake_urlopen)
    result = acquire_release('https://releases.example/chief.zip',
        'https://releases.example/envelope.json', tmp_path,
        allowed_hosts={'releases.example'}, private_storage_confirmed=True)
    assert result['status'] == 'DOWNLOADED_NOT_VERIFIED'
    assert result['activation_authorized'] is False
    assert Path(result['archive']).read_bytes() == b'synthetic-archive'
    assert Path(result['envelope']).read_bytes() == b'{"signed":true}'


@pytest.mark.parametrize('url', [
    'http://releases.example/chief.zip',
    'https://user:secret@releases.example/chief.zip',
    'https://releases.example/chief.zip?token=secret',
    'https://other.example/chief.zip',
])
def test_acquisition_rejects_unapproved_or_secret_bearing_urls(url, tmp_path):
    with pytest.raises(ValueError):
        acquire_release(url, 'https://releases.example/envelope.json', tmp_path,
            allowed_hosts={'releases.example'}, private_storage_confirmed=True)


def test_acquisition_rechecks_redirect_host(monkeypatch, tmp_path):
    monkeypatch.setattr('update_center.acquisition.urlopen',
        lambda request, timeout: Response('https://redirect.example/payload', b'x'))
    with pytest.raises(ValueError):
        acquire_release('https://releases.example/chief.zip',
            'https://releases.example/envelope.json', tmp_path,
            allowed_hosts={'releases.example'}, private_storage_confirmed=True)


def test_acquisition_enforces_download_bounds(monkeypatch, tmp_path):
    payload = b'x' * (MAX_ENVELOPE + 1)
    monkeypatch.setattr('update_center.acquisition.urlopen',
        lambda request, timeout: Response(request.full_url, payload))
    with pytest.raises(AcquisitionError):
        acquire_release('https://releases.example/chief.zip',
            'https://releases.example/envelope.json', tmp_path,
            allowed_hosts={'releases.example'}, private_storage_confirmed=True)
