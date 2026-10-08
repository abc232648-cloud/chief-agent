"""Explicit non-secret service configuration; never migrates operational data."""
import argparse
import json
import os
from pathlib import Path
from operations.backup import _plain
from deployment.https_policy import contract as https_contract


def configure(filename):
    path=Path(filename)
    if not path.is_absolute():raise ValueError('Absolute instance configuration required.')
    _plain(path,file=True)
    if path.stat().st_size>8192:raise ValueError('Instance configuration exceeds limit.')
    data=json.loads(path.read_text(encoding='utf-8'))
    allowed={'mode','state_root','database','schema_sha256','dashboard_port','browser_runtime',
             'farm_operations','trusted_tls_proxy','public_host'}
    if not isinstance(data,dict) or set(data)-allowed:raise ValueError('Unknown instance field; secrets and arbitrary environment are forbidden.')
    if data.get('mode') not in {'PRODUCTION','ISOLATED_DEVELOPMENT'}:raise ValueError('Explicit prepared instance required.')
    root=Path(data['state_root']);database=Path(data['database'])
    if not root.is_absolute() or not database.is_absolute():raise ValueError('Absolute private state paths required.')
    _plain(root);_plain(database,file=True)
    if not database.is_relative_to(root):raise PermissionError('Database outside private state root.')
    port=data.get('dashboard_port',8765)
    if type(port) is not int or not 1<=port<=65535:raise ValueError('Invalid dashboard port.')
    farm=data.get('farm_operations','DISABLED')
    if farm not in {'DISABLED','ENABLED'}:raise ValueError('farm_operations must be explicitly DISABLED or ENABLED.')

    # Service configuration cannot quietly inherit a different target or public bind.
    for key,expected in [('JOB_WORKER_DB',str(database)),('CHIEF_STATE_ROOT',str(root)),
                         ('DASHBOARD_HOST','127.0.0.1')]:
        if os.environ.get(key) not in (None,expected):raise PermissionError('Conflicting instance environment.')
    os.environ.update(JOB_WORKER_DB=str(database),CHIEF_STATE_ROOT=str(root),
                      DASHBOARD_HOST='127.0.0.1',DASHBOARD_PORT=str(port))

    if data.get('browser_runtime'):
        runtime=Path(data['browser_runtime'])
        if not runtime.is_absolute():raise ValueError('Absolute browser runtime configuration required.')
        _plain(runtime,file=True)
        os.environ['CHIEF_BROWSER_RUNTIME']=str(runtime)

    if data['mode']=='PRODUCTION':
        # Every production web request is HTTPS-only. The backend remains loopback and
        # trusts exactly one same-host loopback TLS terminator.
        https=https_contract(data.get('public_host'),port,data.get('trusted_tls_proxy'))
        expected_env={
            'CHIEF_TRUSTED_PROXY':https['trusted_proxy'],
            'DASHBOARD_TRUSTED_HOST':https['public_host'],
            'CHIEF_FARM_PRODUCTION':farm,
        }
        for key,expected in expected_env.items():
            if os.environ.get(key) not in (None,expected):raise PermissionError('Conflicting instance environment.')
        os.environ.update(expected_env)
        os.environ.update(CHIEF_INSTANCE_MODE='production',CHIEF_INSTANCE_CONFIG=str(path))
    else:
        if farm!='DISABLED' or data.get('trusted_tls_proxy') is not None or data.get('public_host') is not None:
            raise ValueError('Production Farm/TLS fields are not accepted by an isolated-development instance.')
        os.environ.update(CHIEF_INSTANCE_MODE='test',CHIEF_ISOLATED_ROOT=str(root),CHIEF_FARM_PRODUCTION='DISABLED')
        for key in ('CHIEF_INSTANCE_CONFIG','CHIEF_TRUSTED_PROXY','DASHBOARD_TRUSTED_HOST'):
            os.environ.pop(key,None)
    # No .env loading for an explicitly configured service, even in isolated tests.
    os.environ['CHIEF_SERVICE_CONFIGURED']='1'
    from deployment.instance import load_instance
    return load_instance('worker')


def run(component,instance):
    if component=='worker':
        from worker.runner import main
        return main()
    if component=='scheduler':
        from scheduler import main
        return main()
    from worker.ownership import ComponentOwnership
    from deployment.service_runtime import ServiceRuntime
    with ComponentOwnership(instance.database,'dashboard'):
        import dashboard_app as app
        from identity.service import IdentityService
        IdentityService(app.STORE).require_ready()
        with ServiceRuntime(instance,'dashboard') as runtime:
            from application.http_transport import create_dashboard_server,close_dashboard_server
            from threading import Thread
            server=create_dashboard_server(app.Handler)
            thread=Thread(target=server.run,name='chief-http',daemon=True)
            try:
                thread.start();runtime.ready()
                while thread.is_alive() and not runtime.stopping.wait(0.2):pass
                if not runtime.stopping.is_set():raise RuntimeError('HTTP service stopped unexpectedly.')
            finally:
                close_dashboard_server(server);thread.join(timeout=10)
                if thread.is_alive():raise RuntimeError('HTTP shutdown incomplete.')
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',required=True,choices=['dashboard','worker','scheduler'])
    parser.add_argument('--config',required=True)
    parser.add_argument('--stop',action='store_true')
    args=parser.parse_args()
    try:
        instance=configure(args.config)
        if args.stop:
            from deployment.service_runtime import request_stop
            return 0 if request_stop(instance,args.component) else 3
        from operations.storage_health import storage_readiness
        from operations.diagnostics import configure as configure_diagnostics
        storage_readiness(instance.database.parent)
        logger,handler=configure_diagnostics(instance.database.parent,args.component)
        try:return run(args.component,instance)
        finally:
            logger.removeHandler(handler);handler.close()
    except Exception:
        # No paths, credentials, exception values or environment in service logs.
        print('CHIEF_SERVICE_FAILED: startup or operation refused; inspect private instance status.')
        return 1


if __name__=='__main__':raise SystemExit(main())
