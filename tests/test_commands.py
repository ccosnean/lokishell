"""Command semantics and destructive-reset boundaries, with disposable state."""
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from tests.support import LokiFixture, LOKI, module
from lokishell.commands import main, CONTRIBUTE_URL


class CommandTests(LokiFixture):
    initial_tools = ()

    def test_create_does_not_select_and_use_without_name_uses_global(self):
        before = json.loads((self.store / 'settings.json').read_text())
        pointer = (self.projects['personal'] / '.envrc').read_text()
        self.run_loki('create', 'new-persona')
        self.assertTrue((self.directory('new-persona') / 'profile.json').is_file())
        self.assertEqual(before, json.loads((self.store / 'settings.json').read_text()))
        self.assertEqual(pointer, (self.projects['personal'] / '.envrc').read_text())
        self.run_loki('use', '--no-shell', cwd=self.projects['work'])
        self.assertEqual(json.loads(self.run_loki('status', cwd=self.projects['work']).stdout)['profile'], 'personal')
        self.run_loki('use', 'new-persona', '--global', '--no-shell')
        self.assertEqual(json.loads(self.run_loki('status', cwd=self.base).stdout)['profile'], 'new-persona')
        self.assertEqual(json.loads(self.run_loki('status').stdout)['profile'], 'personal')

    def test_help_and_removed_commands(self):
        help_text = self.run_loki('--help').stdout
        for command in ('create', 'use', 'reset', 'tools'):
            self.assertIn(command, help_text)
        for old in ('init', 'shift', 'exec'):
            result = self.run_loki(old, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('No such command', result.stderr)
        self.assertNotIn('_effective-env', help_text)

    def test_use_without_global_explains_setup_without_creating_persona(self):
        env = {**self.env, 'LOKI_HOME': str(self.base / 'fresh-store')}
        result = self.run_loki('use', '--no-shell', cwd=self.base, env=env, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('loki use NAME --global', result.stderr)
        self.assertFalse((self.base / 'fresh-store').exists())

    def test_validate_ignores_unenabled_broken_definitions(self):
        broken = self.store / 'tools/broken'
        broken.mkdir(parents=True)
        (broken / 'tool.toml').write_text('this is not toml')
        empty = self.run_loki('tools', 'validate').stdout
        self.assertIn('nothing to validate', empty)
        self.run_loki('tools', 'install', 'firebase')
        selected = self.run_loki('tools', 'validate').stdout
        self.assertIn('Validated 2 tool definitions', selected)
        self.assertIn('google, firebase', selected)
        full = self.run_loki('tools', 'validate', '--all', check=False)
        self.assertNotEqual(full.returncode, 0)
        self.assertIn('broken', full.stderr)

    def test_tool_list_links_contribution_guide(self):
        self.assertIn(CONTRIBUTE_URL, self.run_loki('tools', 'list').stdout)
        self.assertEqual(json.loads(self.run_loki('tools', 'list', '--json').stdout)['contribute_url'], CONTRIBUTE_URL)

    def reset_cli(self, answer='', interactive=True):
        fake_home = self.base / 'shell-home'
        fake_home.mkdir(exist_ok=True)
        with patch.object(module.Path, 'home', return_value=fake_home), \
             patch('lokishell.reset.interactive_input', return_value=interactive):
            return CliRunner().invoke(main, ['reset'], input=answer, env=self.env)

    def test_reset_both_confirmations_are_required_without_mutation(self):
        token = self.directory('personal') / 'saved-token'
        token.write_text('fictional')
        settings = (self.store / 'settings.json').read_bytes()
        for answer, interactive in [('n\n', True), ('y\nwrong phrase\n', True),
                                    ('y\nRESET LOKISHELL\n', False), ('', True)]:
            result = self.reset_cli(answer, interactive)
            self.assertNotEqual(result.exit_code, 0, result.output)
            self.assertEqual(token.read_text(), 'fictional')
            self.assertEqual((self.store / 'settings.json').read_bytes(), settings)
            self.assertTrue((self.projects['personal'] / '.envrc').is_file())
            self.assertIn('Agent sessions/history', result.output)

    def test_reset_removes_all_managed_state_and_only_its_setup_blocks(self):
        self.run_loki('tools', 'install', 'codex', 'claude')
        self.run_loki('add', 'mcp', 'claude', 'tickets')
        (self.directory('personal') / 'codex/history.jsonl').write_text('fictional session')
        (self.directory('personal') / 'codex/auth.json').write_text('fictional token')
        (self.directory('personal') / 'claude/.credentials.json').write_text('fictional token')
        (self.directory('work') / 'history').write_text('fictional work history')
        fake_home = self.base / 'shell-home'
        fake_home.mkdir()
        original = 'export KEEP_ME=yes\n'
        (fake_home / '.zshrc').write_text(original)
        with patch.dict(os.environ, {**self.env, 'SHELL': '/bin/zsh'}, clear=True), \
             patch.object(module.Path, 'home', return_value=fake_home), \
             patch('sys.argv', [str(LOKI)]), patch('sys.stdout', new=io.StringIO()):
            module.install_shell()
        pointer = self.projects['personal'] / '.envrc'
        pointer.write_text(original + pointer.read_text())
        project_file = self.projects['personal'] / 'keep.txt'
        project_file.write_text('keep')
        result = self.reset_cli('y\nRESET LOKISHELL\n')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(self.store.exists())
        self.assertEqual(pointer.read_text(), original)
        self.assertEqual((fake_home / '.zshrc').read_text(), original + '\n')
        self.assertFalse((fake_home / '.zshenv').exists())
        self.assertFalse((self.projects['work'] / '.envrc').exists())
        self.assertEqual(project_file.read_text(), 'keep')
        self.assertIn('"logout", "tickets"', (self.base / 'calls.jsonl').read_text())
        fresh = json.loads(self.run_loki('status').stdout)
        self.assertIsNone(fresh['profile'])
        self.assertFalse(self.store.exists())
        self.run_loki('create', 'fresh')
        self.assertEqual(json.loads((self.directory('fresh') / 'profile.json').read_text())['tools'], [])

    def test_reset_refuses_linked_setup_before_erasing_credentials(self):
        pointer = self.projects['work'] / '.envrc'
        target = self.base / 'outside-envrc'
        pointer.rename(target)
        pointer.symlink_to(target)
        result = self.reset_cli('y\nRESET LOKISHELL\n')
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('linked setup file', result.output)
        self.assertTrue(self.store.exists())
        self.assertTrue(target.exists())

    def test_failed_logout_retains_configuration_for_retry(self):
        self.run_loki('tools', 'install', 'claude')
        (self.directory('personal') / 'claude/.credentials.json').write_text('fictional')
        self.env['FAIL_TOOL'] = 'claude'
        result = self.reset_cli('y\nRESET LOKISHELL\n')
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('Configuration is retained', result.output)
        self.assertTrue((self.store / 'settings.json').exists())
        self.assertTrue((self.projects['personal'] / '.envrc').exists())
