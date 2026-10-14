"""Explicit local systemd-user lifecycle for managed Chief services on Linux."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from deployment.service_runtime import control_paths, read_record

_UNITS = {
    'dashboard': 'chief-agent-dashboard.service',
    'worker': 'chief-agent-worker.service',
    'scheduler': 'chief-agent-scheduler.service',
}


def _systemctl(*arguments, timeout=60):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Automatic service lifecycle is implemented only for Linux user services.')
    executable = shutil.which('systemctl')
    if not executable:
        raise RuntimeError('systemctl is unavailable.')
    environment = {key: os.environ[key] for key in ('HOME', 'USER', 'LOGNAME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS') if key in os.environ}
    result = subprocess.run([executable, '--user', *arguments], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=environment, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError('Managed Chief user-service command failed.')


def stop_services(*, timeout=60):
    """Stop writers before source selection. Fixed unit names prevent command injection."""
    _systemctl('stop', _UNITS['scheduler'], _UNITS['worker'], _UNITS['dashboard'], timeout=timeout)
    return {'status': 'SERVICES_STOPPED', 'components': ['scheduler', 'worker', 'dashboard']}


def _previous_runs(instance):
    runs = {}
    for component in _UNITS:
        status, _ = control_paths(instance, component)
        if status.exists() and not status.is_symlink():
            try:
                runs[component] = read_record(status).get('run_id')
            except Exception:
                runs[component] = None
        else:
            runs[component] = None
    return runs


def start_services(instance, *, timeout=90):
    """Start the selected source and require fresh READY records for all components."""
    before = _previous_runs(instance)
    _systemctl('start', _UNITS['dashboard'], _UNITS['worker'], _UNITS['scheduler'], timeout=min(timeout, 60))
    deadline = time.monotonic() + timeout
    pending = set(_UNITS)
    while pending and time.monotonic() < deadline:
        for component in tuple(pending):
            status, _ = control_paths(instance, component)
            if not status.exists() or status.is_symlink():
                continue
            try:
                record = read_record(status)
            except Exception:
                continue
            run_id = record.get('run_id')
            if (record.get('state') == 'READY' and isinstance(run_id, str) and len(run_id) == 32 and
                    run_id != before.get(component)):
                pending.remove(component)
        if pending:
            time.sleep(0.2)
    if pending:
        raise RuntimeError('Managed Chief services did not reach fresh READY state.')
    return {'status': 'SERVICES_READY', 'components': ['dashboard', 'worker', 'scheduler']}
