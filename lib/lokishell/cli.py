#!/usr/bin/env python3
"""LokiShell: folder-selected account environments for command-line tools."""
import base64
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from .registry import Registry, Context, ENV_NAME

VERSION = '0.5.1'
BEGIN, END = '# BEGIN lokishell', '# END lokishell'
NAME = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')


def root():
    return Path(os.environ.get('LOKI_HOME', Path.home() / '.config/lokishell')).expanduser().absolute()


def executable():
    return str(Path(sys.argv[0]).absolute())


def write(path, text):
    check_storage_path(path)
    # Readers in other shells must see either complete version, never partial JSON.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(text)
    try:
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def mkdir(path):
    check_storage_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def check_storage_path(path):
    """Do not follow symlinks within managed storage, including ancestor directories."""
    if path.is_symlink():
        raise ValueError(f'Refusing to use a symlink: {path}')
    if path.is_relative_to(root()):
        for parent in path.parents:
            if parent.is_symlink():
                raise ValueError(f'Refusing to use linked account storage: {parent}')
            if parent == root():
                break


def profile_dir(name):
    if not NAME.fullmatch(name):
        raise ValueError('Profile names may contain letters, numbers, underscores, and hyphens.')
    for path in (root(), root() / 'profiles', root() / 'profiles' / name):
        if path.is_symlink():
            raise ValueError(f'Account storage must not be a symlink: {path}')
    return root() / 'profiles' / name


def settings():
    path = root() / 'settings.json'
    return json.loads(path.read_text()) if path.exists() else {
        'global_persona': None, 'folders': {}, 'repositories': {}}


@contextmanager
def edit_settings():
    mkdir(root())
    path = root() / '.settings.lock'
    check_storage_path(path)
    with path.open('a') as lock:
        path.chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = settings()
        if not (root() / 'settings.json').exists():
            value['shell_session'] = uuid.uuid4().hex
        try:
            yield value
            write(root() / 'settings.json', json.dumps(value, indent=2) + '\n')
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def git_context():
    if not shutil.which('git'):
        return None
    env = {k: v for k, v in os.environ.items() if k not in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR')}
    result = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir', '--show-toplevel'],
                            env=env, capture_output=True, text=True)
    lines = result.stdout.splitlines()
    if result.returncode == 0 and len(lines) == 2:
        return Path(lines[0]).resolve(), Path(lines[1]).resolve()
    return None


def effective():
    value = settings()
    current = Path.cwd().resolve()
    context = git_context()
    for directory in (current, *current.parents):
        if str(directory) in value['folders']:
            persona = value['folders'][str(directory)]
            if persona is None:
                return value['global_persona'], 'global (folder override removed)', directory
            return persona, 'folder', directory
        path = directory / '.envrc'
        if path.is_file():
            found = block(path.read_text())
            if found:
                match = re.search(r'^# profile: (.+)$', found.group(), re.M)
                if match:
                    return match.group(1), 'folder', directory
        if context and directory == context[1] and str(context[0]) in value['repositories']:
            return value['repositories'][str(context[0])], 'repository/worktree', context[1]
    return value['global_persona'], 'global', None


def read_config(name):
    path = profile_dir(name) / 'profile.json'
    if not path.is_file():
        raise ValueError(f'Profile {name!r} does not exist. Run loki create {name}.')
    return json.loads(path.read_text())


def save_config(name, config):
    write(profile_dir(name) / 'profile.json', json.dumps(config, indent=2) + '\n')
    write(profile_dir(name) / '.revision', str(time.time_ns()) + '\n')


def registry():
    return Registry(root())


def core_api():
    return sys.modules[__name__]


@contextmanager
def edit_profile(name):
    path = profile_dir(name) / '.profile.lock'
    check_storage_path(path)
    with path.open('a') as lock:
        path.chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read_config(name)
        try:
            yield value
            save_config(name, value)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def create_profile(name):
    directory = profile_dir(name)
    mkdir(directory)
    catalog = registry()
    config = directory / 'profile.json'
    if not config.exists():
        save_config(name, {'name': name, 'tools': [], 'tool_config': {}, 'mcp_logins': {}})
    elif 'tools' not in read_config(name):
        with edit_profile(name) as value:
            value['tools'] = catalog.legacy_roots()
    catalog.prepare(core_api(), name, catalog.roots(read_config(name)))
    envrc = (f'watch_file {shlex.quote(str(directory / "profile.json"))} '
              f'{shlex.quote(str(directory / ".revision"))}\n'
              f'eval "$({shlex.quote(executable())} _env {shlex.quote(name)})"\n')
    write(directory / 'env.sh', envrc)
    write(directory / '.envrc', f'source_env {shlex.quote(str(directory / "env.sh"))}\n')
    return directory


def previous_environment():
    encoded = os.environ.get('LOKI_ENV_STATE')
    if not encoded:
        return {}
    try:
        value = json.loads(base64.urlsafe_b64decode(encoded).decode())
        if not isinstance(value, dict) or any(not ENV_NAME.fullmatch(key) or
                (item is not None and not isinstance(item, str)) for key, item in value.items()):
            raise ValueError('invalid environment snapshot')
        return value
    except (ValueError, UnicodeError, TypeError) as error:
        raise ValueError('Invalid Loki environment snapshot; open a fresh terminal.') from error


def baseline_environment():
    baseline = dict(os.environ)
    baseline.pop('LOKI_ENV_STATE', None)
    for key, value in previous_environment().items():
        if value is None:
            baseline.pop(key, None)
        else:
            baseline[key] = value
    return baseline


def environment(name, tools=None):
    baseline = baseline_environment()
    if name is None:
        return baseline
    remove, values = registry().compose(core_api(), name, baseline, names=tools)
    values.update({'LOKI_HOME': str(root()), 'LOKI_PROFILE': name,
                   'DIRENV_CONFIG': baseline.get('DIRENV_CONFIG', str(Path.home() / '.config/direnv'))})
    owned = remove | set(values)
    snapshot = {key: baseline.get(key) for key in owned}
    result = {**{key: value for key, value in baseline.items() if key not in remove}, **values}
    result['LOKI_ENV_STATE'] = base64.urlsafe_b64encode(json.dumps(snapshot).encode()).decode()
    return result


def changes(name):
    result = environment(name)
    keys = set(previous_environment()) | {'LOKI_ENV_STATE'}
    if name is not None:
        # Snapshot keys describe exactly what this persona owns; unsupported tools are untouched.
        keys.update(json.loads(base64.urlsafe_b64decode(result['LOKI_ENV_STATE']).decode()))
    return {key for key in keys if key not in result}, {key: result[key] for key in keys if key in result}


def add(name, services, options=None):
    create_profile(name)
    catalog = registry()
    if services and services[0] == 'mcp':
        if len(services) != 3 or services[2].startswith('-'):
            raise ValueError('Use loki add mcp CLIENT SERVER_NAME.')
        tool = catalog.get(services[1])
        mcp = tool.data.get('mcp', {})
        if 'login' not in mcp:
            raise ValueError(f'{tool.id} does not define an MCP login command.')
        catalog.install(core_api(), name, [tool.id])
        Context(core_api(), tool, name, options).run(mcp['login'], extra={'server': services[2]})
        with edit_profile(name) as config:
            entries = config.setdefault('mcp_logins', {}).setdefault(tool.id, [])
            if services[2] not in entries:
                entries.append(services[2])
        return
    if not services:
        raise ValueError('Choose an integration from loki tools list.')
    for service in services:
        tool = catalog.get(service)
        if 'command' not in tool.data.get('login', {}) and 'login' not in tool.data.get('hooks', {}):
            raise ValueError(f'{service} has no login action; use loki tools install {service}.')
    catalog.install(core_api(), name, services)
    for service in services:
        tool = catalog.get(service)
        context = Context(core_api(), tool, name, options)
        print(f'Connecting {service} to {name}.', flush=True)
        if 'login' in tool.data.get('hooks', {}):
            tool.call('login', context)
        else:
            login = tool.data['login']
            context.run(login['command'], unset=login.get('unset_env', []))
        save_config(name, read_config(name))
        print(f'{service} connected for every {name} folder.')



def block(text):
    match = re.search(r'(?m)^# BEGIN lokishell\n.*?^# END lokishell(?:\n|$)', text, re.S)
    if (BEGIN in text or END in text) and not match:
        raise ValueError('The LokiShell block is incomplete; leaving the file unchanged.')
    return match


def choose(explicit=None):
    if explicit:
        profile_dir(explicit)
        return explicit
    selected, _, _ = effective()
    if selected:
        return selected
    if not sys.stdin.isatty():
        raise ValueError('Choose a profile explicitly: --profile personal or --profile work.')
    value = input('Profile [personal/work, or a new name]: ').strip()
    profile_dir(value)
    return value


def bind_folder(name, no_shell=False):
    directory = create_profile(name)
    path = Path.cwd() / '.envrc'
    if path.is_symlink():
        raise ValueError('Refusing to replace a symlinked .envrc.')
    previous = path.read_text() if path.exists() else ''
    match = block(previous)
    content = (f'{BEGIN}\n# profile: {name}\nexport LOKI_HOME={shlex.quote(str(root()))}\n'
               f'source_env {shlex.quote(str(directory / "env.sh"))}\n{END}\n')
    if match:
        updated = previous[:match.start()] + content + previous[match.end():]
    else:
        updated = previous + ('\n' if previous and not previous.endswith('\n') else '') + content
    write(path, updated)
    context = git_context()
    with edit_settings() as value:
        value['folders'][str(Path.cwd().resolve())] = name
        if context and context[1] == Path.cwd().resolve():
            common, top = context
            if str(common) not in value['repositories'] or common == top / '.git':
                value['repositories'][str(common)] = name
    subprocess.run(['direnv', 'allow', str(Path.cwd())], check=True)
    subprocess.run(['direnv', 'allow', str(directory)], check=True)
    if not no_shell:
        install_shell()
    print(f'{Path.cwd()} → {name}')
    print(f'Logins are shared by every folder linked to {name}. Run loki add SERVICE to connect one.')


def set_global(name, no_shell=False):
    create_profile(name)
    with edit_settings() as value:
        value['global_persona'] = name
    if not no_shell:
        install_shell()
    print(f'Global persona → {name}')
    active, source, _ = effective()
    if active != name:
        print(f'This {source} continues to use {active}.')


def hook(shell):
    from .shell import hook as shell_hook
    return shell_hook(core_api(), shell)


def install_shell():
    shell = Path(os.environ.get('SHELL', '/bin/zsh')).name
    if shell not in ('zsh', 'bash'):
        raise ValueError('Automatic setup supports zsh and bash. Run loki from one of these shells.')
    home = Path.home()
    zhome = Path(os.environ.get('ZDOTDIR', home)).expanduser()
    files = [zhome / '.zshenv', zhome / '.zshrc'] if shell == 'zsh' else [home / '.bashrc', home / '.bash_profile']
    line = f'eval "$({shlex.quote(executable())} hook {shell})"\n'
    content = f'{BEGIN}\n{line}{END}\n'
    for path in files:
        previous = path.read_text() if path.exists() else ''
        found = block(previous)
        updated = previous[:found.start()] + content + previous[found.end():] if found else previous + '\n' + content
        if updated != previous:
            if previous:
                backup = root() / 'backups'
                mkdir(backup)
                write(backup / f'{path.name}-{time.time_ns()}', previous)
            write(path, updated)
    with edit_settings() as value:
        value['shell_files'] = list(dict.fromkeys([*value.get('shell_files', []), *(str(path) for path in files)]))
    print(f'Shell integration installed for {shell}. Open a fresh terminal once if this shell has not loaded it.')






def clean(name, globally=False):
    if not globally:
        path = Path.cwd() / '.envrc'
        if path.is_symlink():
            raise ValueError('Refusing to replace a symlinked .envrc.')
        active, source, origin = effective()
        if active != name or origin != Path.cwd().resolve():
            raise ValueError('Run clean from the linked folder or worktree root. Nothing changed.')
        text = path.read_text() if path.exists() else ''
        found = block(text)
        if found and f'# profile: {name}\n' not in found.group():
            raise ValueError('This folder has no matching LokiShell pointer. Nothing changed.')
        if found:
            remaining = text[:found.start()] + text[found.end():]
            if remaining.strip():
                write(path, remaining)
                subprocess.run(['direnv', 'allow', str(Path.cwd())], check=True)
            else:
                path.unlink()
        with edit_settings() as value:
            # This opt-out keeps repository inheritance active for the other worktrees.
            value['folders'][str(Path.cwd().resolve())] = None
        print(f'Unlinked this folder. Shared {name} logins and other folders are unchanged.')
        print('This folder now follows the global persona.')
        return
    failures = registry().cleanup(core_api(), name)
    print(f'Cleared {name} managed tool logins across all linked folders, including disabled integrations.')
    print('Restart running agents; browser/app connections and provider-side grants are separate.')
    if failures:
        raise ValueError('Some logouts need attention: '+', '.join(failures))



def key_values(items):
    values = {}
    for item in items:
        key, separator, value = item.partition('=')
        if not separator or not NAME.fullmatch(key):
            raise ValueError('Use --set KEY=VALUE with a simple setting name.')
        values[key] = value
    return values


def entrypoint():
    os.umask(0o077)
    from .commands import main
    main(prog_name='loki')
