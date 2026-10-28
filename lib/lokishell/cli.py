#!/usr/bin/env python3
"""LokiShell: folder-selected account environments for command-line tools."""
import argparse
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

from .registry import Registry, Context, Tool, ENV_NAME

VERSION = '0.3.0'
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
        raise ValueError(f'Profile {name!r} does not exist. Run loki init --profile {name}.')
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


def init(name, no_shell=False):
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


def shift(name, no_shell=False):
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
    loki, direnv = shlex.quote(executable()), shlex.quote(shutil.which('direnv') or 'direnv')
    if shell == 'zsh':
        return f'''# LokiShell: interactive prompts and fresh noninteractive agent shells.
+if [[ -z ${{_loki_zsh_loaded-}} ]]; then
+  typeset _loki_zsh_loaded=1
+  _loki_refresh() {{ eval "$({direnv} export zsh)"; eval "$({loki} _effective-env)"; }}
+  loki() {{ command {loki} "$@"; local loki_rc=$?; _loki_refresh; return $loki_rc; }}
+  typeset -ga chpwd_functions precmd_functions
+  chpwd_functions+=(_loki_refresh)
+  precmd_functions+=(_loki_refresh)
+  _loki_refresh
+fi
+'''.replace('\n+', '\n')
    if shell == 'bash':
        return f'''if [ -z "${{_loki_bash_loaded-}}" ]; then
+  _loki_bash_loaded=1
+  _loki_refresh() {{ eval "$({direnv} export bash)"; eval "$({loki} _effective-env)"; }}
+  loki() {{ command {loki} "$@"; local loki_rc=$?; _loki_refresh; return "$loki_rc"; }}
+  cd() {{ builtin cd "$@" || return; _loki_refresh; }}
+  eval "$({direnv} hook bash)"
+  _loki_refresh
+fi
+'''.replace('\n+', '\n')
    raise ValueError('Supported shells: zsh, bash.')


def install_shell():
    shell = Path(os.environ.get('SHELL', '/bin/zsh')).name
    if shell not in ('zsh', 'bash'):
        raise ValueError('Automatic setup supports zsh and bash. Use loki exec for other runners.')
    home = Path.home()
    files = [home / '.zshenv', home / '.zshrc'] if shell == 'zsh' else [home / '.bashrc', home / '.bash_profile']
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


def tools_command(opts):
    catalog = registry()
    action = opts.tool_action
    persona = opts.profile or effective()[0]
    if action == 'validate':
        if opts.path:
            path = Path(opts.path).expanduser()
            tool = Tool(path / 'tool.toml' if path.is_dir() else path)
            catalog.tools[tool.id] = tool
            catalog.resolve(list(catalog.tools))
        print(f'Validated {len(catalog.tools)} tool definitions.')
        return
    if action == 'show':
        tool = catalog.get(opts.tool)
        data = tool.data
        env = data.get('env', {})
        print(json.dumps({'id': tool.id, 'name': data['name'], 'description': data['description'],
                          'definition': str(tool.path), 'origin': tool.origin,
                          'depends_on': data.get('depends_on', []),
                          'variables': sorted(set(env.get('set', {})) | set(env.get('from_file', {})) |
                                              set(env.get('from_config', {}))),
                          'clears': env.get('unset', []), 'clears_prefixes': env.get('unset_prefixes', []),
                          'cli': data.get('cli', {}), 'login': data.get('login', {}),
                          'hooks': list(data.get('hooks', {}))}, indent=2))
        return
    if action == 'list':
        config = read_config(persona) if persona else {'tools': []}
        enabled = catalog.enabled(config)
        selected = catalog.roots(config)
        rows = []
        for tool in catalog.tools.values():
            if tool.data.get('internal', False) and not opts.all:
                continue
            command = tool.data.get('cli', {}).get('command')
            rows.append({'id': tool.id, 'name': tool.data['name'], 'enabled': tool.id in enabled,
                         'selected': tool.id in selected, 'origin': tool.origin,
                         'cli': command, 'cli_available': bool(shutil.which(command)) if command else True})
        if opts.json:
            print(json.dumps({'persona': persona, 'tools': rows}, indent=2))
        else:
            print(f'Tools for {persona or "no selected persona"}:')
            for row in rows:
                state = 'enabled' if row['enabled'] else 'available'
                native = '' if row['cli_available'] else ' (native CLI not installed)'
                print(f'  {row["id"]:12} {state:10} {row["name"]}{native}')
        return
    persona = choose(opts.profile)
    create_profile(persona)
    if action == 'install':
        names = list(opts.tools)
        if opts.source:
            names.append(catalog.import_local(core_api(), opts.source, replace=opts.replace))
        elif opts.replace:
            raise ValueError('--replace requires --from PATH.')
        if not names:
            raise ValueError('Name a tool or use --from PATH. See loki tools list.')
        catalog.install(core_api(), persona, names)
        print(f'Enabled for {persona}: '+', '.join(names))
        for name in names:
            native = catalog.get(name).data.get('cli', {})
            if native.get('command') and not shutil.which(native['command']):
                print(f'{name}: native CLI missing. '+native.get('install_hint', 'Install it separately.'))
    elif action == 'remove':
        catalog.remove(core_api(), persona, opts.tools)
        print(f'Disabled for {persona}: '+', '.join(opts.tools)+'. Saved credentials are retained.')
    elif action == 'configure':
        catalog.get(opts.tool)
        with edit_profile(persona) as config:
            config.setdefault('tool_config', {}).setdefault(opts.tool, {}).update(key_values(opts.options))
        if opts.tool in catalog.enabled(read_config(persona)):
            catalog.prepare(core_api(), persona, [opts.tool])
        print(f'Updated {opts.tool} settings for {persona}.')



def main():
    os.umask(0o077)
    args = sys.argv[1:]
    if args and args[0] in ('_env', '_effective-env'):
        name = args[1] if args[0] == '_env' else effective()[0]
        remove, values = changes(name)
        for key in sorted(remove):
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                print('unset '+key)
        for key, value in values.items():
            print('export '+key+'='+shlex.quote(value))
        return
    if args and args[0] == '_git-credential':
        # Compatibility with Git helper commands written by releases before the registry.
        args = ['_tool', 'github', 'credential', *args[1:]]
    if args and args[0] == '_tool':
        if len(args) < 3:
            raise ValueError('Internal tool command requires an integration and hook.')
        name = os.environ.get('LOKI_PROFILE') or effective()[0]
        if not name:
            return
        catalog = registry()
        tool = catalog.get(args[1])
        if tool.id not in catalog.enabled(read_config(name)):
            return
        tool.call(args[2], Context(core_api(), tool, name, args=args[3:]))
        return
    parser = argparse.ArgumentParser(description='Shapeshift CLI personas globally, by folder, and across worktrees.')
    parser.add_argument('--version', action='version', version='LokiShell '+VERSION)
    parser.add_argument('--profile', '-p', help='Account profile; otherwise use this folder or ask')
    sub = parser.add_subparsers(dest='action', required=True)
    init_parser = sub.add_parser('init', help='Use the global persona in this folder')
    init_parser.add_argument('--profile', '-p', dest='local_profile')
    init_parser.add_argument('--no-shell', action='store_true', help='Skip shell startup file changes')
    for action, description in (('shift', 'Create/select the global persona'), ('use', 'Use a persona in this folder')):
        persona_parser = sub.add_parser(action, help=description)
        persona_parser.add_argument('persona')
        persona_parser.add_argument('--no-shell', action='store_true', help='Skip shell startup file changes')
    add_parser = sub.add_parser('add', help='Connect a service for all folders of this profile')
    add_parser.add_argument('services', nargs='+')
    add_parser.add_argument('--user', help='Username option passed to the selected integration')
    add_parser.add_argument('--set', action='append', default=[], dest='options', metavar='KEY=VALUE')
    tools_parser = sub.add_parser('tools', help='Browse and enable tool integrations per persona')
    tool_actions = tools_parser.add_subparsers(dest='tool_action', required=True)
    tool_list = tool_actions.add_parser('list', help='List available and enabled integrations')
    tool_list.add_argument('--json', action='store_true')
    tool_list.add_argument('--all', action='store_true', help='Include shared dependency definitions')
    tool_show = tool_actions.add_parser('show', help='Show a tool’s environment names and login definition')
    tool_show.add_argument('tool')
    tool_validate = tool_actions.add_parser('validate', help='Validate catalog definitions, or a local definition')
    tool_validate.add_argument('path', nargs='?')
    tool_install = tool_actions.add_parser('install', help='Enable integrations, without installing native executables')
    tool_install.add_argument('tools', nargs='*')
    tool_install.add_argument('--from', dest='source', help='Install a local tool.toml or tool directory')
    tool_install.add_argument('--replace', action='store_true', help='Explicitly override an existing definition')
    tool_remove = tool_actions.add_parser('remove', help='Disable integrations; keep saved credentials')
    tool_remove.add_argument('tools', nargs='+')
    tool_configure = tool_actions.add_parser('configure', help='Set persona-specific integration options')
    tool_configure.add_argument('tool')
    tool_configure.add_argument('--set', action='append', default=[], dest='options', metavar='KEY=VALUE', required=True)
    clean_parser = sub.add_parser('clean', help='Unlink this folder; --global clears profile logins')
    clean_parser.add_argument('--global', dest='globally', action='store_true')
    sub.add_parser('status', help='Show profile paths, never credentials')
    sub.add_parser('setup', help='Install shell integration without linking a folder')
    hook_parser = sub.add_parser('hook', help='Print shell integration')
    hook_parser.add_argument('shell', choices=('zsh', 'bash'))
    exec_parser = sub.add_parser('exec', help='Select a profile explicitly for an isolated runner')
    exec_parser.add_argument('command', nargs=argparse.REMAINDER)
    opts = parser.parse_args(args)
    if opts.action == 'tools':
        tools_command(opts)
        return
    if opts.action == 'hook':
        print(hook(opts.shell), end='')
        return
    if opts.action == 'setup':
        mkdir(root())
        install_shell()
        return
    if opts.action == 'init':
        name = getattr(opts, 'local_profile', None) or opts.profile or settings()['global_persona'] or 'personal'
        if settings()['global_persona'] is None:
            shift(name, no_shell=True)
        init(name, opts.no_shell)
        return
    if opts.action in ('shift', 'use'):
        if opts.action == 'shift':
            shift(opts.persona, opts.no_shell)
        else:
            init(opts.persona, opts.no_shell)
        return
    name = choose(opts.profile)
    if opts.action == 'add':
        options = key_values(opts.options)
        if opts.user:
            options['user'] = opts.user
        add(name, opts.services, options)
    elif opts.action == 'clean':
        clean(name, opts.globally)
    elif opts.action == 'status':
        directory = profile_dir(name)
        config = read_config(name)
        active, source, folder = effective()
        print(json.dumps({'profile': name, 'active_persona': active, 'source': source,
                          'global_persona': settings()['global_persona'],
                          'folder': str(folder) if folder else None,
                          'storage': str(directory), 'tools': registry().enabled(config)}, indent=2))
    elif opts.action == 'exec':
        command = opts.command[1:] if opts.command[:1] == ['--'] else opts.command
        if not command:
            raise ValueError('Supply a command after exec.')
        os.execvpe(command[0], command, environment(name))


def entrypoint():
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'loki: {error}', file=sys.stderr)
        sys.exit(1)
