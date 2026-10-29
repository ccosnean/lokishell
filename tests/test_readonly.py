"""Agent sessions may read existing personas without writing to their store."""
import io
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

from click.testing import CliRunner

from tests.support import LokiFixture, LOKI, module
from lokishell.commands import main
from lokishell.dispatch import HEADER


class ReadOnlyTests(LokiFixture):
    initial_tools = ('firebase', 'gcloud')

    def snapshot(self):
        return {str(path): (path.stat().st_mode, path.stat().st_mtime_ns, path.stat().st_ctime_ns)
                for path in (self.store, *self.store.rglob('*'))}

    def test_environment_and_hook_queries_do_not_attempt_store_writes(self):
        before = self.snapshot()
        with patch.object(module, 'mkdir', side_effect=PermissionError('sandbox denied mkdir')), \
             patch.object(module, 'write', side_effect=PermissionError('sandbox denied write')), \
             patch.object(Path, 'chmod', side_effect=PermissionError('sandbox denied chmod')), \
             patch.object(sys, 'argv', [str(LOKI)]):
            for args in [['_env', 'personal'], ['_effective-env'], ['hook', 'zsh'], ['hook', 'bash']]:
                with self.subTest(command=args):
                    result = CliRunner().invoke(main, args, env=self.env)
                    self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.snapshot(), before)

    def test_missing_launchers_fail_without_falling_back_to_global_credentials(self):
        shutil.rmtree(self.store / 'bin')
        before = self.snapshot()
        result = self.run_loki('_env', 'personal', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('loki setup', result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.store / 'bin').exists())

    def test_explicit_setup_migrates_launchers_for_existing_and_disabled_tools(self):
        self.run_loki('--profile', 'work', 'tools', 'install', 'codex')
        self.run_loki('--profile', 'work', 'tools', 'remove', 'codex')
        for path in (self.store / 'bin').iterdir():
            path.write_text('#!/bin/sh\nexit 1\n')
        home = self.base / 'setup-home'
        home.mkdir()
        with patch.dict(os.environ, dict(self.env, SHELL='/bin/bash'), clear=True), \
             patch.object(module.Path, 'home', return_value=home), \
             patch.object(sys, 'argv', [str(LOKI)]), patch('sys.stdout', new=io.StringIO()):
            module.install_shell()
        for command in ('firebase', 'gcloud', 'codex'):
            self.assertTrue((self.store / 'bin' / command).read_text().startswith(HEADER))
        self.run_loki('_env', 'personal')

    def test_initialized_commands_use_current_runtime_without_rewriting_launchers(self):
        # Simulate a previous installation's Python and executable disappearing
        # during an upgrade. The fresh shell exports the current runtime paths.
        path = self.store / 'bin/gcloud'
        content = path.read_text().replace(str(LOKI), '/removed-install/bin/loki')
        content = content.replace(sys.executable, '/removed-install/venv/bin/python')
        path.write_text(content)
        before = self.snapshot()
        result = self.run_in_persona('personal', 'gcloud', 'loki-test-env', 'CLOUDSDK_CONFIG')
        self.assertIn(str(self.directory('personal') / 'gcloud'), result.stdout)
        self.assertEqual(self.snapshot(), before)
