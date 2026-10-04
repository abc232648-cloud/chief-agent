"""Local-owner lifecycle controls. No remote authority or PID-based killing."""
import json
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from operations.backup import _plain
from operations.time_integrity import utc_now,utc_text

COMPONENTS={'dashboard','worker','scheduler'}


def atomic_json(path,value):
    path=Path(path);_plain(path.parent)
    if path.exists():_plain(path,file=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            os.chmod(temporary,0o600)
            json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
        deadline=time.monotonic()+1
        while True:
            try:
                os.replace(temporary,path);break
            except PermissionError:
                # Windows readers briefly deny replacement; never truncate the live record.
                if os.name!='nt' or time.monotonic()>=deadline:raise
                time.sleep(.01)
    finally:
        if temporary.exists():temporary.unlink()


def control_paths(instance,component):
    if component not in COMPONENTS:raise ValueError('Unknown service component.')
    _plain(instance.state_root)
    folder=instance.state_root/'service-control'
    folder.mkdir(mode=0o700,exist_ok=True);_plain(folder)
    return folder/(component+'.json'),folder/(component+'.stop.json')


def read_record(path):
    deadline=time.monotonic()+1
    while True:
        try:
            _plain(path,file=True)
            if path.stat().st_size>4096:raise ValueError('Service record exceeds limit.')
            return json.loads(path.read_text(encoding='utf-8'))
        except PermissionError:
            if os.name!='nt' or time.monotonic()>=deadline:raise
            time.sleep(.01)


def request_stop(instance,component):
    status,request=control_paths(instance,component)
    record=read_record(status)
    if record.get('state') not in {'STARTING','READY','STOPPING'}:return False
    token=record.get('run_id')
    if not isinstance(token,str) or len(token)!=32:raise ValueError('Invalid service identity.')
    atomic_json(request,{'run_id':token})
    return True


class ServiceRuntime:
    def __init__(self,instance,component):
        self.status,self.request=control_paths(instance,component)
        self.stopping=threading.Event();self.finished=threading.Event();self.handlers={}
        self.record_lock=threading.RLock()
        self.record={'version':1,'component':component,'run_id':uuid.uuid4().hex,
                     'pid':os.getpid(),'started_at':utc_text(utc_now()),'state':'STARTING'}
        self.thread=threading.Thread(target=self._watch,name='chief-stop-watch',daemon=True)

    def _write(self,state=None):
        with self.record_lock:
            if state is not None:self.record['state']=state
            elif self.stopping.is_set():self.record['state']='STOPPING'
            stamp=utc_text(utc_now())
            self.record.update(updated_at=stamp,heartbeat_at=stamp)
            atomic_json(self.status,self.record)

    def _watch(self):
        last_heartbeat=time.monotonic()
        while not self.finished.wait(0.2):
            try:
                if time.monotonic()-last_heartbeat>=5:
                    self._write()
                    last_heartbeat=time.monotonic()
                if self.request.exists() and read_record(self.request).get('run_id')==self.record['run_id']:
                    self.stopping.set()
            except Exception:
                # A corrupt local control must fail closed, not keep taking work.
                self.stopping.set()

    def __enter__(self):
        self._write('STARTING')
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT,signal.SIGTERM):
                self.handlers[signum]=signal.getsignal(signum)
                signal.signal(signum,lambda *_:self.stopping.set())
        self.thread.start();return self

    def ready(self):self._write('READY')

    def __exit__(self,kind,value,traceback):
        self.stopping.set();self.finished.set()
        if self.thread.is_alive():self.thread.join(timeout=3)
        for signum,handler in self.handlers.items():signal.signal(signum,handler)
        self._write('STOPPED' if kind is None else 'REVIEW')
