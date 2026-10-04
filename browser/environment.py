"""Explicit OS plumbing only; never inherit provider credentials or proxies."""
import os

OS_KEYS = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','TMPDIR',
           'HOME','USERPROFILE','LOCALAPPDATA','APPDATA','LANG','LC_ALL','TZ',
           'DISPLAY','WAYLAND_DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS',
           'PLAYWRIGHT_BROWSERS_PATH','PYTHONUTF8','PYTHONIOENCODING'}


def browser_environment(environment=None):
    environment = os.environ if environment is None else environment
    return {k:v for k,v in environment.items() if k.upper() in OS_KEYS}
