"""Local readiness only. Never decrypt keys, contact providers, or change state."""
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import ssl

from operations.backup import _plain
from operations.time_integrity import utc_text
from control.local_probes import service


def _json(path):
    _plain(path, file=True)
    with path.open('rb') as stream:
        raw=stream.read(65537)
    if len(raw)>65536:raise ValueError('Oversized local configuration')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('Invalid local configuration')
    return value


def _installation(store):
    if os.environ.get('CHIEF_INSTANCE_MODE')!='production':return None
    filename=Path(os.environ.get('CHIEF_INSTANCE_CONFIG',''))
    if not filename.is_absolute():return None
    data=_json(filename)
    root=Path(data['state_root']);db=Path(data['database'])
    if not root.is_absolute() or not db.is_absolute():raise ValueError('Absolute instance paths required')
    _plain(root);_plain(db,file=True)
    if data.get('mode')!='PRODUCTION' or db.resolve()!=Path(store.path).resolve():raise ValueError('Instance mismatch')
    if root.name!='production' or not db.resolve().is_relative_to(root.resolve()):return None
    _plain(root.parent)
    return root.parent,data



def _trusted_listener(host, ca=None, *, port=443):
    if ca is not None:_plain(ca,file=True)
    context=ssl.create_default_context(cafile=str(ca) if ca is not None else None)
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.75) as raw:
            with context.wrap_socket(raw,server_hostname=host):pass
    except (OSError,ssl.SSLError):
        return 'UNAVAILABLE','The local HTTPS listener could not establish a trusted connection for the configured hostname. Check the HTTPS service and certificate.'
    return 'HEALTHY','Local HTTPS certificate and hostname verified. This does not prove that another device trusts the certificate or that every route works.'


def checks(store, now):
    stamp=utc_text(now());rows=[]
    def add(key,label,status,reason,monitoring='MONITORED',observed=stamp):
        rows.append({'node':{'kind':'local_check','id':key},'label':label,'status':status,
                     'reason':reason,'monitoring':monitoring,'desired_mode':'NOT_APPLICABLE',
                     'observed_at':observed,'scope':'LOCAL_READINESS'})
    def run(key,label,fn):
        try:
            status,reason=fn();add(key,label,status,reason)
        except Exception:
            add(key,label,'UNKNOWN','Local check could not be completed. No private diagnostic details are exposed.')
    def disk():
        root=Path(store.path).absolute().parent;_plain(root)
        usage=shutil.disk_usage(root)
        status='DEGRADED' if usage.free<1024**3 or usage.free/usage.total<0.05 else 'HEALTHY'
        return status,f'{usage.free/1024**3:.1f} GiB free on the data volume. Warning below 1 GiB or 5%; no files are deleted automatically.'
    run('local.storage','Data storage space',disk)
    def models():
        path=Path(store.path).absolute();_plain(path,file=True)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)) as con:
            con.execute('PRAGMA query_only=ON')
            tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'model_assignments' not in tables:return 'UNKNOWN','Model assignment storage is not prepared.'
            count=con.execute('SELECT count(*) FROM model_assignments').fetchone()[0]
        return 'HEALTHY',f'Model assignment storage is readable ({count} saved overrides). Provider availability, credentials and model responses were not tested.'
    run('local.models','AI routing storage',models)
    def dns():
        mode=os.environ.get('CHIEF_DNS_MODE')
        if mode is None:return 'UNKNOWN','No explicit Chief DNS policy is configured in this process.'
        from networking.dns_policy import validate_mode
        validate_mode(mode)
        return 'HEALTHY','Chief DNS policy is valid. No external DNS lookup or Internet connectivity test was made.'
    run('local.dns','DNS policy',dns)
    observation=service(store,'worker',now)
    from operations.time_integrity import timestamp_status
    fresh=timestamp_status(observation.observed_at,now=now(),max_age=timedelta(seconds=30))=='CURRENT'
    add('local.worker','Worker supervisor',observation.status.value if fresh else 'UNKNOWN',
        observation.reason if fresh else 'No recent worker supervisor heartbeat. Check whether the worker service is running.',observed=observation.observed_at)
    try:installation=_installation(store)
    except Exception:installation=None
    if installation is None:
        for key,label in [('network','Local dashboard connection'),('https','HTTPS listener'),('owner','Owner PWA files'),('staff','Staff PWA files'),('jobs','Job PWA files')]:
            add('local.'+key,label,'UNKNOWN','No matching managed production installation is configured for this check.','NOT_CONFIGURED',None)
        return rows
    root,config=installation
    def network():
        port=config.get('dashboard_port')
        if type(port) is not int or not 1<=port<=65535:raise ValueError('Invalid port')
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.75):pass
        except OSError:return 'UNAVAILABLE','The configured local dashboard port is not accepting connections.'
        return 'HEALTHY','The configured local dashboard port accepts connections. Remote-device access is not tested.'
    run('local.network','Local dashboard connection',network)
    def https():
        from deployment.https_policy import public_hostname
        host=public_hostname(config.get('public_host'))
        ca=root/'caddy/data/caddy/pki/authorities/local/root.crt'
        return _trusted_listener(host,ca if ca.exists() else None)
    run('local.https','HTTPS listener',https)
    for app,label in [('owner','Owner'),('staff','Staff'),('jobs','Job')]:
        def assets(app=app):
            folder=root/'pwas'/app/'dist'
            for name in ('index.html','sw.js'):
                path=folder/name
                try:
                    _plain(path,file=True)
                    with path.open('rb') as stream:
                        if not stream.read(1):raise ValueError('Empty asset')
                except (OSError,ValueError):return 'UNAVAILABLE','Required PWA files are missing, unreadable or unsafe. Repair the installed client bundle.'
            return 'HEALTHY','Installed entry page and service worker are readable. Browser behavior, delivery and phone installation are not tested here.'
        run('local.'+app,label+' PWA files',assets)
    return rows
