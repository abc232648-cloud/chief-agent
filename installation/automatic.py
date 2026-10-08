"""Download pinned wheels and prepare selected Python environments, never activate.

The source and runtime must already be independently reviewed. This is not an
OS/Python/browser/container installer and never changes an existing environment.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile
from installation.bootstrap import prepare_environment, runtime_identity
from installation.dependencies import inspect_wheels, MAX_BUNDLE
from installation.profiles import selected_profiles, source_profile
from operations.private_tree import private_tree
from update_center.staging import inspect_package


def _download(lock_path, destination, env):
    result=subprocess.run([sys.executable,'-I','-m','pip','--isolated','--disable-pip-version-check',
        'download','--index-url','https://pypi.org/simple','--require-hashes','--no-deps',
        '--only-binary=:all:','--dest',str(destination),'-r',str(lock_path)],
        env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=600)
    if result.returncode:
        raise RuntimeError('Pinned dependency download failed.')


def prepare_selected(source_data, root, *, components, expected_archive, expected_source,
                     expected_runtime, private_storage_confirmed=False,
                     runtime_and_dependencies_reviewed=False):
    selected=selected_profiles(components)
    if private_storage_confirmed is not True or runtime_and_dependencies_reviewed is not True:
        raise ValueError('Private storage and runtime/dependency review are required.')
    if os.name=='nt':
        import ctypes
        privileged=bool(ctypes.windll.shell32.IsUserAnAdmin())
    else:
        privileged=os.getuid()==0
    if privileged:
        raise ValueError('Run preparation as an ordinary user.')
    if expected_runtime!=runtime_identity():
        raise ValueError('Host runtime differs from reviewed identity.')
    verified=inspect_package(source_data,expected_archive=expected_archive,expected_source=expected_source)
    family={'win32':'windows','linux':'ubuntu'}.get(sys.platform)
    if family is None:
        raise ValueError('Operating system is not qualified.')
    profiles={}
    with zipfile.ZipFile(io.BytesIO(source_data)) as archive:
        manifest=json.loads(archive.read('SOURCE_MANIFEST.json'))
        prefix='source/' if 'hash_method' in manifest else 'chief-agent/'
        # Validate every selection before downloading or creating environments.
        for profile in selected:
            lock,inventory=source_profile(archive,prefix,family,profile)
            if any(inventory.get(k)!=expected_runtime[k] for k in ('python','implementation','platform','machine','sqlite')):
                raise ValueError('Selected profile requires a different qualified runtime.')
            profiles[profile]=lock
    result={**verified,'selected':selected,'status':'DEPENDENCIES_PREPARING','activation':'BLOCKED',
            'external_actions_enabled':False,'environments':{},'components':{},
            'scope':'PYTHON_DEPENDENCIES_ONLY','remaining_runtime_setup':['browser build','service activation'],
            'download_id':'dependencies-'+uuid.uuid4().hex}
    with private_tree(root,result['download_id']) as (folder,write):
        write('attempt.json',json.dumps(result,sort_keys=True).encode())
        for name in ('temporary','home','environments'):
            (folder/name).mkdir(mode=0o700)
        env={k:os.environ[k] for k in ('SYSTEMROOT','WINDIR','COMSPEC','LD_LIBRARY_PATH') if k in os.environ}
        env.update(PATH=str(Path(sys.executable).parent)+os.pathsep+os.defpath,
                   HOME=str(folder/'home'),USERPROFILE=str(folder/'home'),
                   TEMP=str(folder/'temporary'),TMP=str(folder/'temporary'),TMPDIR=str(folder/'temporary'),
                   PIP_CONFIG_FILE=os.devnull,PYTHONUTF8='1')
        for profile,lock in profiles.items():
            state={'status':'DOWNLOADING','repair':'Retry into a new isolated attempt after correcting the reported prerequisite.'}
            result['components'][profile]=state
            try:
                write(profile+'.txt',lock.encode())
                downloads=folder/(profile+'-wheels');downloads.mkdir(mode=0o700)
                _download(folder/(profile+'.txt'),downloads,env)
                files=sorted(downloads.iterdir())
                if not files or any(not p.is_file() or p.is_symlink() or p.suffix!='.whl' for p in files):
                    raise ValueError('Unexpected dependency download output.')
                if sum(p.stat().st_size for p in files)>MAX_BUNDLE:
                    raise ValueError('Dependency downloads exceed limit.')
                stream=io.BytesIO()
                with zipfile.ZipFile(stream,'w',zipfile.ZIP_STORED) as bundle:
                    for p in files:
                        bundle.write(p,p.name)
                data=stream.getvalue();digest=hashlib.sha256(data).hexdigest()
                inspect_wheels(data,expected_archive=digest,lock_text=lock)
                state['status']='PREPARING'
                receipt=prepare_environment(source_data,data,folder/'environments',expected_archive=expected_archive,
                    expected_source=expected_source,expected_wheels=digest,expected_runtime=expected_runtime,
                    private_storage_confirmed=True,runtime_and_dependencies_reviewed=True,dependency_profile=profile)
                state.update(status=receipt['status'],receipt=receipt)
                if receipt['status']!='ENVIRONMENT_PREPARED_NOT_ACTIVATED':
                    raise RuntimeError('Selected component preparation did not finish.')
                result['environments'][profile]=str(folder/'environments'/receipt['preparation_id']/'environment'/('Scripts/python.exe' if os.name=='nt' else 'bin/python'))
            except KeyboardInterrupt:
                state['status']='INTERRUPTED';result['status']='DEPENDENCIES_INTERRUPTED';break
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,zipfile.BadZipFile):
                state['status']='FAILED';result['status']='DEPENDENCIES_FAILED';break
        else:
            result['status']='SELECTED_PYTHON_DEPENDENCIES_PREPARED_NOT_ACTIVATED'
        for profile in selected:
            result['components'].setdefault(profile,{'status':'NOT_ATTEMPTED'})
        if 'scrapy' in result['environments']:
            result['launch_environment']={'CHIEF_SCRAPY_PYTHON':result['environments']['scrapy']}
        write('result.json',json.dumps(result,sort_keys=True).encode())
    return result
