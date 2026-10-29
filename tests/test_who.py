"""Account checks use fictional credentials and disposable native CLI stubs."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from tests.support import LokiFixture, LOKI
from lokishell.registry import Tool


class WhoTests(LokiFixture):
    initial_tools = ()

    def stub(self, name, body):
        script = self.fake / name
        script.write_text('#!' + sys.executable + '\n' + body)
        script.chmod(0o700)

    def who(self, *args, **kwargs):
        return json.loads(self.run_loki(*args, 'who', '--json', **kwargs).stdout)

    def test_context_selection_and_empty_state(self):
        folder = self.who()
        self.assertEqual((folder['persona'], folder['source']), ('personal', 'folder'))
        self.assertEqual(folder['tools'], [])
        global_result = self.who(cwd=self.base)
        self.assertEqual((global_result['persona'], global_result['source']), ('personal', 'global'))
        explicit = self.who('--profile', 'work')
        self.assertEqual((explicit['persona'], explicit['active_persona'], explicit['source']),
                         ('work', 'personal', 'explicit'))
        store = self.base / 'no-store'
        empty = self.who(cwd=self.base, env=dict(self.env, LOKI_HOME=str(store)))
        self.assertIsNone(empty['persona'])
        self.assertFalse(store.exists())

    def test_worktree_context_is_inherited(self):
        repo = self.base / 'repo'
        repo.mkdir()
        def git(*args):
            subprocess.run(['git', *args], cwd=repo, env=self.env, check=True, capture_output=True)
        git('init')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
            'commit', '--allow-empty', '-m', 'fixture')
        self.run_loki('use', 'work', '--no-shell', cwd=repo)
        tree = self.base / 'tree'
        git('worktree', 'add', '-b', 'other', str(tree))
        result = self.who(cwd=tree)
        self.assertEqual((result['persona'], result['source']), ('work', 'repository/worktree'))

    def test_installed_accounts_use_selected_persona_and_hide_internal_dependencies(self):
        self.run_loki('tools', 'install', 'firebase', 'gcloud', 'github', 'claude', 'codex', 'adc')
        (self.directory('personal') / 'auth/github-token').write_text('fictional-personal-token')
        payload = base64.urlsafe_b64encode(json.dumps({'email': 'codex@example.invalid'}).encode()).decode().rstrip('=')
        (self.directory('personal') / 'codex/auth.json').write_text(json.dumps({
            'tokens': {'id_token': 'header.' + payload + '.signature', 'access_token': 'NEVER-PRINT-SECRET'}}))
        (self.directory('personal') / 'gcloud/application_default_credentials.json').write_text(json.dumps({
            'type': 'service_account', 'client_email': 'adc@example.invalid', 'private_key': 'NEVER-PRINT-SECRET'}))
        common = '''import os, sys
from pathlib import Path
p = Path(os.environ['LOKI_HOME']) / 'profiles' / 'personal'
assert os.environ['LOKI_PROFILE'] == 'personal'
assert os.environ['GOOGLE_APPLICATION_CREDENTIALS'] == str(p / 'gcloud/application_default_credentials.json')
name = Path(sys.argv[0]).name
owned = {'firebase': 'XDG_CONFIG_HOME', 'gcloud': 'CLOUDSDK_CONFIG', 'gh': 'GH_TOKEN',
         'claude': 'CLAUDE_CONFIG_DIR', 'codex': 'CODEX_HOME'}
expected = {'XDG_CONFIG_HOME': str(p / 'xdg'), 'CLOUDSDK_CONFIG': str(p / 'gcloud'),
            'GH_TOKEN': 'fictional-personal-token', 'CLAUDE_CONFIG_DIR': str(p / 'claude'),
            'CODEX_HOME': str(p / 'codex')}
assert os.environ[owned[name]] == expected[owned[name]]
if name == 'firebase':
 assert 'FIREBASE_TOKEN' not in os.environ
else:
 assert os.environ['FIREBASE_TOKEN'] == 'wrong-inherited-token'
'''
        self.stub('firebase', common + r'''assert sys.argv[1:] == ['login:list', '--non-interactive']
print('Logged in as selected@example.invalid\n\nOther available accounts\n - global@example.invalid')
''')
        self.stub('gcloud', common + "assert sys.argv[1:] == ['auth', 'list', '--filter=status:ACTIVE', '--format=value(account)', '--quiet']\nprint('gcloud@example.invalid')\n")
        self.stub('gh', common + "assert sys.argv[1:] == ['api', 'user', '--jq', '.login']\nprint('personal-user')\n")
        self.stub('claude', common + "assert sys.argv[1:] == ['auth', 'status']\nprint('{\"loggedIn\":true,\"email\":\"claude@example.invalid\",\"access_token\":\"NEVER-PRINT-SECRET\"}')\n")
        self.stub('codex', common + "assert sys.argv[1:] == ['login', 'status']\nprint('Logged in using ChatGPT', file=sys.stderr)\n")
        settings = (self.store / 'settings.json').read_bytes()
        config = (self.directory('personal') / 'profile.json').read_bytes()
        result = self.who()
        rows = {row['id']: row for row in result['tools']}
        self.assertEqual(set(rows), {'firebase', 'gcloud', 'github', 'claude', 'codex', 'adc'})
        for name, account in {'firebase': 'selected@example.invalid', 'gcloud': 'gcloud@example.invalid',
                              'github': 'personal-user', 'claude': 'claude@example.invalid',
                              'codex': 'codex@example.invalid', 'adc': 'adc@example.invalid'}.items():
            self.assertEqual(rows[name]['account'], account, rows[name])
        self.assertEqual(rows['adc']['status'], 'configured')
        self.assertNotIn('NEVER-PRINT-SECRET', json.dumps(result))
        self.assertEqual(settings, (self.store / 'settings.json').read_bytes())
        self.assertEqual(config, (self.directory('personal') / 'profile.json').read_bytes())
        table = self.run_loki('who').stdout
        self.assertIn('Persona: personal', table)
        self.assertIn('Context: folder', table)
        self.assertIn('│', table)
        self.assertIn('selected@example.invalid', table)
        self.assertNotIn('NEVER-PRINT-SECRET', table)

    def test_signed_out_invalid_output_and_failures_are_distinct_and_private(self):
        self.run_loki('tools', 'install', 'firebase', 'claude', 'codex', 'github', 'gcloud', 'adc')
        self.stub('firebase', "print('No authorized accounts, run firebase login')\n")
        self.stub('claude', "import sys\nprint('{\"loggedIn\": false}')\nsys.exit(1)\n")
        self.stub('codex', "import sys\nprint('Not logged in', file=sys.stderr)\nsys.exit(1)\n")
        self.stub('gcloud', "import sys\nprint('NEVER-PRINT-SECRET', file=sys.stderr)\nsys.exit(8)\n")
        # No saved GitHub token: do not contact GitHub or fall back to shared gh.
        self.stub('gh', "raise AssertionError('must not run')\n")
        rows = {row['id']: row for row in self.who()['tools']}
        for name in ('firebase', 'claude', 'codex', 'github', 'adc'):
            self.assertEqual(rows[name]['status'], 'signed_out', rows[name])
        self.assertEqual(rows['gcloud']['status'], 'error')
        self.stub('claude', "print('invalid-json-NEVER-PRINT-SECRET')\n")
        self.assertNotIn('NEVER-PRINT-SECRET', self.run_loki('who').stdout)
        self.assertEqual({r['id']: r for r in self.who()['tools']}['claude']['status'], 'error')

    def custom(self, who=''):
        folder = self.base / 'acme'
        folder.mkdir(exist_ok=True)
        manifest = folder / 'tool.toml'
        manifest.write_text('schema_version=1\nid="acme"\nname="Acme"\ndescription="Test"\n' + who)
        self.run_loki('tools', 'install', '--from', str(folder), '--replace')
        return manifest

    def test_custom_declarative_probe_missing_cli_and_timeout(self):
        self.custom('[who]\ncommand=["missing-loki-who-test-command"]\n')
        self.assertEqual(self.who()['tools'][0]['status'], 'unavailable')
        self.custom('[who]\ncommand=["acme", "{persona}"]\nsigned_out_exit_codes=[3]\n')
        self.stub('acme', "import sys\nassert sys.argv[1] == 'personal'\nprint('me@example.invalid')\n")
        self.assertEqual(self.who()['tools'][0]['account'], 'me@example.invalid')
        self.stub('acme', "import sys\nsys.exit(3)\n")
        self.assertEqual(self.who()['tools'][0]['status'], 'signed_out')
        self.stub('acme', 'import subprocess, sys, time\nsubprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])\ntime.sleep(20)\n')
        start = time.monotonic()
        result = self.run_loki('who', '--json', '--timeout', '0.2')
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(json.loads(result.stdout)['tools'][0]['status'], 'timeout')
        self.custom()
        self.assertEqual(self.who()['tools'][0]['status'], 'unsupported')

    def test_probe_schema_rejects_invalid_configuration(self):
        manifest = self.base / 'invalid.toml'
        for definition in ('command="whoami"', 'command=[]', 'timeout=0', 'timeout=nan',
                           'command=["whoami"]\nsigned_out_exit_codes=[0]',
                           'command=["whoami"]\nsigned_out_exit_codes=["1"]'):
            with self.subTest(definition=definition):
                manifest.write_text('schema_version=1\nid="invalid"\nname="Invalid"\ndescription="Test"\n[who]\n' + definition)
                with self.assertRaises(ValueError):
                    Tool(manifest)

    def test_account_unavailable_modes_never_expose_tokens_or_borrow_gcloud_identity(self):
        self.run_loki('tools', 'install', 'adc', 'firebase', 'claude', 'codex')
        adc = self.directory('personal') / 'gcloud/application_default_credentials.json'
        adc.write_text(json.dumps({'type': 'authorized_user', 'refresh_token': 'NEVER-PRINT-SECRET'}))
        self.stub('firebase', "print('No authorized accounts, run firebase login')\n")
        self.stub('claude', "print('{\"loggedIn\":true,\"authMethod\":\"api_key\",\"key\":\"NEVER-PRINT-SECRET\"}')\n")
        self.stub('codex', "import sys\nprint('Logged in using an API key - NEVER-PRINT-SECRET', file=sys.stderr)\n")
        data = self.who()
        rows = {row['id']: row for row in data['tools']}
        self.assertEqual(rows['adc']['status'], 'configured')
        self.assertEqual(rows['firebase']['status'], 'configured')
        self.assertEqual(rows['codex']['status'], 'signed_in')
        self.assertEqual(rows['claude']['status'], 'signed_in')
        self.assertTrue(all(row['account'] is None for row in rows.values()))
        self.assertNotIn('NEVER-PRINT-SECRET', json.dumps(data))
        adc.write_text('invalid-NEVER-PRINT-SECRET')
        data = self.who()
        self.assertEqual(next(row for row in data['tools'] if row['id'] == 'adc')['status'], 'error')
        self.assertNotIn('NEVER-PRINT-SECRET', json.dumps(data))

    def test_explicit_persona_checks_its_own_environment_and_disabled_probes_do_not_run(self):
        self.custom('[who]\ncommand=["acme", "{persona}"]\n')
        self.run_loki('--profile', 'work', 'tools', 'install', 'acme')
        self.stub('acme', "import os, sys\nassert os.environ['LOKI_PROFILE'] == sys.argv[1]\nprint(os.environ['LOKI_PROFILE'] + '@example.invalid')\n")
        self.assertEqual(self.who('--profile', 'work')['tools'][0]['account'], 'work@example.invalid')
        self.assertEqual(self.who()['tools'][0]['account'], 'personal@example.invalid')
        self.run_loki('tools', 'remove', 'acme')
        self.stub('acme', "raise AssertionError('disabled probe ran')\n")
        self.assertEqual(self.who()['tools'], [])
