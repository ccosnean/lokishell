"""Edit user-owned setup files without applying credential-store permissions."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile


def read(path):
    # Keep existing CRLFs and other user text byte-for-byte through block edits.
    with Path(path).open(newline='') as source:
        return source.read()


def write(core, path, text, expected):
    path = Path(path)
    core.check_storage_path(path)
    previous = path.stat() if path.exists() else None
    if previous and not path.is_file():
        raise ValueError(f'Refusing to replace a non-file setup path: {path}')
    if (read(path) if previous else '') != expected:
        raise ValueError(f'Setup file changed during editing; leaving it unchanged: {path}')
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        if previous:
            # BSD and GNU cp preserve mode, ownership and ACLs with -p. Writing
            # into that copy also preserves metadata lost by a new 0600 inode.
            subprocess.run(['/bin/cp', '-p', str(path), str(temporary)], check=True,
                           capture_output=True, text=True)
            # Python also copies extended attributes on Linux; GNU cp -p
            # alone does not preserve all user attributes there.
            shutil.copystat(path, temporary)
        with temporary.open('w', newline='') as output:
            output.write(text)
        core.check_storage_path(path)
        current = path.stat() if path.exists() else None
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns) if info else None
        if identity(current) != identity(previous):
            raise ValueError(f'Setup file changed during editing; leaving it unchanged: {path}')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def remove(core, path, expected):
    core.check_storage_path(path)
    if read(path) != expected:
        raise ValueError(f'Setup file changed during editing; leaving it unchanged: {path}')
    path.unlink()
