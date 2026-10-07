"""Windows CurrentUser DPAPI; no portable/custom encryption keys."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import subprocess
import tempfile


class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data,decrypt=False):
    if os.name!='nt':raise OSError('Windows credential backend is unavailable.')
    source_buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(source_buffer,ctypes.POINTER(ctypes.c_ubyte)));result=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL;kernel.LocalFree.argtypes=[ctypes.c_void_p]
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result)):
        raise OSError('Windows credential operation failed.')
    try:return ctypes.string_at(result.data,result.size)
    finally:kernel.LocalFree(result.data)


def unprotect(data):return _crypt(data,True)


def provision(directory,key,value):
    if os.name!='nt':raise OSError('Windows credential backend is unavailable.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}\.dpapi',key):raise ValueError('Invalid credential key.')
    if not isinstance(value,str) or not 1<=len(value.encode())<=16384:raise ValueError('Invalid credential size.')
    root=Path(directory).absolute()
    for item in (root,*root.parents):
        if item.is_symlink() or item.is_junction():raise PermissionError('Credential paths must not follow links.')
    root.mkdir(parents=True,exist_ok=True)
    sid=subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-Command','[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],text=True).strip()
    if not re.fullmatch(r'S-\d+(?:-\d+)+',sid):raise OSError('Cannot identify the credential owner.')
    advapi=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    descriptor=ctypes.c_void_p()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    advapi.SetFileSecurityW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_void_p]
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    sddl=f'D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)'
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,ctypes.byref(descriptor),None):raise OSError('Credential ACL creation failed.')
    try:
        if not advapi.SetFileSecurityW(str(root),0x80000004,descriptor):raise OSError('Credential directory ACL protection failed.')
    finally:kernel.LocalFree(descriptor)
    encrypted=_crypt(value.encode())
    fd,name=tempfile.mkstemp(dir=root,prefix='.credential-')
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(encrypted);stream.flush();os.fsync(stream.fileno())
        os.replace(name,root/key)
    finally:
        if Path(name).exists():Path(name).unlink()
    return {'status':'PROVISIONED','backend':'windows-dpapi','scope':'CURRENT_USER'}
