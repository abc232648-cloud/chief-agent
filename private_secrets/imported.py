"""OS-encrypted imported keys. No plaintext fallback or logging of child output."""
import os
from pathlib import Path
import subprocess
from .service import SecretUnavailable, _private_read


def _systemd(operation, value):
    try:
        result = subprocess.run(
            ['/usr/bin/systemd-creds', '--user', '--name=chief-model-key',
             '--with-key=host' if operation == 'encrypt' else '--refuse-null', operation, '-', '-'],
            input=value, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15, check=True)
        if not result.stdout or len(result.stdout) > 32768:
            raise ValueError()
        return result.stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SecretUnavailable('Protected key storage is unavailable; no plaintext fallback was used.') from None


def provision(directory, value):
    if not isinstance(value, str) or not 1 <= len(value.encode()) <= 4096 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('Enter a valid API key without whitespace (maximum 4096 bytes).')
    root = Path(directory)
    if os.name == 'nt':
        from .windows import provision as windows_provision
        windows_provision(root, 'key.dpapi', value)
        return 'windows-dpapi'
    encrypted = _systemd('encrypt', value.encode())
    fd = os.open(root/'key.cred', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encrypted); stream.flush(); os.fsync(stream.fileno())
    return 'systemd-user'


def resolve(directory, backend):
    try:
        if backend == 'windows-dpapi' and os.name == 'nt':
            from .windows import unprotect
            value = unprotect(_private_read(directory, 'key.dpapi'))
        elif backend == 'systemd-user' and os.name != 'nt':
            value = _systemd('decrypt', _private_read(directory, 'key.cred'))
        else:
            raise ValueError()
        text = value.decode('utf-8')
        if not 1 <= len(value) <= 4096 or any(c.isspace() or ord(c) < 32 for c in text):
            raise ValueError()
        return text
    except (OSError, ValueError):
        raise SecretUnavailable('Stored key unavailable for this operating-system account.') from None
