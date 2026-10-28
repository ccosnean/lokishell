"""Tool loading, selective environments, and third-party definitions."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
from tests.support import LokiFixture, LOKI, ROOT, module
from lokishell.registry import Registry, Tool


def load_tests(loader, tests, pattern):
    # New integrations contribute tests only in their own directory; no central list to edit.
    for file in sorted((ROOT/'tools').glob('*/test_*.py')):
        spec=importlib.util.spec_from_file_location('tool_tests_'+file.parent.name+'_'+file.stem,file)
        integration=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(integration)
        tests.addTests(loader.loadTestsFromModule(integration))
    return tests


class ToolTests(LokiFixture):
    initial_tools=()

    def test_new_persona_does_not_change_unselected_tools(self):
        listing=json.loads(self.run_loki('tools','list','--json').stdout)
        self.assertFalse(any(tool['enabled'] for tool in listing['tools']))
        probe='import os,json; print(json.dumps({k:os.environ.get(k) for k in ["GH_TOKEN","FIREBASE_TOKEN","ANTHROPIC_API_KEY","OPENAI_API_KEY"]}))'
        values=json.loads(self.run_in_persona('personal','python3','-c',probe).stdout)
        self.assertEqual(values['GH_TOKEN'],'wrong-inherited-token')
        self.assertEqual(values['OPENAI_API_KEY'],'wrong-inherited-key')
        self.assertFalse((self.directory('personal')/'codex').exists())

    def test_selective_enable_and_switch_restore_unmanaged_environment(self):
        self.run_loki('tools','install','github')
        self.run_loki('--profile','work','tools','install','firebase')
        (self.directory('personal')/'auth/github-token').write_text('fictional-personal')
        probe='import os,json; print(json.dumps({k:os.environ.get(k) for k in ["GH_TOKEN","GH_CONFIG_DIR","XDG_CONFIG_HOME","OPENAI_API_KEY","LOKI_PROFILE"]}))'
        first=json.loads(self.run_in_persona('personal','python3','-c',probe).stdout)
        self.assertEqual(first['GH_TOKEN'],'fictional-personal')
        result=self.run_in_persona('personal','bash','--noprofile','--norc','-c',
                                  'loki_test_env="$("$1" _env work)"; eval "$loki_test_env"; python3 -c "$2"',
                                  'bash',str(LOKI),probe)
        switched=json.loads(result.stdout)
        self.assertEqual(switched['GH_TOKEN'],'wrong-inherited-token')
        self.assertIsNone(switched['GH_CONFIG_DIR'])
        self.assertEqual(switched['XDG_CONFIG_HOME'],str(self.directory('work')/'xdg'))
        self.assertEqual(switched['OPENAI_API_KEY'],'wrong-inherited-key')

    @unittest.skipUnless(shutil.which('zsh'), 'zsh not available')
    def test_interactive_folder_switch_and_tool_removal_restore_original_environment(self):
        self.run_loki('tools','install','github')
        self.run_loki('--profile','work','tools','install','firebase')
        (self.directory('personal')/'auth/github-token').write_text('fictional-personal')
        dot=self.base/'tool-switch-zdot'; dot.mkdir()
        (dot/'.zshenv').write_text(self.run_loki('hook','zsh').stdout)
        script=('echo "first=$GH_TOKEN"; cd '+str(self.projects['work'])+'; '
                'echo "second=$GH_TOKEN"; echo "work=$XDG_CONFIG_HOME"; '
                'loki tools remove firebase; echo "google=${CLOUDSDK_AUTH_ACCESS_TOKEN-unset}"; '
                'cd '+str(self.base)+'; echo "back=$GH_TOKEN"')
        result=subprocess.run(['zsh','-c',script],cwd=self.projects['personal'],
                              env=dict(self.env,ZDOTDIR=str(dot)),check=True,text=True,capture_output=True)
        self.assertIn('first=fictional-personal',result.stdout)
        self.assertIn('second=wrong-inherited-token',result.stdout)
        self.assertIn('work='+str(self.directory('work')/'xdg'),result.stdout)
        self.assertIn('google=wrong-inherited-token',result.stdout)
        self.assertIn('back=fictional-personal',result.stdout)

    def test_local_tool_can_be_installed_configured_logged_in_and_cleaned_without_core_changes(self):
        definition=self.base/'acme'; definition.mkdir()
        (definition/'tool.toml').write_text('''schema_version = 1
id = "acme"
name = "Acme"
description = "Test integration contributed as one definition"
[env]
unset = ["ACME_TOKEN"]
[env.set]
ACME_CONFIG_DIR = "{profile}/acme"
ACME_REGION = "{config.region}"
[state]
directories = ["acme"]
[login]
command = ["python3", "-c", "import os; from pathlib import Path; Path(os.environ['ACME_CONFIG_DIR'], 'token').write_text('fictional')"]
[cleanup]
files = ["acme/token"]
''')
        self.run_loki('tools','install','--from',str(definition))
        self.run_loki('tools','configure','acme','--set','region=eu')
        self.run_loki('add','acme')
        self.assertEqual((self.directory('personal')/'acme/token').read_text(),'fictional')
        result=self.run_in_persona('personal','python3','-c','import os; print(os.environ["ACME_REGION"])')
        self.assertEqual(result.stdout.strip(),'eu')
        self.run_loki('tools','remove','acme')
        self.assertTrue((self.directory('personal')/'acme/token').exists())
        # A global reset also clears data for integrations that were previously disabled.
        self.run_loki('clean','--global')
        self.assertFalse((self.directory('personal')/'acme/token').exists())

    def test_disabled_tool_cleanup_still_runs_in_its_own_persona_environment(self):
        self.run_loki('tools','install','claude')
        (self.directory('personal')/'claude/.credentials.json').write_text('fictional')
        self.run_loki('tools','remove','claude')
        self.run_loki('clean','--global')
        calls=[json.loads(x) for x in (self.base/'calls.jsonl').read_text().splitlines()]
        self.assertEqual(calls[-1]['claude'],str(self.directory('personal')/'claude'))
        self.assertEqual(calls[-1]['args'],['auth','logout'])

    def test_legacy_persona_migration_preserves_existing_logins_and_tool_selection(self):
        directory=self.directory('personal')
        (directory/'gcloud').mkdir()
        saved=directory/'gcloud/credentials.db'; saved.write_text('fictional-existing')
        config=json.loads((directory/'profile.json').read_text())
        config.pop('tools')
        (directory/'profile.json').write_text(json.dumps(config))
        self.run_loki('use','personal','--global','--no-shell')
        updated=json.loads((directory/'profile.json').read_text())
        self.assertIn('codex',updated['tools'])
        self.assertIn('firebase',updated['tools'])
        self.assertEqual(saved.read_text(),'fictional-existing')

    def test_dependency_removal_and_conflicting_variables_are_rejected(self):
        self.run_loki('tools','install','firebase')
        failed=self.run_loki('tools','remove','google',check=False)
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('require',failed.stderr)
        definition=self.base/'conflict'; definition.mkdir()
        (definition/'tool.toml').write_text('''schema_version=1
id="conflict"
name="Conflict"
description="Conflicting owner"
[env.set]
XDG_CONFIG_HOME="{profile}/different"
''')
        failed=self.run_loki('tools','install','--from',str(definition),check=False)
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('conflicting values for XDG_CONFIG_HOME',failed.stderr)
        self.assertNotIn('conflict',json.loads((self.directory('personal')/'profile.json').read_text())['tools'])

    def test_invalid_definition_cannot_escape_profile_or_replace_builtin_silently(self):
        definition=self.base/'invalid'; definition.mkdir()
        file=definition/'tool.toml'
        file.write_text('''schema_version=1
id="unsafe"
name="Unsafe"
description="Bad cleanup path"
[cleanup]
directories=["../work"]
''')
        failed=self.run_loki('tools','install','--from',str(definition),check=False)
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('inside a persona',failed.stderr)
        file.write_text('''schema_version=1
id="firebase"
name="Override"
description="Explicit replacement is required"
''')
        failed=self.run_loki('tools','install','--from',str(definition),check=False)
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('--replace',failed.stderr)

    def test_catalog_does_not_import_unselected_python_hooks(self):
        definition=self.base/'unselected'; definition.mkdir()
        (definition/'tool.toml').write_text('''schema_version=1
id="unselected"
name="Unselected"
description="A hook must not run merely because a definition exists"
[hooks]
prepare="prepare"
''')
        (definition/'adapter.py').write_text('raise RuntimeError("must not import on discovery")\n')
        with patch.dict(os.environ,self.env,clear=True):
            registry=module.registry()
            registry.import_local(module,definition)
        self.run_loki('tools','list')
        self.run_in_persona('personal','python3','-c','print("unselected hooks not loaded")')
