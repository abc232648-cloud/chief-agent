"""Explicit local preparation/preflight and managed installation commands."""
import argparse
import getpass
import json
import os
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)

    check=commands.add_parser('preflight')
    check.add_argument('--config',required=True)

    manifest=commands.add_parser('build-source-manifest')
    manifest.add_argument('--source-root',required=True)
    manifest.add_argument('--output',required=True)
    manifest.add_argument('--expected-git-commit')

    install_backup=commands.add_parser('create-install-backup')
    install_backup.add_argument('--config',required=True)
    install_backup.add_argument('--destination',required=True)
    install_backup.add_argument('--source-root',required=True)
    install_backup.add_argument('--source-manifest',required=True)
    install_backup.add_argument('--confirm-private-storage',action='store_true')

    ready=commands.add_parser('install-readiness')
    ready.add_argument('--config',required=True);ready.add_argument('--source-root',required=True);ready.add_argument('--source-manifest',required=True)
    ready.add_argument('--backup-directory',required=True);ready.add_argument('--manifest-sha256',required=True)
    ready.add_argument('--expected-source',required=True);ready.add_argument('--expected-schema',required=True)
    ready.add_argument('--platform',choices=['linux','windows','macos'],required=True)
    ready.add_argument('--project',required=True);ready.add_argument('--user',required=True);ready.add_argument('--python')
    ready.add_argument('--managed-installation')

    bootstrap=commands.add_parser('bootstrap-install')
    bootstrap.add_argument('--directory',required=True)
    bootstrap.add_argument('--source-root',required=True)
    bootstrap.add_argument('--source-manifest',required=True)
    bootstrap.add_argument('--expected-source',required=True)
    bootstrap.add_argument('--python',required=True)
    bootstrap.add_argument('--config',required=True)
    bootstrap.add_argument('--schema-sha256',required=True)
    bootstrap.add_argument('--confirm-private-storage',action='store_true')

    preview=commands.add_parser('prepare-preview')
    preview.add_argument('--directory',required=True);preview.add_argument('--source-root',required=True);preview.add_argument('--source-manifest',required=True);preview.add_argument('--username',required=True)

    production=commands.add_parser('prepare-production')
    production.add_argument('--directory',required=True)
    production.add_argument('--config',required=True)
    production.add_argument('--source-root',required=True)
    production.add_argument('--source-manifest',required=True)
    production.add_argument('--username',required=True)
    production.add_argument('--public-host',required=True)
    production.add_argument('--browser-runtime',required=True)
    production.add_argument('--dashboard-port',type=int,default=8765)
    production.add_argument('--trusted-tls-proxy',default='127.0.0.1')
    production.add_argument('--farm-operations',choices=['DISABLED','ENABLED'],default='DISABLED')
    args=parser.parse_args()

    if args.command=='preflight':
        os.environ['CHIEF_INSTANCE_MODE']='production';os.environ['CHIEF_INSTANCE_CONFIG']=str(Path(args.config).absolute())
        from .instance import load_instance
        instance=load_instance();print(json.dumps({'status':'READY','mode':instance.mode,'schema_sha256':instance.schema_sha256,'schema_migration':False},sort_keys=True))
    elif args.command=='build-source-manifest':
        from .source_manifest import build_source_manifest
        result=build_source_manifest(args.source_root,args.output,expected_git_commit=args.expected_git_commit)
        print(json.dumps(result,sort_keys=True))
    elif args.command=='create-install-backup':
        from .install_backup import create_install_backup
        result=create_install_backup(args.config,args.destination,source_root=args.source_root,
            source_manifest=args.source_manifest,private_storage_confirmed=args.confirm_private_storage)
        print(json.dumps(result,sort_keys=True))
    elif args.command=='install-readiness':
        from .install_readiness import installation_readiness
        result=installation_readiness(args.config,source_root=args.source_root,source_manifest=args.source_manifest,
            backup_directory=args.backup_directory,manifest_sha256=args.manifest_sha256,
            expected_source=args.expected_source,expected_schema=args.expected_schema,
            platform=args.platform,project=args.project,user=args.user,python=args.python,
            managed_installation=args.managed_installation)
        print(json.dumps(result,sort_keys=True))
    elif args.command=='bootstrap-install':
        from .bootstrap_install import bootstrap_installation
        result=bootstrap_installation(args.directory,source_root=args.source_root,source_manifest=args.source_manifest,
            expected_source=args.expected_source,python=args.python,instance_config=args.config,
            schema_sha256=args.schema_sha256,private_storage_confirmed=args.confirm_private_storage)
        print(json.dumps(result,sort_keys=True))
    elif args.command=='prepare-production':
        password=getpass.getpass('Production Owner password: ')
        if password!=getpass.getpass('Repeat password: '):raise ValueError('Passwords did not match.')
        from .prepare_instance import prepare_production
        result=prepare_production(args.directory,args.config,args.source_root,args.source_manifest,args.username,password,
            public_host=args.public_host,browser_runtime=args.browser_runtime,dashboard_port=args.dashboard_port,
            trusted_tls_proxy=args.trusted_tls_proxy,farm_operations=args.farm_operations)
        print(json.dumps(result,sort_keys=True))
    else:
        password=getpass.getpass('Preview Owner password: ')
        if password!=getpass.getpass('Repeat password: '):raise ValueError('Passwords did not match.')
        from .prepare_instance import prepare_isolated
        print(json.dumps(prepare_isolated(args.directory,args.source_root,args.source_manifest,args.username,password),sort_keys=True))


if __name__=='__main__':main()
