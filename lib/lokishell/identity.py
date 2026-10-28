"""Bounded, persona-scoped account checks declared by tool integrations."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import signal
import subprocess
import unicodedata

from .registry import Context


LABELS = {'signed_in': 'Signed in', 'signed_out': 'Not signed in',
          'configured': 'Credentials saved', 'unknown': 'Unknown',
          'unavailable': 'CLI not found', 'unsupported': 'Not supported',
          'error': 'Check failed', 'timeout': 'Timed out'}


def display_text(value):
    if not isinstance(value, str):
        raise ValueError('Expected display text')
    # Treat CLI output as data, never terminal control sequences.
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    return ''.join(c for c in value if not unicodedata.category(c).startswith('C'))[:256]


class ProbeContext(Context):
    def __init__(self, core, tool, persona, timeout=None):
        super().__init__(core, tool, persona)
        self.timeout = timeout or tool.data.get('who', {}).get('timeout', 10)

    def capture(self):
        """Run who.command without stdin; never echo its output or error text."""
        command = [self.render(part) for part in self.tool.data['who']['command']]
        env = self.core.environment(self.persona)
        env.update(NO_COLOR='1', CLICOLOR='0')
        with subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=self.timeout)
            except BaseException:
                # CLI launchers may have children holding the output pipes open.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def read_json(self, relative):
        path = self.path(relative)
        return json.loads(path.read_text()) if path.is_file() else None


def check_tool(core, tool, persona, timeout=None):
    row = {'id': tool.id, 'name': tool.data['name'], 'status': 'unsupported',
           'account': None, 'detail': None}
    try:
        context = ProbeContext(core, tool, persona, timeout)
        if 'who' in tool.data.get('hooks', {}):
            result = tool.call('who', context)
        elif 'command' in tool.data.get('who', {}):
            output = context.capture()
            if output.returncode in tool.data['who'].get('signed_out_exit_codes', []):
                result = {'status': 'signed_out'}
            elif output.returncode:
                result = {'status': 'error'}
            else:
                account = output.stdout.strip()
                if '\n' in account or len(account) > 256:
                    raise ValueError('Account command must return a single identity')
                result = {'status': 'signed_in' if account else 'signed_out', 'account': account or None}
        else:
            return row
        if not isinstance(result, dict) or result.get('status') not in LABELS:
            raise ValueError('Invalid account result')
        row['status'] = result['status']
        for key in ('account', 'detail'):
            if result.get(key) is not None:
                row[key] = display_text(result[key])
    except FileNotFoundError:
        row.update(status='unavailable', account=None, detail=None)
    except subprocess.TimeoutExpired:
        row.update(status='timeout', account=None, detail=None)
    except Exception:
        # Exceptions, stderr and full authentication responses may contain tokens.
        row.update(status='error', account=None, detail=None)
    return row


def accounts(core, persona, timeout=None):
    catalog = core.registry()
    tools = [catalog.get(name) for name in sorted(catalog.enabled(core.read_config(persona)))
             if not catalog.get(name).data.get('internal', False)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda tool: check_tool(core, tool, persona, timeout), tools))
