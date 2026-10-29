"""Discover tool-owned definitions and compose only enabled integrations."""
import importlib.util
from collections.abc import MutableMapping
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import string
import subprocess
import tomllib

CATALOG = Path(__file__).resolve().parents[2] / 'tools'
IDENTIFIER = re.compile(r'^[a-z][a-z0-9_-]*$')
ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
RESERVED = {'HOME', 'home', 'PATH', 'SHELL', 'BASH_ENV', 'ENV', 'ZDOTDIR'}
SHARED_CONFIG = {'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR'}
COMMAND = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._+-]*$')
SECTIONS = {'schema_version', 'id', 'name', 'description', 'depends_on', 'internal', 'legacy_default',
            'cli', 'env', 'state', 'login', 'cleanup', 'hooks', 'mcp', 'who'}


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value):
        raise ValueError(f'Invalid relative storage path: {value!r}')
    parts = PurePosixPath(value).parts
    if not parts or value.startswith('/') or any(p in ('.', '..') for p in value.split('/')):
        raise ValueError(f'Storage paths must stay inside a persona: {value!r}')
    return value


def env_name(value, scope='shell'):
    if not isinstance(value, str) or not ENV_NAME.fullmatch(value):
        raise ValueError(f'Invalid environment name: {value!r}')
    if value in RESERVED or value.startswith(('LOKI_', 'DIRENV_')):
        raise ValueError(f'Tools cannot own shell/core variable {value}')
    if scope == 'shell' and (value in SHARED_CONFIG or value == 'XDG_'):
        raise ValueError(f'Shared variable {value} requires env.scope = "process"')
    return value


def string_list(value, label):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f'{label} must be an array of strings')
    return value


def allowed_keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'Unexpected fields in {label}: {set(value) - set(allowed) if isinstance(value, dict) else value}')


class Tool:
    def __init__(self, path, origin='bundled'):
        self.path, self.origin = Path(path), origin
        try:
            self.data = tomllib.loads(self.path.read_text())
            self.validate()
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ValueError(f'{self.path}: {error}') from error
        self.id = self.data['id']
        self.module = None

    def validate(self):
        d = self.data
        allowed_keys(d, SECTIONS, 'tool')
        if d.get('schema_version') != 1 or not IDENTIFIER.fullmatch(d.get('id', '')):
            raise ValueError('Use schema_version = 1 and a lowercase tool id')
        for key in ('name', 'description'):
            if not isinstance(d.get(key), str) or not d[key]:
                raise ValueError(f'{key} is required')
        string_list(d.get('depends_on', []), 'depends_on')
        cli = d.get('cli', {})
        allowed_keys(cli, {'command', 'commands', 'install_hint', 'optional'}, 'cli')
        if cli and not isinstance(cli.get('command'), str):
            raise ValueError('cli.command must name the native executable')
        for command in [*string_list(cli.get('commands', []), 'cli.commands'), *([cli['command']] if cli else [])]:
            if not COMMAND.fullmatch(command) or command in ('loki', 'direnv'):
                raise ValueError('CLI commands must be executable names, excluding loki and direnv')
        env = d.get('env', {})
        allowed_keys(env, {'scope', 'unset', 'unset_prefixes', 'set', 'from_file', 'from_config'}, 'env')
        scope = env.get('scope', 'shell')
        if scope not in ('shell', 'process'):
            raise ValueError('env.scope must be shell or process')
        if scope == 'process' and not cli and not d.get('internal', False):
            raise ValueError('Process-scoped integrations need cli.command')
        for key in ('set', 'from_file', 'from_config'):
            if not isinstance(env.get(key, {}), dict):
                raise ValueError(f'env.{key} must be a table')
        for key in ('unset', 'unset_prefixes'):
            for name in string_list(env.get(key, []), f'env.{key}'):
                env_name(name, scope)
        for name, value in env.get('set', {}).items():
            env_name(name, scope)
            if not isinstance(value, str):
                raise ValueError(f'env.set.{name} must be a string')
        for kind in ('from_file', 'from_config'):
            for name, value in env.get(kind, {}).items():
                env_name(name, scope)
                source = 'path' if kind == 'from_file' else 'key'
                allowed_keys(value, {source, 'default'}, f'env.{kind}.{name}')
                if not isinstance(value.get(source), str) or not isinstance(value.get('default', ''), str):
                    raise ValueError(f'env.{kind}.{name} needs string {source} and default')
                if kind == 'from_file':
                    relative_path(value['path'])
        state = d.get('state', {})
        allowed_keys(state, {'directories', 'files'}, 'state')
        for path in string_list(state.get('directories', []), 'state.directories'):
            relative_path(path)
        for file in state.get('files', []):
            allowed_keys(file, {'path', 'content'}, 'state.files entry')
            relative_path(file['path'])
            if not isinstance(file.get('content'), str):
                raise ValueError('state.files.content must be a string')
        for section, keys in [('login', {'command', 'unset_env'}),
                              ('cleanup', {'command', 'when_nonempty', 'files', 'directories'}),
                              ('mcp', {'login', 'logout'})]:
            value = d.get(section, {})
            allowed_keys(value, keys, section)
            commands = ('login', 'logout') if section == 'mcp' else ('command',)
            for key in commands:
                if key in value and not string_list(value[key], f'{section}.{key}'):
                    raise ValueError(f'{section}.{key} cannot be empty')
        for name in d.get('login', {}).get('unset_env', []):
            env_name(name, 'process')
        cleanup = d.get('cleanup', {})
        for key in ('files', 'directories'):
            for path in string_list(cleanup.get(key, []), f'cleanup.{key}'):
                relative_path(path)
        if 'when_nonempty' in cleanup:
            relative_path(cleanup['when_nonempty'])
        hooks = d.get('hooks', {})
        allowed_keys(hooks, {'prepare', 'login', 'cleanup', 'credential', 'who'}, 'hooks')
        who = d.get('who', {})
        allowed_keys(who, {'command', 'timeout', 'signed_out_exit_codes'}, 'who')
        if 'command' in who and not string_list(who['command'], 'who.command'):
            raise ValueError('who.command cannot be empty')
        timeout = who.get('timeout', 10)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError('who.timeout must be between 0 and 60 seconds')
        codes = who.get('signed_out_exit_codes', [])
        if not isinstance(codes, list) or any(type(code) is not int or not 1 <= code <= 255 for code in codes):
            raise ValueError('who.signed_out_exit_codes must contain exit codes from 1 to 255')
        if who and 'command' not in who and 'who' not in hooks:
            raise ValueError('who needs a command or a Python hook')
        if hooks and not (self.path.parent / 'adapter.py').is_file():
            raise ValueError('Declared hooks require adapter.py beside tool.toml')
        for function in hooks.values():
            if not isinstance(function, str) or not function.isidentifier() or function.startswith('_'):
                raise ValueError('Hook functions must be public Python identifiers')

    def call(self, hook, context):
        function = self.data.get('hooks', {}).get(hook)
        if not function:
            return None
        if self.module is None:
            file = self.path.parent / 'adapter.py'
            spec = importlib.util.spec_from_file_location(f'lokishell_tool_{self.id}', file)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        callback = getattr(self.module, function, None)
        if not callable(callback):
            raise ValueError(f'{self.id}: adapter.py has no callable {function}')
        return callback(context)


class Context:
    """Small API available to an integration's optional Python hooks."""
    def __init__(self, core, tool, persona, options=None, args=None):
        self.core, self.tool, self.persona = core, tool, persona
        self.directory = core.profile_dir(persona)
        self.options, self.args = options or {}, args or []

    @property
    def config(self):
        return self.core.read_config(self.persona)

    def setting(self, key, default=''):
        config = self.config
        return config.get('tool_config', {}).get(self.tool.id, {}).get(key, config.get(key, default))

    def path(self, relative):
        path = self.directory / relative_path(relative)
        self.core.check_storage_path(path)
        return path

    def write(self, relative, content):
        path = self.path(relative)
        self.core.mkdir(path.parent)
        self.core.write(path, content)

    def render(self, value, extra=None):
        literals = {'profile': str(self.directory), 'persona': self.persona, 'id': self.tool.id,
                    'home': str(Path.home()), 'loki': self.core.executable(),
                    'loki_quoted': shlex.quote(self.core.executable()), **(extra or {})}
        result = []
        for literal, field, spec, conversion in string.Formatter().parse(value):
            result.append(literal)
            if field is None:
                continue
            if spec or conversion:
                raise ValueError('Template format specifiers/conversions are not supported')
            if field.startswith('config.'):
                replacement = self.setting(field[7:])
            elif field.startswith('option.'):
                replacement = self.options[field[7:]]
            elif field in literals:
                replacement = literals[field]
            else:
                raise ValueError(f'{self.tool.id}: unknown template {{{field}}}')
            result.append(str(replacement))
        return ''.join(result)

    def run(self, command, unset=(), extra=None):
        from .dispatch import native_command
        env = self.core.environment(self.persona, tools=[self.tool.id])
        for name in unset:
            env.pop(name, None)
        argv = [self.render(part, extra) for part in command]
        native = native_command(self.core, argv[0], env)
        if not native:
            hint = self.tool.data.get('cli', {}).get('install_hint', 'Install the native CLI first.')
            raise ValueError(f'{self.tool.id}: {argv[0]} is not installed. {hint}')
        argv[0] = native
        subprocess.run(argv, env=env, check=True)


class ToolCatalog(MutableMapping):
    """Discover paths cheaply; parse only the integrations an operation needs."""
    def __init__(self):
        self.paths, self.loaded = {}, {}

    def __getitem__(self, name):
        if name not in self.loaded:
            path, origin = self.paths[name]
            tool = Tool(path, origin)
            if path.parent.name != tool.id:
                raise ValueError(f'{path}: the directory name must match id = {tool.id!r}')
            self.loaded[name] = tool
        return self.loaded[name]

    def __setitem__(self, name, tool):
        self.paths[name] = (tool.path, tool.origin)
        self.loaded[name] = tool

    def __delitem__(self, name):
        del self.paths[name]
        self.loaded.pop(name, None)

    def __iter__(self):
        return iter(self.paths)

    def __len__(self):
        return len(self.paths)

    def __contains__(self, name):
        return name in self.paths


class Registry:
    def __init__(self, user_root, catalog=CATALOG):
        self.user_root, self.tools = Path(user_root), ToolCatalog()
        for directory, origin in ((Path(catalog), 'bundled'), (self.user_root / 'tools', 'user')):
            for path in sorted(directory.glob('*/tool.toml')):
                self.tools.paths[path.parent.name] = (path, origin)

    def get(self, name):
        if name not in self.tools:
            raise ValueError(f'Unknown tool {name!r}. Run loki tools catalog.')
        return self.tools[name]

    def legacy_roots(self):
        return [name for name, tool in self.tools.items() if tool.data.get('legacy_default', False)]

    def roots(self, config):
        return config['tools'] if 'tools' in config else self.legacy_roots()

    def resolve(self, names):
        ordered, active = [], set()
        def visit(name):
            if name in active:
                raise ValueError(f'Circular tool dependency involving {name}')
            if name in ordered:
                return
            tool = self.get(name)
            active.add(name)
            for dependency in tool.data.get('depends_on', []):
                visit(dependency)
            active.remove(name)
            ordered.append(name)
        for name in names:
            visit(name)
        return ordered

    def enabled(self, config):
        return self.resolve(self.roots(config))

    def prepare(self, core, persona, names):
        for name in self.resolve(names):
            tool = self.get(name)
            context = Context(core, tool, persona)
            state = tool.data.get('state', {})
            for path in state.get('directories', []):
                core.mkdir(context.path(path))
            for file in state.get('files', []):
                path = context.path(file['path'])
                core.mkdir(path.parent)
                if not path.exists():
                    core.write(path, context.render(file['content']))
            tool.call('prepare', context)
        from .dispatch import prepare
        prepare(core, self, names)

    def commands(self, names):
        commands = {}
        for name in self.resolve(names):
            tool = self.get(name)
            cli = tool.data.get('cli', {})
            if not cli or not any(self.get(part).data.get('env', {}).get('scope') == 'process'
                                  for part in self.resolve([name])):
                continue
            for command in [cli['command'], *cli.get('commands', [])]:
                commands.setdefault(command, []).append(name)
        return commands

    def compose(self, core, persona, baseline, names=None, shell=False):
        remove, values, owners = set(), {}, {}
        config = core.read_config(persona)
        names = self.enabled(config) if names is None else self.resolve(names)
        for name in names:
            tool = self.get(name)
            context = Context(core, tool, persona)
            env = tool.data.get('env', {})
            if shell and env.get('scope') == 'process':
                continue
            remove.update(env.get('unset', []))
            for prefix in env.get('unset_prefixes', []):
                for key in baseline:
                    if key.startswith(prefix):
                        env_name(key, env.get('scope', 'shell'))
                        remove.add(key)
            produced = {key: context.render(value) for key, value in env.get('set', {}).items()}
            for key, source in env.get('from_file', {}).items():
                path = context.path(source['path'])
                produced[key] = path.read_text().strip() if path.is_file() else source.get('default', '')
            for key, source in env.get('from_config', {}).items():
                produced[key] = str(context.setting(source['key'], source.get('default', '')))
            for key, value in produced.items():
                if key in values and values[key] != value:
                    raise ValueError(f'{owners[key]} and {name} set conflicting values for {key}')
                values[key], owners[key] = value, name
        return remove, values

    def install(self, core, persona, names):
        self.resolve(names)
        roots = list(dict.fromkeys([*self.roots(core.read_config(persona)), *names]))
        self.validate_environments(core, persona, roots)
        self.prepare(core, persona, roots)
        # Merge again under the lock: another agent may have installed a different integration.
        with core.edit_profile(persona) as config:
            roots = list(dict.fromkeys([*self.roots(config), *names]))
            self.validate_environments(core, persona, roots)
            config['tools'] = roots
            config['managed_tools'] = list(dict.fromkeys([*config.get('managed_tools', []), *self.resolve(roots)]))

    def validate_environments(self, core, persona, roots):
        baseline = core.baseline_environment()
        self.compose(core, persona, baseline, roots, shell=True)
        for names in self.commands(roots).values():
            self.compose(core, persona, baseline, names)

    def remove(self, core, persona, names):
        for name in names:
            self.get(name)
        with core.edit_profile(persona) as config:
            roots = [name for name in self.roots(config) if name not in names]
            still_needed = set(self.resolve(roots)) & set(names)
            if still_needed:
                raise ValueError('Other enabled tools still require: '+', '.join(sorted(still_needed)))
            config['tools'] = roots

    def cleanup_contexts(self, core, persona):
        config = core.read_config(persona)
        managed = list(dict.fromkeys([*config.get('managed_tools', []), *self.enabled(config)]))
        contexts = [Context(core, self.get(name), persona) for name in self.resolve(managed)]
        # Validate all paths before changing any of them.
        for context in contexts:
            cleanup = context.tool.data.get('cleanup', {})
            for part in cleanup.get('files', []) + cleanup.get('directories', []):
                context.path(part)
            if cleanup.get('when_nonempty'):
                context.path(cleanup['when_nonempty'])
        return contexts

    def cleanup(self, core, persona):
        config = core.read_config(persona)
        contexts = self.cleanup_contexts(core, persona)
        failures = []
        for context in reversed(contexts):
            tool, cleanup = context.tool, context.tool.data.get('cleanup', {})
            mcp = tool.data.get('mcp', {})
            if 'logout' in mcp:
                for server in config.get('mcp_logins', {}).get(tool.id, []):
                    try:
                        context.run(mcp['logout'], extra={'server': server})
                    except (OSError, ValueError, subprocess.CalledProcessError):
                        failures.append(f'{tool.data["name"]} MCP logout: {server}')
            condition = cleanup.get('when_nonempty')
            should_run = not condition or (context.path(condition).is_dir() and any(context.path(condition).iterdir()))
            if should_run:
                try:
                    if 'command' in cleanup:
                        context.run(cleanup['command'])
                    tool.call('cleanup', context)
                except (OSError, ValueError, subprocess.CalledProcessError):
                    failures.append(f'{tool.data["name"]} logout')
            for part in cleanup.get('files', []):
                context.path(part).unlink(missing_ok=True)
            for part in cleanup.get('directories', []):
                path = context.path(part)
                if path.exists():
                    shutil.rmtree(path)
                core.mkdir(path)
        core.save_config(persona, core.read_config(persona))
        return failures

    def import_local(self, core, source, replace=False):
        source = Path(source).expanduser().resolve()
        path = source / 'tool.toml' if source.is_dir() else source
        tool = Tool(path, 'user')
        if tool.id in self.tools and not replace:
            raise ValueError(f'{tool.id} already exists. Use --replace to explicitly override its definition.')
        # Validate the new definition and its dependencies before committing it.
        old = self.tools.get(tool.id)
        self.tools[tool.id] = tool
        try:
            self.resolve([tool.id])
        except Exception:
            if old is None:
                self.tools.pop(tool.id)
            else:
                self.tools[tool.id] = old
            raise
        destination = self.user_root / 'tools' / tool.id
        # Check every source and destination before changing a previously working definition.
        for filename in ('tool.toml', 'adapter.py', 'README.md'):
            original = path.parent / filename
            if original.is_symlink():
                raise ValueError(f'Refusing a linked tool file: {original}')
            core.check_storage_path(destination / filename)
        core.mkdir(destination)
        # Publish the manifest last, after its hook implementation is present.
        for filename in ('adapter.py', 'README.md', 'tool.toml'):
            original = path.parent / filename
            if original.exists():
                if original.is_symlink():
                    raise ValueError(f'Refusing a linked tool file: {original}')
                core.write(destination / filename, original.read_text())
            elif (destination / filename).exists():
                (destination / filename).unlink()
        self.tools[tool.id] = Tool(destination / 'tool.toml', 'user')
        return tool.id
