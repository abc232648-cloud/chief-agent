"""Build a deterministic source manifest from one clean, pinned Git checkout.

The manifest is an installation artifact, not repository state. Only Git-tracked
regular files are considered, Chief private/runtime paths are excluded using the
same policy as release-package verification, and the output must live outside
the source checkout so generating it cannot change the source identity.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

from compatibility.package import _private
from operations import backup


def _git(root, *args):
    result = subprocess.run(
        ['git', '-C', str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode('utf-8', errors='replace').strip()
        raise ValueError('Git source inspection failed' + (': ' + message if message else '.'))
    return result.stdout


def _atomic_json(path, value):
    path = Path(os.path.abspath(path))
    parent = backup._plain(path.parent)
    if path.exists() or path.is_symlink():
        backup._plain(path, file=True)
    temporary = parent / ('.' + path.name + '.tmp-' + str(uuid.uuid4()))
    data = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def build_source_manifest(source_root, output, *, expected_git_commit=None):
    """Write and return the manifest for a clean tracked source checkout."""
    root = backup._plain(source_root)
    output = Path(os.path.abspath(output))
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError('Source manifest output must be outside the source checkout.')

    top = Path(_git(root, 'rev-parse', '--show-toplevel').decode('utf-8').strip())
    top = backup._plain(top)
    if top != root:
        raise ValueError('Source root must be the Git checkout root.')

    commit = _git(root, 'rev-parse', 'HEAD').decode('ascii').strip().lower()
    if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
        raise ValueError('Git checkout does not have a valid commit identity.')
    if expected_git_commit is not None and commit != expected_git_commit.lower():
        raise ValueError('Git checkout commit does not match the pinned release commit.')

    if _git(root, 'status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('Tracked source checkout has local changes.')

    raw = _git(root, 'ls-files', '-z')
    names = [item.decode('utf-8') for item in raw.split(b'\0') if item]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('Git checkout contains case-colliding tracked paths.')

    files = {}
    excluded_private = []
    for name in sorted(names):
        relative = backup._relative(name)
        if _private(relative):
            excluded_private.append(name)
            continue
        path = backup._plain(root / relative, file=True)
        files[name] = backup.digest_file(path)
    if not files:
        raise ValueError('Source checkout has no deployable tracked files.')

    tree = hashlib.sha256(
        '\n'.join(name + '\0' + files[name] for name in sorted(files)).encode('utf-8')
    ).hexdigest()
    manifest = {'files': files, 'source_tree_sha256': tree}
    written = _atomic_json(output, manifest)
    if backup.source_identity(root, written) != tree:
        raise ValueError('Generated source manifest did not reproduce its source identity.')
    return {
        'status': 'SOURCE_MANIFEST_BUILT',
        'git_commit': commit,
        'source_tree_sha256': tree,
        'file_count': len(files),
        'private_paths_excluded': len(excluded_private),
        'manifest': str(written),
    }
