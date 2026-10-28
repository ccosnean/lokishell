"""Explicit, interactive reset of all locally managed LokiShell state."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import click


def interactive_input():
    return sys.stdin.isatty()


def reset_plan(core):
    storage = core.root()
    core.check_storage_path(storage)
    home = Path.home().resolve()
    resolved = storage.resolve()
    protected = {Path('/'), home, home / '.config', Path.cwd().resolve()}
    if resolved in protected or home.is_relative_to(resolved) or Path.cwd().resolve().is_relative_to(resolved):
        raise ValueError(f'Refusing to erase an unsafe LOKI_HOME: {storage}')
    # Never recursively remove an arbitrary directory supplied through LOKI_HOME.
    if storage.exists():
        known = {'settings.json', '.settings.lock', 'profiles', 'tools', 'backups'}
        unknown = {path.name for path in storage.iterdir()} - known
        if unknown:
            raise ValueError('Unexpected files in LOKI_HOME; nothing changed: '+', '.join(sorted(unknown)))
    settings = core.settings()
    personas = []
    for directory in sorted((storage / 'profiles').glob('*')):
        core.check_storage_path(directory)
        if directory.is_dir() and (directory / 'profile.json').is_file():
            core.read_config(directory.name)
            personas.append(directory.name)
    catalog = core.registry()
    for persona in personas:
        catalog.cleanup_contexts(core, persona)  # Preflight every persona before any logout.
    zhome = Path(os.environ.get('ZDOTDIR', home)).expanduser()
    shells = set(settings.get('shell_files', [])) | {
        str(home / '.zshenv'), str(home / '.zshrc'), str(home / '.bashrc'), str(home / '.bash_profile'),
        str(zhome / '.zshenv'), str(zhome / '.zshrc')}
    paths = {Path(folder) / '.envrc' for folder in settings.get('folders', {})}
    paths.update(Path(path) for path in shells)
    edits = []
    for path in sorted(paths):
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink():
            # A symlink to an unrelated shell file is not ours to edit.
            if path.is_file() and core.BEGIN not in path.read_text():
                continue
            raise ValueError(f'Refusing to change a linked setup file: {path}')
        previous = path.read_text()
        found = core.block(previous)
        if found:
            remaining = previous[:found.start()] + previous[found.end():]
            edits.append((path, previous, remaining))
    return storage, personas, edits, catalog


def reset(core):
    storage, personas, edits, catalog = reset_plan(core)
    if not storage.exists() and not edits:
        click.echo('LokiShell is already in a fresh-install state.')
        return
    click.echo(f'Reset ALL LokiShell personas in {storage}')
    click.echo('You will permanently lose:')
    click.echo('  - Personas: '+(', '.join(personas) or 'none'))
    click.echo('  - Saved credentials and MCP tokens managed by these personas')
    click.echo('  - Agent sessions/history and every tool setting stored inside LokiShell')
    click.echo('  - Custom tool definitions, global/folder/worktree assignments, and setup backups')
    click.echo('LokiShell blocks will be removed from:')
    for path, _, _ in edits:
        click.echo(f'  - {path}')
    if not edits:
        click.echo('  (no registered setup files found)')
    click.echo('Project files, other shell settings, and installed CLI binaries are kept.')
    click.echo('Browser sessions and provider-side access grants are not revoked.')
    click.echo('Stop running agents before resetting; they may hold tokens or recreate files.')
    if not interactive_input():
        raise click.ClickException('Reset requires an interactive terminal and two confirmations. Nothing changed.')
    click.confirm('1/2: Erase all of the LokiShell data listed above?', default=False, abort=True)
    phrase = click.prompt('2/2: Type RESET LOKISHELL to confirm', default='', show_default=False)
    if phrase != 'RESET LOKISHELL':
        raise click.ClickException('Confirmation did not match. Nothing changed.')
    # Recheck the reviewed files so edits made while the prompts were open survive.
    new_storage, new_personas, new_edits, catalog = reset_plan(core)
    if (new_storage, new_personas, new_edits) != (storage, personas, edits):
        raise click.ClickException('LokiShell setup changed during confirmation. Nothing changed; run reset again.')
    failures = []
    for persona in personas:
        failures.extend(f'{persona}: {item}' for item in catalog.cleanup(core, persona))
    if failures:
        raise click.ClickException('Reset stopped after some logouts failed. Configuration is retained so you can retry: '
                                   +', '.join(failures))
    # Revoke direnv trust while the original .envrc contents still exist.
    trusted_paths = [path for path, _, _ in edits if path.name == '.envrc']
    trusted_paths += [core.profile_dir(persona) / '.envrc' for persona in personas]
    for path in trusted_paths:
        if path.is_file() and shutil.which('direnv'):
            result = subprocess.run(['direnv', 'deny', str(path.parent)], capture_output=True,
                                    text=True, env=core.baseline_environment())
            # Older direnv returns ENOENT when an edited .envrc has no approval.
            # That is already denied; actual permission/storage failures must stop reset.
            absent = ('remove ' in result.stderr and '/allow/' in result.stderr
                      and 'no such file or directory' in result.stderr)
            if result.returncode and not absent:
                raise click.ClickException(f'Could not remove direnv approval for {path}: {result.stderr.strip()}')
    for path, _, remaining in edits:
        if remaining.strip():
            core.write(path, remaining)
        else:
            path.unlink()
    if storage.exists():
        shutil.rmtree(storage)
    click.echo('LokiShell reset. All managed personas and data were removed.')
    click.echo('Open a fresh terminal before setting up again. Preserved .envrc files may need direnv allow.')
