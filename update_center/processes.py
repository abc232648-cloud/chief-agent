"""Bounded trusted test/probe processes, including ordinary descendants.

Not a hostile-code security sandbox. No elevation, system service, or unrelated
process is used. Windows children enter a private kill-on-close job before their
first instruction; POSIX children use a new process group.
"""
import os
import signal
import subprocess
import time


def run(command, *, cwd, env, output, timeout, max_output_bytes=8*1024*1024):
    if type(timeout) not in (int, float) or not 0 < timeout <= 7200:
        raise ValueError('Bounded process timeout required.')
    if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 64*1024*1024:
        raise ValueError('Bounded process output required.')
    if os.name == 'nt':
        return _windows(command, cwd=cwd, env=env, output=output, timeout=timeout, max_output_bytes=max_output_bytes)
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=output, stderr=output, start_new_session=True)
    try:
        deadline = time.monotonic()+timeout
        while True:
            if os.fstat(output.fileno()).st_size > max_output_bytes:
                raise ValueError('Update process output exceeded its limit.')
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                code = process.wait(timeout=min(.1, remaining))
                if os.fstat(output.fileno()).st_size > max_output_bytes:
                    raise ValueError('Update process output exceeded its limit.')
                return subprocess.CompletedProcess(command, code)
            except subprocess.TimeoutExpired:
                continue
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _windows(command, *, cwd, env, output, timeout, max_output_bytes):
    import ctypes as c
    from ctypes import wintypes as w
    import _winapi
    import msvcrt

    class Basic(c.Structure):
        _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64), ('flags', w.DWORD),
                    ('min_working', c.c_size_t), ('max_working', c.c_size_t), ('process_limit', w.DWORD),
                    ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

    class IO(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

    class Extended(c.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', c.c_size_t),
                    ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]

    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.ResumeThread.argtypes = [w.HANDLE]
    kernel.ResumeThread.restype = w.DWORD
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise c.WinError(c.get_last_error())
    process = thread = None
    assigned = False
    try:
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if not kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)):
            raise c.WinError(c.get_last_error())
        with open(os.devnull, 'rb') as null:
            output_handle = msvcrt.get_osfhandle(output.fileno())
            null_handle = msvcrt.get_osfhandle(null.fileno())
            handles = (output_handle, null_handle)
            original = [os.get_handle_inheritable(handle) for handle in handles]
            try:
                for handle in handles:
                    os.set_handle_inheritable(handle, True)
                startup = subprocess.STARTUPINFO()
                startup.dwFlags = subprocess.STARTF_USESTDHANDLES
                startup.hStdInput, startup.hStdOutput, startup.hStdError = null_handle, output_handle, output_handle
                startup.lpAttributeList = {'handle_list': list(handles)}
                process, thread, _, _ = _winapi.CreateProcess(str(command[0]), subprocess.list2cmdline(command),
                    None, None, True, 0x4 | 0x08000000 | 0x400, env, str(cwd), startup)
            finally:
                for handle, inherited in zip(handles, original):
                    os.set_handle_inheritable(handle, inherited)
        if not kernel.AssignProcessToJobObject(job, process):
            raise c.WinError(c.get_last_error())
        assigned = True
        if kernel.ResumeThread(thread) == 0xFFFFFFFF:
            raise c.WinError(c.get_last_error())
        deadline = time.monotonic()+timeout
        while True:
            if os.fstat(output.fileno()).st_size > max_output_bytes:
                raise ValueError('Update process output exceeded its limit.')
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            wait = _winapi.WaitForSingleObject(process, max(1, int(min(.1, remaining)*1000)))
            if wait == 0:
                break
            if wait != 258:
                raise OSError('Update process wait failed.')
        if os.fstat(output.fileno()).st_size > max_output_bytes:
            raise ValueError('Update process output exceeded its limit.')
        return subprocess.CompletedProcess(command, _winapi.GetExitCodeProcess(process))
    finally:
        # Closing this private job kills surviving children even after parent success.
        kernel.CloseHandle(job)
        if process is not None:
            if not assigned:
                _winapi.TerminateProcess(process, 1)
            _winapi.WaitForSingleObject(process, 10000)
            _winapi.CloseHandle(process)
        if thread is not None:
            _winapi.CloseHandle(thread)
