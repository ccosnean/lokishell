"""PATH launchers apply a tool's environment only to that command and its children."""
import os
import shlex
import shutil
import sys

HEADER = '#!/bin/sh\n# LokiShell command launcher v2\n'


def ensure_prepared(core, catalog, names):
    """Validate without repairing: shell startup must work with read-only stores."""
    for command in catalog.commands(names):
        path = core.root() / 'bin' / command
        core.check_storage_path(path)
        if not path.is_file() or not os.access(path, os.X_OK) or not path.read_text().startswith(HEADER):
            raise ValueError('Command launchers need setup. Run loki setup in your regular terminal, then retry. '
                             'No configuration was changed.')


def without_launchers(core, path, env):
    owned = {str(core.root() / 'bin'), env.get('LOKI_SHIM_PATH')}
    return os.pathsep.join(part for part in path.split(os.pathsep) if part not in owned)


def native_command(core, command, env):
    return shutil.which(command, path=without_launchers(core, env.get('PATH', os.defpath), env))


def prepare(core, catalog, names):
    commands = catalog.commands(names)
    if not commands:
        return
    directory = core.root() / 'bin'
    core.mkdir(directory)
    for command in commands:
        path = directory / command
        core.check_storage_path(path)
        # Use the running interpreter directly: a custom Python integration
        # must not intercept Loki's own /usr/bin/env python3 shebang.
        # Runtime paths come from the active shell so upgrades do not require
        # rewriting launchers during read-only profile loading. The fallback
        # also permits direct invocation outside an initialized shell.
        content = (HEADER + '_loki_python=${LOKI_PYTHON:-}\n_loki_executable=${LOKI_EXECUTABLE:-}\n'
                   + '[ -n "$_loki_python" ] || _loki_python=' + shlex.quote(sys.executable) + '\n'
                   + '[ -n "$_loki_executable" ] || _loki_executable=' + shlex.quote(core.executable()) + '\n'
                   + 'exec "$_loki_python" "$_loki_executable" _run-command ' + shlex.quote(command) + ' "$@"\n')
        if not path.exists() or path.read_text() != content:
            core.write(path, content, mode=0o700)


def run(core, command, args):
    persona = os.environ.get('LOKI_PROFILE') or core.effective()[0]
    if persona:
        catalog = core.registry()
        names = catalog.commands(catalog.roots(core.read_config(persona))).get(command)
        env = core.environment(persona, tools=names) if names else core.environment(persona)
    else:
        env = core.baseline_environment()
    executable = native_command(core, command, env)
    if not executable:
        raise ValueError(f'Native CLI {command!r} is not installed. Install it separately.')
    os.execve(executable, [executable, *args], env)
