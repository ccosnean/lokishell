"""GitHub-specific login and Git credential helpers. No provider logic lives in Loki core."""
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request


def who(ctx):
    token = ctx.path('auth/github-token')
    if not token.is_file() or not token.read_text().strip():
        return {'status': 'signed_out'}
    result = ctx.capture()
    if result.returncode:
        return {'status': 'error'}
    username = result.stdout.strip()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,38}', username):
        return {'status': 'error'}
    return {'status': 'signed_in', 'account': username}


def prepare(ctx):
    # Import only identity metadata from the old dev-account helper, never its tokens.
    legacy = Path.home() / '.config/dev-accounts' / ctx.persona / 'env.sh'
    fields = {}
    if legacy.is_file():
        mapping = {'DEV_ACCOUNT_GITHUB_USER': 'github_user', 'DEV_ACCOUNT_GIT_NAME': 'git_name',
                   'DEV_ACCOUNT_GIT_EMAIL': 'git_email'}
        for line in legacy.read_text().splitlines():
            for key, field in mapping.items():
                if line.startswith(f'export {key}='):
                    values = shlex.split(line.split('=', 1)[1])
                    if len(values) == 1:
                        fields[field] = values[0]
    if fields:
        with ctx.core.edit_profile(ctx.persona) as config:
            settings = config.setdefault('tool_config', {}).setdefault(ctx.tool.id, {})
            for field, value in fields.items():
                if field not in config:
                    settings.setdefault(field, value)
    helper = '!'+shlex.quote(ctx.core.executable())+f' _tool {ctx.tool.id} credential'
    content = ('[user]\n    useConfigOnly = true\n'
               '[credential]\n    helper =\n    helper = '+json.dumps(helper)+'\n'
               '[url "https://github.com/"]\n'
               '    insteadOf = git@github.com:\n    insteadOf = ssh://git@github.com/\n')
    for key, field in (('name', 'git_name'), ('email', 'git_email')):
        if ctx.setting(field):
            content += '[user]\n    '+key+' = '+json.dumps(ctx.setting(field))+'\n'
    ctx.write('gitconfig', content)


def login(ctx):
    username = ctx.options.get('user') or ctx.setting('github_user')
    if not username:
        if not sys.stdin.isatty():
            raise ValueError('Supply --user GITHUB_USERNAME to add github.')
        username = input('GitHub username: ').strip()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]*', username):
        raise ValueError('Invalid GitHub username.')
    token = ''
    base = ctx.core.baseline_environment()
    gh = shutil.which('gh', path=base.get('PATH', os.defpath))
    if gh:
        base['GH_CONFIG_DIR'] = os.environ.get('LOKI_GH_IMPORT_DIR', str(Path.home() / '.config/gh'))
        for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'):
            base.pop(key, None)
        result = subprocess.run([gh, 'auth', 'token', '--hostname', 'github.com', '--user', username],
                                env=base, capture_output=True, text=True)
        if result.returncode == 0:
            token = result.stdout.strip()
    if not token:
        if not sys.stdin.isatty():
            raise ValueError('No saved gh login for that user. Run loki add github interactively to enter a token.')
        print('No saved gh login found. Create a GitHub personal access token for this account, then paste it below.')
        token = getpass.getpass('GitHub token (hidden): ').strip()
    request = urllib.request.Request('https://api.github.com/user', headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'User-Agent': 'LokiShell'})
    with urllib.request.urlopen(request, timeout=20) as response:
        account = json.load(response)
    if account['login'].lower() != username.lower():
        raise ValueError('The token belongs to a different GitHub account; nothing was saved.')
    ctx.write('auth/github-token', token + '\n')
    with ctx.core.edit_profile(ctx.persona) as config:
        values = config.setdefault('tool_config', {}).setdefault(ctx.tool.id, {})
        values['github_user'] = account['login']
        values.setdefault('git_name', config.get('git_name') or account.get('name') or account['login'])
        values.setdefault('git_email', config.get('git_email') or account.get('email') or
                          f'{account["id"]}+{account["login"]}@users.noreply.github.com')
    prepare(ctx)


def credential(ctx):
    if ctx.args and ctx.args != ['get']:
        return
    fields = dict(line.rstrip('\n').split('=', 1) for line in sys.stdin if '=' in line)
    if fields.get('protocol') != 'https' or fields.get('host') != 'github.com':
        return
    path = ctx.path('auth/github-token')
    if path.is_file():
        print('username='+str(ctx.setting('github_user', 'x-access-token')))
        print('password='+path.read_text().strip())
