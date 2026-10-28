"""Google configuration selection, including offline checks with the real SDK."""
import json
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

from tests.support import LokiFixture, module


# Capture the real CLI before LokiFixture adds its authentication stubs to PATH.
GCLOUD = shutil.which('gcloud')


class GoogleConfigurationTests(LokiFixture):
    initial_tools = ()

    def test_google_integrations_clear_inherited_configuration_without_forcing_a_name(self):
        self.env['CLOUDSDK_ACTIVE_CONFIG_NAME'] = 'outside-loki'
        self.env['CLOUDSDK_CONFIG'] = str(self.base / 'outside-google')
        probe = '''import os, json
print(json.dumps({key: os.environ.get(key) for key in
    ('CLOUDSDK_CONFIG', 'CLOUDSDK_ACTIVE_CONFIG_NAME')}))
'''
        for tool in ('gcloud', 'adc', 'firebase'):
            with self.subTest(tool=tool):
                self.run_loki('tools', 'install', tool)
                result = self.run_in_persona('personal', sys.executable, '-c', probe)
                values = json.loads(result.stdout)
                self.assertEqual(values['CLOUDSDK_CONFIG'], str(self.directory('personal') / 'gcloud'))
                self.assertIsNone(values['CLOUDSDK_ACTIVE_CONFIG_NAME'])
                self.run_loki('tools', 'remove', tool)
        self.assertFalse((self.base / 'outside-google').exists())

    def gcloud(self, persona, *args):
        # Only local config commands are used. No real login, logout, or cloud API.
        with patch.dict(os.environ, self.env, clear=True):
            env = module.environment(persona)
        env.update(CLOUDSDK_CORE_DISABLE_USAGE_REPORTING='true',
                   CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK='true')
        result = subprocess.run([GCLOUD, '--quiet', *args], env=env, cwd=self.base,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def install_gcloud(self):
        if not GCLOUD:
            self.skipTest('Optional installed gcloud SDK check')
        self.env['CLOUDSDK_ACTIVE_CONFIG_NAME'] = 'outside-loki'
        for persona in ('personal', 'work'):
            self.run_loki('--profile', persona, 'tools', 'install', 'gcloud')

    def test_real_gcloud_can_write_properties_in_fresh_and_cleaned_personas(self):
        self.install_gcloud()
        for persona in ('personal', 'work'):
            config = self.directory(persona) / 'gcloud/configurations/config_default'
            self.assertFalse(config.exists())
            # auth login persists core/account through the same property writer.
            self.gcloud(persona, 'config', 'set', 'core/disable_prompts', 'true')
            self.assertTrue(config.is_file())
            self.assertEqual(self.gcloud(persona, 'config', 'get-value', 'core/disable_prompts'), 'True')
        work_config = self.directory('work') / 'gcloud/configurations/config_default'
        saved = work_config.read_bytes()
        self.run_loki('--profile', 'personal', 'clean', '--global')
        self.gcloud('personal', 'config', 'set', 'core/disable_prompts', 'true')
        self.assertEqual(work_config.read_bytes(), saved)

    def test_real_gcloud_retains_persona_named_configuration_on_reinstall(self):
        self.install_gcloud()
        self.gcloud('personal', 'config', 'configurations', 'create', 'project-a')
        self.gcloud('personal', 'config', 'set', 'core/disable_prompts', 'true')
        config = self.directory('personal') / 'gcloud/configurations/config_project-a'
        saved = config.read_bytes()
        self.run_loki('tools', 'install', 'gcloud')
        self.assertEqual(self.gcloud('personal', 'config', 'get-value', 'core/disable_prompts'), 'True')
        self.assertEqual((self.directory('personal') / 'gcloud/active_config').read_text().strip(), 'project-a')
        self.assertEqual(config.read_bytes(), saved)
        self.gcloud('work', 'config', 'set', 'core/disable_prompts', 'false')
        self.assertEqual(self.gcloud('personal', 'config', 'get-value', 'core/disable_prompts'), 'True')
