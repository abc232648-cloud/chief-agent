"""Reviewed instance preparation. Production is promoted only from a verified isolated fixture."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid


def prepare_isolated(root,source_root,source_manifest,username,password,*,organization_name=None,farm_name=None):
    from database.store import Store
    from domains.storage import initialize
    from control.agents import AgentControls
    from control.notifications import initialize as notifications
    from application.composition import default_registry
    from browser.site_access import SiteAccess
    from operations.backup import create_backup,restore_isolated
    from identity.service import IdentityService
    from .schema import fingerprint
    root=Path(root).absolute()
    if root.exists():raise ValueError('Preparation requires a new isolated directory.')
    os.umask(0o077);root.mkdir(parents=True,mode=0o700)
    (root/'.chief-isolated-development.json').write_text(json.dumps({'purpose':'ISOLATED_DEVELOPMENT'}))
    (root/'.chief-preview.json').write_text(json.dumps({'mode':'PREVIEW'}))
    store=Store(root/'preview.sqlite3');initialize(store);AgentControls(store,default_registry());notifications(store);SiteAccess(store)
    for name in ('migrations','evidence_migrations','execution_migrations','identity_migrations','farm_indexes'):
        backup=root/('backup-before-'+name);restore=root/('restore-before-'+name)
        receipt=create_backup(store.path,backup,source_root=source_root,source_manifest=source_manifest,private_storage_confirmed=True,writers_quiesced=True)
        pins=dict(manifest_sha256=receipt['manifest_sha256'],expected_source=receipt['source_tree_sha256'],expected_schema=receipt['schema_sha256'])
        restore_isolated(backup,restore,**pins,private_storage_confirmed=True)
        import importlib
        importlib.import_module('database.'+name).migrate_isolated(store,isolated_root=root,backup_directory=backup,restore_directory=restore,**pins)
    owner=IdentityService(store).bootstrap(username,password)
    if farm_name is not None and organization_name is None:
        raise ValueError('An initial farm requires an organization name.')
    ownership_result=None
    if organization_name is not None:
        from .ownership_bootstrap import bootstrap_initial
        ownership_result=bootstrap_initial(store,owner,organization_name,farm_name)
    with store._connect() as con:schema=fingerprint(con)
    result={'status':'PREVIEW_PREPARED','database':str(store.path),'schema_sha256':schema,'owner_id':owner,'worker_enabled':False,'restores_promoted':False}
    if ownership_result is not None:result.update(ownership_result)
    return result


def _new_absolute(path,label):
    path=Path(path).expanduser()
    if not path.is_absolute():raise ValueError(label+' must be absolute.')
    if path.exists() or path.is_symlink():raise FileExistsError(label+' must not already exist.')
    for parent in path.parents:
        if parent.exists() and (parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction())):
            raise PermissionError(label+' parents must not use links.')
    return path


def _write_new_json(path,data,mode=0o600):
    path=Path(path)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(data,stream,sort_keys=True,separators=(',',':'))
        stream.write('\n');stream.flush();os.fsync(stream.fileno())


def prepare_production(root,config,source_root,source_manifest,username,password,*,
                       public_host,browser_runtime,dashboard_port=8765,
                       trusted_tls_proxy='127.0.0.1',farm_operations='DISABLED',
                       organization_name=None,farm_name=None):
    """Create a new production instance without ever running migrations on production state.

    All schema work, Owner bootstrap and optional initial organization/farm bootstrap happen
    in a disposable isolated fixture. A clean SQLite snapshot is then verified, copied to a
    new production candidate, marked, and atomically promoted into the requested state-root
    path. Existing production state is never adopted, overwritten, or migrated.
    """
    from .https_policy import contract as https_contract
    from .schema import preflight
    from browser.runtime import launch_options

    root=_new_absolute(root,'Production state root')
    config=_new_absolute(config,'Production config')
    browser_runtime=Path(browser_runtime).expanduser()
    if not browser_runtime.is_absolute() or not browser_runtime.is_file():
        raise ValueError('Production requires an existing absolute browser runtime configuration.')
    # Re-hash and validate the selected native browser before state preparation.
    launch_options(str(browser_runtime))
    https=https_contract(public_host,dashboard_port,trusted_tls_proxy)
    if farm_operations not in {'DISABLED','ENABLED'}:
        raise ValueError('farm_operations must be explicitly DISABLED or ENABLED.')
    if farm_name is not None and organization_name is None:
        raise ValueError('An initial farm requires an organization name.')
    if farm_operations=='ENABLED' and (organization_name is None or farm_name is None):
        raise ValueError('Enabled Farm operations require an initial organization and farm name.')

    os.umask(0o077)
    root.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    config.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    token=uuid.uuid4().hex
    work=root.parent/('.'+root.name+'.isolated-'+token)
    candidate=root.parent/('.'+root.name+'.candidate-'+token)
    if work.exists() or candidate.exists():raise FileExistsError('Preparation workspace collision.')

    prepared=prepare_isolated(work,source_root,source_manifest,username,password,
                              organization_name=organization_name,farm_name=farm_name)
    schema=prepared['schema_sha256'];source_db=Path(prepared['database'])
    candidate.mkdir(mode=0o700)
    target_db=candidate/'state.sqlite3'
    # SQLite backup produces one closed, journal-independent production snapshot.
    with closing(sqlite3.connect(source_db.resolve().as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(target_db)) as destination:
        source.backup(destination)
    target_db.chmod(0o600)
    preflight(target_db,schema)
    _write_new_json(candidate/'.chief-production.json',{'mode':'PRODUCTION'})

    # The candidate becomes the production root in one same-filesystem rename.
    candidate.rename(root)
    final_db=root/'state.sqlite3'
    preflight(final_db,schema)
    data={
        'mode':'PRODUCTION',
        'state_root':str(root),
        'database':str(final_db),
        'schema_sha256':schema,
        'dashboard_port':dashboard_port,
        'browser_runtime':str(browser_runtime),
        'farm_operations':farm_operations,
        'trusted_tls_proxy':https['trusted_proxy'],
        'public_host':https['public_host'],
    }
    _write_new_json(config,data)
    shutil.rmtree(work)
    result={
        'status':'PRODUCTION_PREPARED',
        'config':str(config),
        'state_root':str(root),
        'database':str(final_db),
        'schema_sha256':schema,
        'owner_id':prepared['owner_id'],
        'public_origin':https['public_origin'],
        'migrations_on_production':False,
        'services_started':False,
    }
    for key in ('organization_id','organization_name','farm_id','farm_name'):
        if key in prepared:result[key]=prepared[key]
    return result
