"""Click commands; account and shell behavior stays in the core module."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import click

from . import cli as core
from .registry import Context, Tool

CONTRIBUTE_URL = 'https://github.com/ccosnean/lokishell/blob/main/CONTRIBUTING.md'


class LokiGroup(click.Group):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
            raise click.ClickException(str(error)) from error


@click.group(cls=LokiGroup, context_settings={'help_option_names': ['-h', '--help']})
@click.version_option(core.VERSION, prog_name='LokiShell', message='%(prog)s %(version)s')
@click.option('--profile', '-p', help='Manage this persona instead of the current one.')
@click.pass_context
def main(ctx, profile):
    """Use CLI personas globally, by folder, and across worktrees."""
    ctx.ensure_object(dict)
    ctx.obj['profile'] = profile


def selected_profile():
    return click.get_current_context().find_root().obj.get('profile')


@main.command('create')
@click.argument('persona')
def create_command(persona):
    """Create a persona without selecting it."""
    existed = (core.profile_dir(persona) / 'profile.json').exists()
    core.create_profile(persona)
    click.echo(f'Persona {persona} already exists.' if existed else f'Created persona {persona}.')
    click.echo(f'Use loki use {persona}, or loki use {persona} --global, to select it.')


@main.command('use')
@click.argument('persona', required=False)
@click.option('--global', 'globally', is_flag=True, help='Set the global default instead of this folder.')
@click.option('--no-shell', is_flag=True, help='Skip shell startup file changes.')
def use_command(persona, globally, no_shell):
    """Use a persona; omit its name to use the global persona.

    A named persona is created if it does not exist. Folder and worktree
    assignments override the global default.
    """
    name = persona or selected_profile() or core.settings()['global_persona']
    if not name:
        raise click.ClickException('No global persona selected. Run loki use NAME --global first.')
    if globally:
        core.set_global(name, no_shell)
    else:
        core.bind_folder(name, no_shell)


@main.command('add')
@click.argument('services', nargs=-1, required=True)
@click.option('--user', help='Username passed to the integration.')
@click.option('--set', 'options', multiple=True, metavar='KEY=VALUE', help='Option for this login.')
def add_command(services, user, options):
    """Enable a tool and connect its account for this persona."""
    values = core.key_values(options)
    if user:
        values['user'] = user
    core.add(core.choose(selected_profile()), services, values)


@main.group('tools')
def tools_group():
    """Browse and manage tool integrations for a persona."""


@tools_group.command('list')
@click.option('--all', 'all_tools', is_flag=True, help='Include shared dependencies.')
@click.option('--json', 'as_json', is_flag=True, help='Print machine-readable results.')
def tools_list(all_tools, as_json):
    """List integrations installed for this persona."""
    list_tools('installed', all_tools, as_json)


@tools_group.command('catalog')
@click.option('--all', 'all_tools', is_flag=True, help='Include shared dependencies.')
@click.option('--json', 'as_json', is_flag=True, help='Print machine-readable results.')
def tools_catalog(all_tools, as_json):
    """Browse all bundled and locally added integrations."""
    list_tools('catalog', all_tools, as_json)


def list_tools(scope, all_tools, as_json):
    catalog = core.registry()
    persona = selected_profile() or core.effective()[0]
    config = core.read_config(persona) if persona else {'tools': []}
    enabled, selected = catalog.enabled(config), catalog.roots(config)
    rows = []
    names = catalog.tools if scope == 'catalog' else enabled
    for name in sorted(names):
        tool = catalog.get(name)
        if tool.data.get('internal', False) and not all_tools:
            continue
        command = tool.data.get('cli', {}).get('command')
        cli_path = shutil.which(command) if command else None
        rows.append({'id': tool.id, 'name': tool.data['name'], 'enabled': tool.id in enabled,
                     'selected': tool.id in selected, 'origin': tool.origin,
                     'definition': str(tool.path), 'cli': command, 'cli_path': cli_path,
                     'cli_available': bool(cli_path) if command else True})
    if as_json:
        click.echo(json.dumps({'persona': persona, 'scope': scope, 'tools': rows,
                               'contribute_url': CONTRIBUTE_URL}, indent=2))
        return
    if rows:
        from .table import echo_table
        click.secho('Tool catalog' if scope == 'catalog' else f'Installed for {persona}', bold=True)
        headers = ['Tool', 'Name']
        cells = [[row['id'], row['name']] for row in rows]
        if any(row['origin'] == 'user' for row in rows):
            headers.append('Source')
            for row, cell in zip(rows, cells):
                path = Path(row['definition']).parent
                home = Path.home()
                source = str(Path('~') / path.relative_to(home)) if path.is_relative_to(home) else str(path)
                cell.append(source if row['origin'] == 'user' else 'Bundled')
        echo_table(headers, cells)
    elif persona:
        click.echo(f'No tools installed for {persona}.')
    else:
        click.echo('No persona selected. Run loki use NAME --global to get started.')
    if scope == 'catalog':
        click.echo('\nInstall an integration: loki tools install <tool>')
        click.echo(f'Contribute your tool: {CONTRIBUTE_URL}')
    else:
        missing = [row['cli'] for row in rows if not row['cli_available']]
        if missing:
            click.echo('\nNative commands not found: '+', '.join(missing)+'. See loki tools show <tool>.')
        click.echo('\nBrowse all integrations: loki tools catalog')


@tools_group.command('show')
@click.argument('tool')
def tools_show(tool):
    """Show a tool's variables and login definition."""
    integration = core.registry().get(tool)
    data = integration.data
    env = data.get('env', {})
    click.echo(json.dumps({'id': tool, 'name': data['name'], 'description': data['description'],
                          'definition': str(integration.path), 'origin': integration.origin,
                          'depends_on': data.get('depends_on', []),
                          'variables': sorted(set(env.get('set', {})) | set(env.get('from_file', {})) |
                                              set(env.get('from_config', {}))),
                          'clears': env.get('unset', []), 'clears_prefixes': env.get('unset_prefixes', []),
                          'cli': data.get('cli', {}), 'login': data.get('login', {}),
                          'who': data.get('who', {}),
                          'hooks': list(data.get('hooks', {}))}, indent=2))


@tools_group.command('validate')
@click.argument('path', required=False, type=click.Path(exists=True, path_type=Path))
@click.option('--all', 'all_tools', is_flag=True, help='Validate the entire catalog (for contributors).')
def tools_validate(path, all_tools):
    """Validate enabled integrations, or an explicit local definition."""
    if path and all_tools:
        raise click.UsageError('Choose PATH or --all, not both.')
    catalog = core.registry()
    if path:
        tool = Tool(path / 'tool.toml' if path.is_dir() else path)
        catalog.tools[tool.id] = tool
        names = catalog.resolve([tool.id])
        scope = f'for {tool.id}'
    elif all_tools:
        names = catalog.resolve(list(catalog.tools))
        scope = 'in the full catalog'
    else:
        persona = selected_profile() or core.effective()[0]
        names = catalog.enabled(core.read_config(persona)) if persona else []
        if not names:
            click.echo(f'No tools enabled for {persona or "the current persona"}; nothing to validate.')
            return
        scope = f'enabled for {persona} (including dependencies)'
    click.echo(f'Validated {len(names)} tool definitions {scope}: '+', '.join(names)+'.')


@tools_group.command('install')
@click.argument('tools', nargs=-1)
@click.option('--from', 'source', type=click.Path(exists=True, path_type=Path), help='Local tool definition or directory.')
@click.option('--replace', is_flag=True, help='Explicitly replace a local/bundled definition.')
def tools_install(tools, source, replace):
    """Enable integrations; install native CLI binaries separately."""
    if replace and not source:
        raise click.UsageError('--replace requires --from PATH.')
    if not tools and not source:
        raise click.UsageError('Name a tool or use --from PATH. See loki tools catalog.')
    persona = core.choose(selected_profile())
    core.create_profile(persona)
    catalog = core.registry()
    names = list(tools)
    if source:
        names.append(catalog.import_local(core, source, replace=replace))
    catalog.install(core, persona, names)
    click.echo(f'Enabled for {persona}: '+', '.join(names))
    for name in names:
        native = catalog.get(name).data.get('cli', {})
        if native.get('command') and not shutil.which(native['command']):
            click.echo(f'{name}: native CLI missing. '+native.get('install_hint', 'Install it separately.'))


@tools_group.command('remove')
@click.argument('tools', nargs=-1, required=True)
def tools_remove(tools):
    """Disable integrations; retain their saved credentials."""
    persona = core.choose(selected_profile())
    core.registry().remove(core, persona, tools)
    click.echo(f'Disabled for {persona}: '+', '.join(tools)+'. Saved credentials are retained.')


@tools_group.command('configure')
@click.argument('tool')
@click.option('--set', 'options', multiple=True, required=True, metavar='KEY=VALUE')
def tools_configure(tool, options):
    """Set nonsecret, persona-specific integration options."""
    persona = core.choose(selected_profile())
    catalog = core.registry()
    catalog.get(tool)
    core.create_profile(persona)
    with core.edit_profile(persona) as config:
        config.setdefault('tool_config', {}).setdefault(tool, {}).update(core.key_values(options))
    if tool in catalog.enabled(core.read_config(persona)):
        catalog.prepare(core, persona, [tool])
    click.echo(f'Updated {tool} settings for {persona}.')


@main.command('clean')
@click.option('--global', 'globally', is_flag=True, help='Clear this persona\'s managed logins across all folders.')
def clean_command(globally):
    """Unlink this folder; optionally clear one persona's logins."""
    core.clean(core.choose(selected_profile()), globally)


@main.command('reset')
def reset_command():
    """Erase all LokiShell data and setup after two confirmations."""
    if selected_profile():
        raise click.UsageError('reset affects all personas; do not pass --profile.')
    from .reset import reset
    reset(core)


@main.command('status')
def status_command():
    """Show the current persona, paths, and enabled tools."""
    active, source, folder = core.effective()
    name = selected_profile() or active
    click.echo(json.dumps({'profile': name, 'active_persona': active, 'source': source,
                          'global_persona': core.settings()['global_persona'],
                          'folder': str(folder) if folder else None,
                          'storage': str(core.profile_dir(name)) if name else None,
                          'tools': core.registry().enabled(core.read_config(name)) if name else []}, indent=2))


@main.command('setup')
def setup_command():
    """Install shell integration without selecting a persona."""
    core.mkdir(core.root())
    core.install_shell()


@main.command('who')
@click.option('--json', 'as_json', is_flag=True, help='Print machine-readable account results.')
@click.option('--timeout', type=click.FloatRange(min=0, max=60, min_open=True),
              help='Maximum seconds per account command (default: integration setting or 10).')
def who_command(as_json, timeout):
    """Show the effective persona and accounts reported by installed tools."""
    from .identity import accounts, LABELS
    from .table import echo_table
    active, source, folder = core.effective()
    name = selected_profile() or active
    if selected_profile():
        source, folder = 'explicit', None
    rows = accounts(core, name, timeout) if name else []
    result = {'persona': name, 'active_persona': active, 'source': source,
              'folder': str(folder) if folder else None, 'tools': rows}
    if as_json:
        click.echo(json.dumps(result, indent=2))
    elif not name:
        click.echo('No persona selected. Run loki use NAME --global to get started.')
    else:
        click.secho(f'Persona: {name}', bold=True)
        click.echo(f'Context: {source}'+(f' · {folder}' if folder else ''))
        if rows:
            echo_table(['Tool', 'Account', 'Status'],
                       [[row['id'], row['account'] or '—', LABELS[row['status']]
                         + (f" ({row['detail']})" if row['detail'] else '')] for row in rows])
        else:
            click.echo('No tools installed for this persona.')


@main.command('hook')
@click.argument('shell', type=click.Choice(['zsh', 'bash']))
def hook_command(shell):
    """Print shell integration."""
    click.echo(core.hook(shell), nl=False)


def emit_environment(name):
    remove, values = core.changes(name)
    for key in sorted(remove):
        click.echo('unset '+key)
    for key, value in values.items():
        click.echo('export '+key+'='+shlex.quote(value))


@main.command('_env', hidden=True)
@click.argument('persona')
def env_command(persona):
    emit_environment(persona)


@main.command('_effective-env', hidden=True)
@click.option('--shell', type=click.Choice(['zsh', 'bash']))
@click.option('--session', 'previous_session', default='')
def effective_env_command(shell, previous_session):
    from .shell import session
    current_session = session(core)
    if shell and previous_session and previous_session != current_session:
        emit_environment(None)
        click.echo('_loki_unload')
        return
    emit_environment(core.effective()[0])
    if shell:
        click.echo('_loki_session='+shlex.quote(current_session))


@main.command('_tool', hidden=True)
@click.argument('tool')
@click.argument('hook', type=click.Choice(['credential']))
@click.argument('args', nargs=-1)
def tool_command(tool, hook, args):
    name = os.environ.get('LOKI_PROFILE') or core.effective()[0]
    if not name:
        return
    catalog = core.registry()
    integration = catalog.get(tool)
    if tool in catalog.enabled(core.read_config(name)):
        integration.call(hook, Context(core, integration, name, args=list(args)))


@main.command('_git-credential', hidden=True)
@click.argument('args', nargs=-1)
@click.pass_context
def git_credential_command(ctx, args):
    # Existing Git configs can retain this helper across an upgrade.
    ctx.invoke(tool_command, tool='github', hook='credential', args=args)
