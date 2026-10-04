"""Explicit local preparation/preflight. No production migration or restore promotion."""
import argparse
import getpass
import importlib
import json
import os
from pathlib import Path


def prepare_isolated(root,source_root,source_manifest,username,password):
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
        importlib.import_module('database.'+name).migrate_isolated(store,isolated_root=root,backup_directory=backup,restore_directory=restore,**pins)
    owner=IdentityService(store).bootstrap(username,password)
    with store._connect() as con:schema=fingerprint(con)
    return {'status':'PREVIEW_PREPARED','database':str(store.path),'schema_sha256':schema,'owner_id':owner,'worker_enabled':False,'restores_promoted':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    check=commands.add_parser('preflight');check.add_argument('--config',required=True)
    prepare=commands.add_parser('prepare-preview');prepare.add_argument('--directory',required=True);prepare.add_argument('--source-root',required=True);prepare.add_argument('--source-manifest',required=True);prepare.add_argument('--username',required=True)
    args=parser.parse_args()
    if args.command=='preflight':
        os.environ['CHIEF_INSTANCE_MODE']='production';os.environ['CHIEF_INSTANCE_CONFIG']=str(Path(args.config).absolute())
        from .instance import load_instance
        instance=load_instance();print(json.dumps({'status':'READY','mode':instance.mode,'schema_sha256':instance.schema_sha256,'schema_migration':False}))
    else:
        password=getpass.getpass('Preview Owner password: ')
        if password!=getpass.getpass('Repeat password: '):raise ValueError('Passwords did not match.')
        print(json.dumps(prepare_isolated(args.directory,args.source_root,args.source_manifest,args.username,password)))


if __name__=='__main__':main()
