"""Exercise the Windows packaged timezone fallback, not a host Unix database."""
import os
import subprocess
import sys
import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows-only packaged IANA timezone prerequisite')
def test_lagos_scheduler_timezone_without_system_zoneinfo():
    result = subprocess.run([sys.executable, '-c',
        "from zoneinfo import ZoneInfo,reset_tzpath; from datetime import datetime; "
        "reset_tzpath([]); print(datetime(2026,1,1,tzinfo=ZoneInfo('Africa/Lagos')).utcoffset().total_seconds())"],
        capture_output=True, text=True, timeout=10, check=True)
    assert result.stdout.strip() == '3600.0'
