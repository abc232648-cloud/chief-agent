"""Fail-closed production installation readiness without installing or starting Chief.

This validates the already prepared production instance, private storage, pinned
source tree, sandboxed browser selection, external-runtime profile isolation, a
separately pinned operational backup, and the reviewable service definitions. It
never installs services, starts components, promotes a restore, changes Farm
approval, or performs network operations.
"""
import os
from pathlib import Path

_ENV_KEYS = (
    'JOB_WORKER_DB','CHIEF_STATE_ROOT','DASHBOARD_HOST','DASHBOARD_PORT',
    'CHIEF_BROWSER_RUNTIME','CHIEF_TRUSTED_PROXY','DASHBOARD_TRUSTED_HOST',
    'CHIEF_FARM_PRODUCTION','CHIEF_INSTANCE_MODE','CHIEF_INSTANCE_CONFIG',
    'CHIEF_ISOLATED_ROOT','CHIEF_SERVICE_CONFIGURED',
)


def _restore_environment(before):
    for key in _ENV_KEYS:
        value=before[key]
        if value is None:os.environ.pop(key,None)
        else:os.environ[key]=value


def _absolute(label,value):
    path=Path(value)
    if not path.is_absolute():raise ValueError(label+' must be absolute for installation readiness.')
    return path


def installation_readiness(config, *, source_root, source_manifest, backup_directory,
                           manifest_sha256, expected_source, expected_schema, platform,
                           project, user, python=None):
    """Return metadata-only readiness evidence; no deployment action is performed."""
    config=_absolute('Production config',config)
    source_root=_absolute('Source root',source_root)
    source_manifest=_absolute('Source manifest',source_manifest)
    backup_directory=_absolute('Backup directory',backup_directory)
    before={key:os.environ.get(key) for key in _ENV_KEYS}
    try:
        from deployment.launch import configure
        instance=configure(str(config))
        if instance.mode!='production':
            raise PermissionError('Installation readiness is only valid for a prepared production instance.')
        if expected_schema!=instance.schema_sha256:
            raise ValueError('Backup schema pin must match the prepared production instance schema.')

        from operations.storage_health import storage_readiness
        storage=storage_readiness(instance.state_root)

        from browser.runtime import launch_options
        browser=launch_options()
        if browser.get('chromium_sandbox') is not True or not browser.get('executable_path'):
            raise PermissionError('Production browser qualification must require the native sandbox.')

        from operations.backup import source_identity,verify_backup
        actual_source=source_identity(source_root,source_manifest)
        if actual_source!=expected_source:
            raise ValueError('Installed source tree does not match the pinned backup source identity.')

        # Existing desktop/global runtime profiles are never discovered or imported.
        # Each pinned candidate gets one reserved path below Chief private state. An
        # unmarked pre-existing path is a conflict, not something readiness may reuse.
        from deployment.runtime_profiles import plans_from_source
        runtime_profiles=plans_from_source(instance.state_root,source_root)

        manifest=verify_backup(backup_directory,manifest_sha256=manifest_sha256,
                               expected_source=expected_source,expected_schema=expected_schema)

        from deployment.service_files import definitions
        files=definitions(platform,project,user,instance_config=str(config),python=python)
        expected_names={
            'windows':{'chief-agent-dashboard.xml','chief-agent-worker.xml','chief-agent-scheduler.xml'},
            'linux':{'chief-agent-dashboard.service','chief-agent-worker.service','chief-agent-scheduler.service'},
            'macos':{'local.chief-agent-dashboard.plist','local.chief-agent-worker.plist','local.chief-agent-scheduler.plist'},
        }
        if platform not in expected_names or set(files)!=expected_names[platform]:
            raise ValueError('Incomplete service-definition set.')

        return {
            'status':'SOURCE_READY_FOR_INSTALL_REVIEW',
            'installation_performed':False,
            'services_started':False,
            'restore_promoted':False,
            'network_checks_performed':False,
            'mode':instance.mode,
            'source_tree_sha256':actual_source,
            'schema_sha256':instance.schema_sha256,
            'storage_scope':storage['scope'],
            'browser_sandbox_required':True,
            'runtime_profiles':[{'candidate_id':item['candidate_id'],'status':item['status']} for item in runtime_profiles],
            'backup_id':manifest['backup_id'],
            'backup_coverage':manifest['coverage'],
            'service_definitions':sorted(files),
        }
    finally:
        _restore_environment(before)
