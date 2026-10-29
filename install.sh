#!/bin/sh
# Install a published LokiShell release. Keep this bootstrap self-contained so
# it can install releases that predate the installer. Run only after sh has
# received the whole function, including the embedded Python program.
set -eu

install_lokishell() {
    loki_python=${LOKI_INSTALL_PYTHON:-}
    if [ -z "$loki_python" ]; then
        for loki_candidate in python3 python3.14 python3.13 python3.12 python3.11; do
            if command -v "$loki_candidate" >/dev/null 2>&1 &&
                "$loki_candidate" -I -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
                loki_python=$loki_candidate
                break
            fi
        done
    fi
    if [ -z "$loki_python" ]; then
        printf '%s\n' 'LokiShell needs Python 3.11 or newer. Install Python, then rerun this command.' >&2
        return 1
    fi
    "$loki_python" -I - "$@" <<'LOKI_INSTALL_PYTHON'
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
import venv

REPOSITORY = 'ccosnean/lokishell'
OWNER = 'LokiShell curl installer v1\n'
CURL = ['curl', '--fail', '--silent', '--show-error', '--location',
        '--proto', '=https', '--proto-redir', '=https', '--tlsv1.2',
        '--retry', '2', '--connect-timeout', '15', '--max-time', '180']


def release_info(version):
    endpoint = 'latest'
    if version:
        if not re.fullmatch(r'v?\d+\.\d+\.\d+', version):
            raise ValueError('Use a stable version such as --version 0.7.0.')
        endpoint = 'tags/v' + version.removeprefix('v')
    response = subprocess.check_output(CURL + [
        '--header', 'Accept: application/vnd.github+json',
        '--header', 'X-GitHub-Api-Version: 2022-11-28',
        f'https://api.github.com/repos/{REPOSITORY}/releases/{endpoint}'])
    release = json.loads(response)
    tag = release.get('tag_name', '')
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag) or release.get('draft') or release.get('prerelease'):
        raise ValueError('No stable LokiShell release was found.')
    if version and tag != 'v' + version.removeprefix('v'):
        raise ValueError('GitHub returned a different version than requested.')
    name = f'lokishell-{tag[1:]}.tar.gz'
    asset = next((item for item in release.get('assets', []) if item['name'] == name), None)
    if not asset or not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or ''):
        raise ValueError(f'{tag} has no release archive with a SHA-256 digest.')
    expected_url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    if asset.get('browser_download_url') != expected_url:
        raise ValueError('Unexpected release download URL.')
    return tag[1:], expected_url, asset['digest'].split(':', 1)[1]


def download(url, destination):
    subprocess.run(CURL + ['--output', str(destination), url], check=True)


def unpack(archive, destination, version):
    """Extract regular files only, without trusting tar paths or permissions."""
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        if len(members) > 10000 or sum(item.size for item in members) > 256 * 1024 * 1024:
            raise ValueError('Release archive is unexpectedly large.')
        seen = set()
        for item in members:
            path = PurePosixPath(item.name)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.name
                    or not path.parts or path.parts[0] != f'lokishell-{version}'
                    or not (item.isfile() or item.isdir()) or path in seen):
                raise ValueError('Release archive contains an unsafe or duplicate path.')
            seen.add(path)
        for item in members:
            parts = PurePosixPath(item.name).parts[1:]
            target = destination.joinpath(*parts)
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(item) as content, target.open('xb') as output:
                    shutil.copyfileobj(content, output)
                target.chmod(0o755 if item.mode & 0o111 else 0o644)
    for required in ('bin/loki', 'requirements.txt', 'lib/lokishell/cli.py'):
        if not (destination / required).is_file():
            raise ValueError(f'Release archive is missing {required}.')
    if not (destination / 'tools').is_dir():
        raise ValueError('Release archive is missing its tool definitions.')


def prepare_environment(build):
    environment = build / 'venv'
    try:
        venv.EnvBuilder(with_pip=True, symlinks=True).create(environment)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError('Could not create a Python virtual environment. Install Python venv/ensurepip support and retry.') from error
    python = environment / 'bin/python'
    subprocess.run([str(python), '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check',
                    'install', '--no-input', '--no-cache-dir', '--no-user', '--only-binary=:all:',
                    '--prefix', str(environment), '-r', str(build / 'requirements.txt')], check=True)


def configure_launcher(build):
    launcher = build / 'bin/loki'
    body = launcher.read_text().split('\n', 1)[1]
    # Both sh and Python can execute this header, including prefixes with
    # spaces. Loki's own command launchers also invoke this file via Python.
    header = "#!/bin/sh\n'''exec' " + shlex.quote(str(build / 'venv/bin/python')) + ' "$0" "$@"\n' + "' '''\n"
    launcher.write_text(header + body)
    launcher.chmod(0o755)


def check_version(launcher, version):
    result = subprocess.check_output([str(launcher), '--version'], text=True, stderr=subprocess.STDOUT).strip()
    if result != 'LokiShell ' + version:
        raise ValueError('The installed executable reported an unexpected version.')


def check_destination(root, entry):
    for path in (root, root / 'releases', root / '.installer', root / '.install.lock'):
        if path.is_symlink():
            raise ValueError(f'Refusing a linked installation path: {path}')
    if root.exists() and any(root.iterdir()):
        marker = root / '.installer'
        if not marker.is_file() or marker.read_text() != OWNER:
            raise ValueError(f'{root} already contains files not managed by this installer.')
    if os.path.lexists(entry):
        expected = root / 'current/bin/loki'
        if not entry.is_symlink() or Path(os.path.abspath(entry.parent / os.readlink(entry))) != expected:
            raise ValueError(f'{entry} already exists and is not owned by this installer. Choose another --prefix.')
    current = root / 'current'
    if os.path.lexists(current):
        if not current.is_symlink() or not current.resolve().is_relative_to(root / 'releases'):
            raise ValueError(f'Refusing to replace an unrelated installation path: {current}')


@contextmanager
def install_lock(root, entry):
    check_destination(root, entry)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / '.installer').exists():
        (root / '.installer').write_text(OWNER)
    with (root / '.install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        check_destination(root, entry)
        yield


def activate(root, entry, build):
    current = root / 'current'
    previous = os.readlink(current) if current.is_symlink() else None
    temporary_current = root / ('.current-' + uuid.uuid4().hex)
    temporary_entry = entry.parent / ('.loki-' + uuid.uuid4().hex)
    entry.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary_entry.symlink_to(root / 'current/bin/loki')
        temporary_current.symlink_to(build.relative_to(root))
        check_destination(root, entry)
        temporary_current.replace(current)
        try:
            temporary_entry.replace(entry)
        except OSError:
            if previous is None:
                current.unlink()
            else:
                temporary_current.symlink_to(previous)
                temporary_current.replace(current)
            raise
    finally:
        temporary_current.unlink(missing_ok=True)
        temporary_entry.unlink(missing_ok=True)


def install(prefix, version=None):
    root, entry = prefix / 'share/lokishell', prefix / 'bin/loki'
    check_destination(root, entry)
    version, url, digest = release_info(version)
    with install_lock(root, entry):
        current = root / 'current'
        receipt = current / '.release.json'
        expected = {'version': version, 'sha256': digest}
        if receipt.is_file() and json.loads(receipt.read_text()) == expected and entry.exists():
            try:
                check_version(entry, version)
            except (OSError, ValueError, subprocess.CalledProcessError):
                pass  # A broken runtime can be repaired by rerunning the installer.
            else:
                print(f'LokiShell {version} is already installed at {entry}.')
                return entry
        with tempfile.TemporaryDirectory(prefix='lokishell-download-') as scratch:
            archive = Path(scratch) / 'release.tar.gz'
            print(f'Installing LokiShell {version}...', flush=True)
            download(url, archive)
            with archive.open('rb') as content:
                if hashlib.file_digest(content, 'sha256').hexdigest() != digest:
                    raise ValueError('Release checksum did not match. The existing installation is unchanged.')
            releases = root / 'releases'
            releases.mkdir(exist_ok=True)
            # Build in its final location: virtual environments contain
            # absolute paths and must not be moved after creation.
            build = Path(tempfile.mkdtemp(prefix=version + '-', dir=releases))
            try:
                unpack(archive, build, version)
                prepare_environment(build)
                configure_launcher(build)
                check_version(build / 'bin/loki', version)
                (build / '.release.json').write_text(json.dumps(expected) + '\n')
                activate(root, entry, build)
            except BaseException:
                if not current.is_symlink() or current.resolve() != build:
                    shutil.rmtree(build)
                raise
        print(f'Installed LokiShell {version} at {entry}.')
        return entry


def main():
    parser = argparse.ArgumentParser(prog='install.sh', description='Install or update LokiShell from a verified GitHub release.')
    parser.add_argument('--prefix', default=str(Path.home() / '.local'), help='installation prefix (default: ~/.local)')
    parser.add_argument('--version', help='install a specific release, such as 0.7.0 (default: latest)')
    args = parser.parse_args()
    if sys.version_info < (3, 11) or sys.platform not in ('darwin', 'linux'):
        raise ValueError('This installer requires macOS or Linux with Python 3.11 or newer.')
    for command in ('curl', 'direnv'):
        if not shutil.which(command):
            raise ValueError(f'{command} is required. Install it with your package manager and rerun the installer.')
    prefix = Path(args.prefix).expanduser().resolve()
    if any(c in str(prefix) for c in ('\n', '\r', '\x00')):
        raise ValueError('The installation prefix cannot contain control characters.')
    entry = install(prefix, args.version)
    if str(entry.parent) not in os.environ.get('PATH', '').split(os.pathsep):
        print('Add the installation directory to your PATH:')
        print('  export PATH=' + shlex.quote(str(entry.parent)) + ':"$PATH"')
    print('Prepare shell integration and existing personas: ' + shlex.quote(str(entry)) + ' setup')
    print('For a new persona: ' + shlex.quote(str(entry)) + ' use NAME --global')
    print('Then open a fresh terminal. Rerun this installer to upgrade; no reset is needed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f'Install failed: {error}', file=sys.stderr)
        sys.exit(1)
LOKI_INSTALL_PYTHON
}

install_lokishell "$@"
