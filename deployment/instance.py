"""Explicit local instance boundary. Production is never inferred from a DB path."""
from dataclasses import dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class Instance:
    mode: str
    state_root: Path
    database: Path
    schema_sha256: str | None = None

    def open_store(self):
        from database.store import Store
        os.environ['JOB_WORKER_DB']=str(self.database)
        os.environ['CHIEF_STATE_ROOT']=str(self.state_root)
        return Store(self.database,initialize=self.mode!='production',expected_schema=self.schema_sha256)


def load_instance(component='dashboard'):
    mode=os.environ.get('CHIEF_INSTANCE_MODE','preview').lower()
    if mode not in {'preview','test','production'}:raise ValueError('Choose preview, test or production explicitly.')
    if mode=='production':
        config=Path(os.environ.get('CHIEF_INSTANCE_CONFIG',''))
        if not config.is_absolute() or not config.is_file():raise ValueError('Production requires an explicit existing instance configuration.')
        data=json.loads(config.read_text())
        if data.get('mode')!='PRODUCTION':raise ValueError('Production configuration mode mismatch.')
        root=Path(data['state_root']);db=Path(data['database'])
        if not root.is_absolute() or not db.is_absolute():raise ValueError('Production paths must be absolute.')
        for item in (root,db,*root.parents,*db.parents):
            if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):raise PermissionError('Production paths must not follow links.')
        root=root.resolve(strict=True);db=db.resolve(strict=True)
        if not db.is_relative_to(root) or db==root:raise PermissionError('Database is outside the declared private state root.')
        marker=root/'.chief-production.json'
        if not marker.is_file() or json.loads(marker.read_text())!={'mode':'PRODUCTION'}:raise PermissionError('Production state root must be explicitly marked during reviewed preparation.')
        if os.environ.get('JOB_WORKER_DB') not in (None,str(db)):raise PermissionError('Legacy database override conflicts with the declared production instance.')
        from .schema import preflight
        preflight(db,data.get('schema_sha256'))
        return Instance(mode,root,db,data['schema_sha256'])
    if mode=='test':
        root=Path(os.environ.get('CHIEF_ISOLATED_ROOT',''))
        db=Path(os.environ.get('JOB_WORKER_DB',''))
        if not root.is_absolute() or not db.is_absolute():raise PermissionError('Test startup requires an explicit isolated root and database.')
        root=root.resolve(strict=True);db=db.resolve()
        if not db.is_relative_to(root) or (root/'.chief-production.json').exists():raise PermissionError('Test instance cannot open production or outside state.')
        if json.loads((root/'.chief-isolated-development.json').read_text())!={'purpose':'ISOLATED_DEVELOPMENT'}:raise PermissionError('Explicit isolated development marker required.')
        return Instance(mode,root,db)
    if component!='dashboard':raise PermissionError('Preview mode cannot start a worker or scheduler.')
    if os.environ.get('JOB_WORKER_DB') or os.environ.get('CHIEF_INSTANCE_CONFIG'):raise PermissionError('Preview refuses operational/legacy database overrides; select the intended instance explicitly.')
    root=Path(os.environ.get('CHIEF_PREVIEW_ROOT',str(Path.home()/'.local/state/chief-preview'))).expanduser().absolute()
    if (root/'.chief-production.json').exists():raise PermissionError('Preview cannot use a production state directory.')
    for item in (root,*root.parents):
        if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):raise PermissionError('Preview state must not follow links.')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    return Instance(mode,root,root/'preview.sqlite3')
