"""Exclusive, immutable draft output under an operator-owned private root.

Directory handles pin ancestors on Windows (deny delete) and POSIX (dir_fd).
Normal failures remove only files created by this operation. Process death can
leave an inert draft directory; it is never overwritten or replayed automatically.
This does not protect against an administrator or a compromised service identity.
"""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import re


def validate_application_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise ValueError('Invalid application identifier.')
    if value.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        raise ValueError('Reserved application identifier.')
    return value


@contextmanager
def _windows_directory(path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    # Read attributes; share read/write but not delete; open the reparse object.
    handle = create(str(path), 0x80, 3, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), 'Cannot pin draft directory.')
    try:
        class Info(ctypes.Structure):
            _fields_ = [('attributes', wintypes.DWORD), ('tag', wintypes.DWORD)]
        info = Info()
        get = kernel.GetFileInformationByHandleEx
        get.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        if not get(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), 'Cannot inspect draft directory.')
        if info.attributes & 0x400 or not info.attributes & 0x10:
            raise ValueError('Draft directories must not be links or reparse points.')
        yield
    finally:
        close(handle)


@contextmanager
def draft_files(root, application_id, draft):
    """Yield completed paths; caller commits DB inside this scope or files roll back."""
    identity = validate_application_id(application_id)
    root = Path(os.path.abspath(root))
    # Serialize before touching the filesystem.
    payloads = {'cv.json': json.dumps(draft['cv'], ensure_ascii=False, indent=2).encode('utf-8'),
                'cover-letter.txt': draft['cover_letter'].encode('utf-8')}
    created = []
    folder = root / 'candidate' / 'application_history' / 'drafts' / identity
    with ExitStack() as stack:
        current = Path(root.anchor)
        if os.name == 'nt':
            stack.enter_context(_windows_directory(current))
            for part in root.parts[1:]:
                current /= part
                stack.enter_context(_windows_directory(current))
            for part in ('candidate', 'application_history', 'drafts'):
                current /= part
                current.mkdir(mode=0o700, exist_ok=True)
                stack.enter_context(_windows_directory(current))
            folder.mkdir(mode=0o700, exist_ok=False)
            # Held until cleanup completes, excluding the leaf's own removal.
            leaf_stack = ExitStack()
            try:
                leaf_stack.enter_context(_windows_directory(folder))
                def open_file(name):
                    return os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
                def remove_file(name):
                    (folder / name).unlink()
                yield from _write(payloads, created, folder, open_file, remove_file)
            except BaseException:
                leaf_stack.close()
                folder.rmdir()
                raise
            finally:
                leaf_stack.close()
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(current, flags)
            stack.callback(os.close, fd)
            for part in root.parts[1:]:
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
            for part in ('candidate', 'application_history', 'drafts'):
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
            parent_fd = fd
            os.mkdir(identity, 0o700, dir_fd=parent_fd)
            try:
                fd = os.open(identity, flags, dir_fd=parent_fd)
                stack.callback(os.close, fd)
                def open_file(name):
                    return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                def remove_file(name):
                    os.unlink(name, dir_fd=fd)
                os.fsync(parent_fd)
                yield from _write(payloads, created, folder, open_file, remove_file, fd)
            except BaseException:
                os.rmdir(identity, dir_fd=parent_fd)
                raise


def _write(payloads, created, folder, open_file, remove_file, directory_fd=None):
    try:
        for name, data in payloads.items():
            fd = open_file(name)
            created.append(name)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        if directory_fd is not None:
            os.fsync(directory_fd)
        yield {'cv_path': str(folder / 'cv.json'), 'cover_letter_path': str(folder / 'cover-letter.txt')}
    except BaseException:
        for name in reversed(created):
            remove_file(name)
        raise
