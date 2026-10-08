"""Prepare an isolated offline environment using a reviewed host interpreter.

This is an operator API, not a dashboard endpoint. No service is started and no
live pointer/database is accepted. Wheel/runtime review is separate from hash
matching. Failed/interrupted attempts remain inert for diagnosis, never reused.
"""
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import uuid
import zipfile
from compatibility.manifest import _unique_object

from operations.backup import _sha
from operations.private_tree import private_tree
from operations.time_integrity import utc_now, utc_text
from installation.dependencies import inspect_wheels, locked_requirements, normalized
from update_center.staging import inspect_package
from installation.profiles import source_profile


def runtime_identity():
    return {'python':platform.python_version(),'implementation':platform.python_implementation(),
            'platform':sys.platform,'machine':platform.machine(),'sqlite':sqlite3.sqlite_version,
            'executable_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            'bootstrap_pip_version':importlib.metadata.version('pip')}


def _run(command,env,cwd):
    # Never propagate pip output: hostile package metadata can contain payloads.
    result=subprocess.run(command,cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=300)
    if result.returncode:raise RuntimeError('Offline bootstrap command failed.')


def prepare_environment(source_data,wheel_data,root,*,expected_archive,expected_source,
                        expected_wheels,expected_runtime,private_storage_confirmed=False,
                        runtime_and_dependencies_reviewed=False, dependency_profile="core"):
    if private_storage_confirmed is not True or runtime_and_dependencies_reviewed is not True:
        raise ValueError('Private storage and explicit runtime/dependency review are required.')
    if os.name=='nt':
        import ctypes
        privileged=bool(ctypes.windll.shell32.IsUserAnAdmin())
    else:privileged=os.getuid()==0
    if privileged:raise ValueError('Prepare the environment as a normal user, not administrator/root.')
    observed=runtime_identity()
    if not isinstance(expected_runtime,dict) or observed!=expected_runtime:
        raise ValueError('Host runtime differs from reviewed identity.')
    _sha(expected_wheels)
    verified=inspect_package(source_data,expected_archive=expected_archive,expected_source=expected_source)
    family={'win32':'windows','linux':'ubuntu'}.get(sys.platform)
    if family is None:raise ValueError('This operating system is not qualified for bootstrap.')
    with zipfile.ZipFile(io.BytesIO(source_data)) as archive:
        manifest=json.loads(archive.read('SOURCE_MANIFEST.json'))
        prefix='source/' if 'hash_method' in manifest else 'chief-agent/'
        lock,inventory=source_profile(archive,prefix,family,dependency_profile)
        if any(inventory.get(k)!=observed[k] for k in ('python','implementation','platform','machine','sqlite')):
            raise ValueError('Source runtime inventory differs from this host; qualify a new inventory first.')
        pins=locked_requirements(lock)
        packages=inventory.get('packages')
        if not isinstance(packages,dict):raise ValueError('Source package inventory is missing.')
        expected_packages={normalized(name):version for name,version in packages.items()}
        if len(expected_packages)!=len(packages):raise ValueError('Duplicate normalized package names.')
        # The historical Windows inventory includes the host bootstrap tool,
        # while its wheel lock deliberately excludes pip from the target venv.
        if 'pip' not in pins and 'pip' in expected_packages:
            if expected_packages.pop('pip')!=observed['bootstrap_pip_version']:
                raise ValueError('Bootstrap pip differs from the recorded inventory.')
        if expected_packages!={name:pin['version'] for name,pin in pins.items()}:
            raise ValueError('Source package inventory and dependency lock differ.')
        wheels=inspect_wheels(wheel_data,expected_archive=expected_wheels,lock_text=lock)
        identity='bootstrap-'+uuid.uuid4().hex
        receipt={**verified,'format_version':1,'preparation_id':identity,'created_at':utc_text(utc_now()),
                 'runtime':observed,'dependency_archive_sha256':expected_wheels,'dependency_profile':dependency_profile,
                 'status':'ENVIRONMENT_PREPARATION_STARTED','phase':'CREATE_ENVIRONMENT','activation':'BLOCKED','data_migration':False,
                 'publisher_authentication':'NOT_ESTABLISHED','compatibility':'NOT_EVALUATED',
                 'external_actions_enabled':False}
        with private_tree(root,identity) as (folder,write):
            for name in sorted(manifest['files']):write('source/'+name,archive.read(prefix+name))
            write('SOURCE_MANIFEST.json',archive.read('SOURCE_MANIFEST.json'))
            for name,data in wheels.items():write('wheels/'+name,data)
            write('attempt.json',json.dumps(receipt,sort_keys=True).encode())
            for name in ('temporary','home'): (folder/name).mkdir(mode=0o700)
            environment=folder/'environment'
            python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            # Supply only runtime/OS necessities. Do not inherit provider keys,
            # SMTP secrets, PYTHONPATH or pip indexes from the Chief process.
            env={k:os.environ[k] for k in ('SYSTEMROOT','WINDIR','COMSPEC','LD_LIBRARY_PATH') if k in os.environ}
            env.update(PATH=str(Path(sys.executable).parent)+os.pathsep+os.defpath,
                       HOME=str(folder/'home'),USERPROFILE=str(folder/'home'),
                       TMP=str(folder/'temporary'),TEMP=str(folder/'temporary'),TMPDIR=str(folder/'temporary'),
                       PIP_CONFIG_FILE=os.devnull,PYTHONUTF8='1')
            try:
                _run([sys.executable,'-I','-m','venv','--copies','--without-pip',str(environment)],env,folder)
                if not python.is_file() or python.is_symlink() or 'include-system-site-packages = false' not in (environment/'pyvenv.cfg').read_text():
                    raise RuntimeError('Isolated environment was not created correctly.')
                receipt['phase']='INSTALL_OFFLINE_WHEELS'
                _run([sys.executable,'-I','-m','pip','--python',str(python),'--isolated','install',
                      '--no-index','--no-deps','--only-binary=:all:','--no-compile',
                      *[str(folder/'wheels'/name) for name in sorted(wheels)]],env,folder)
                receipt['phase']='CHECK_DEPENDENCIES'
                _run([sys.executable,'-I','-m','pip','--python',str(python),'--isolated','check'],env,folder)
                expected=json.dumps(expected_packages,sort_keys=True)
                probe="import importlib.metadata as m,json,re; actual={re.sub(r'[-_.]+','-',d.metadata['Name']).lower():d.version for d in m.distributions()}; assert actual==json.loads("+repr(expected)+")"
                receipt['phase']='VERIFY_INSTALLED_INVENTORY'
                _run([str(python),'-I','-c',probe],env,folder)
                receipt['phase']='VERIFY_SOURCE_AND_RUNTIME'
                if runtime_identity()!=observed:raise RuntimeError('Runtime changed during bootstrap.')
                for name,digest in manifest['files'].items():
                    if hashlib.sha256((folder/'source'/name).read_bytes()).hexdigest()!=digest:
                        raise RuntimeError('Source changed during bootstrap.')
            except KeyboardInterrupt:
                receipt.update(status='ENVIRONMENT_PREPARATION_INTERRUPTED',reason='Interrupted attempt preserved. Never resume or activate it automatically.')
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
                receipt.update(status='ENVIRONMENT_PREPARATION_FAILED',reason='Offline preparation failed; inspect this inert attempt locally. Never auto-retry or activate it.')
            else:
                receipt.update(status='ENVIRONMENT_PREPARED_NOT_ACTIVATED',phase='COMPLETE',reason='Installation-specific acceptance and explicit guarded activation remain required.')
            receipt['finished_at']=utc_text(utc_now())
            write('result.json',json.dumps(receipt,sort_keys=True).encode())
    return receipt
