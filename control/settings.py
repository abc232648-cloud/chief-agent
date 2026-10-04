import json
import os
from pathlib import Path
import tempfile

KEYS={'EMAIL_PROVIDER','SMTP_HOST','SMTP_PORT','SMTP_USERNAME','SMTP_PASSWORD','SMTP_PASSWORD_REF','EMAIL_SENDER','EMAIL_RECIPIENT'}


def settings_path():
    return Path(os.environ.get('JOB_WORKER_DB',str(Path(__file__).resolve().parents[1]/'database/worker.db'))).resolve().parent/'email-settings.json'


def environment(env=None):
    result=dict(os.environ if env is None else env)
    if env is None and settings_path().exists():
        data=json.loads(settings_path().read_text())
        result.update({k:v for k,v in data.items() if k in KEYS})
    return result


def save(payload):
    if not payload or set(payload)-KEYS:raise ValueError('Unsupported email setting.')
    if any(not isinstance(v,str) or len(v)>1000 or any(c in v for c in '\r\n\x00') for v in payload.values()):raise ValueError('Settings must be single-line text.')
    current=environment();changes=dict(payload)
    if os.environ.get('CHIEF_INSTANCE_MODE','').lower()=='production' and (changes.get('SMTP_PASSWORD') or current.get('SMTP_PASSWORD')):
        raise ValueError('Production email uses a provisioned secret reference; plaintext credential migration requires an explicit recovery plan.')
    if changes.get('SMTP_PASSWORD')=='':changes.pop('SMTP_PASSWORD')
    current.update(changes)
    from notifications.email_config import load_email_settings
    settings=load_email_settings(current)
    if not 1<=settings.port<=65535:raise ValueError('Invalid SMTP port.')
    for key in ('EMAIL_SENDER','EMAIL_RECIPIENT'):
        if current.get(key) and ('@' not in current[key] or any(c.isspace() for c in current[key])):raise ValueError('Enter a valid email address.')
    if not settings.host or any(c.isspace() for c in settings.host) or '/' in settings.host:raise ValueError('Enter a mail server hostname.')
    path=settings_path();path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.email-settings-')
    try:
        os.chmod(name,0o600)
        with os.fdopen(fd,'w') as stream:json.dump({k:current.get(k,'') for k in KEYS},stream)
        os.replace(name,path)
    finally:
        if Path(name).exists():Path(name).unlink()
    return {'status':'SAVED','message':'Email settings saved privately; used by the next delivery. Blank password preserves the existing password.'}
