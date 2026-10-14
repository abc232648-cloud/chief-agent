"""Explicit browser/client test inputs; never inherit provider/session secrets."""
import hashlib
import json
from pathlib import Path

from update_center.local_inputs import read_bounded
from browser.runtime import launch_options
from compatibility.manifest import _unique_object
from operations import backup


def resolve(filename=None):
    environment, identities = {}, {}
    if filename is not None:
        config = json.loads(read_bounded(filename, 8192), object_pairs_hook=_unique_object)
        if not isinstance(config, dict) or set(config) != {'browser_runtime', 'client_root'}:
            raise ValueError('Only explicit browser and built companion-client paths are accepted.')
        runtime, root = Path(config['browser_runtime']), Path(config['client_root'])
        if not runtime.is_absolute() or not root.is_absolute():
            raise ValueError('Absolute test input paths required.')
        backup._plain(root)
        launch_options(str(runtime))  # Existing executable hash/sandbox contract.
        identities['browser_runtime'] = backup.digest_file(runtime)
        count = size = 0
        for repo in ('chief-owner-pwa', 'chief-staff-pwa', 'Job-agent-pwa'):
            directory = backup._plain(root / repo / 'dist')
            backup._plain(directory / 'index.html', file=True)
            for path in sorted(directory.rglob('*')):
                backup._plain(path)
                if path.is_file():
                    count += 1; size += path.stat().st_size
                    if count > 5000 or size > 256*1024*1024:
                        raise ValueError('Companion test assets exceed the bounded inventory.')
                    identities[repo+'/'+path.relative_to(directory).as_posix()] = backup.digest_file(path)
        environment = dict(CHIEF_BROWSER_RUNTIME=str(runtime), CHIEF_A10_CLIENT_ROOT=str(root), CHIEF_A10_BROWSER='chromium')
    digest = hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()
    return environment, digest
