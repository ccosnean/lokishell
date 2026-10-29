"""Process boundaries and preservation of files owned by the user."""
import base64
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
from unittest.mock import patch

from click.testing import CliRunner
from tests.support import LokiFixture, LOKI, module
from lokishell import setup_files
from lokishell.commands import main


class IsolationTests(LokiFixture):
    initial_tools = ()

    def stub(self, name, body):
        path = self.fake / name
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o755)
        return path

    def test_launchers_preserve_arguments_io_status_and_unrelated_configuration(self):
        self.run_loki('tools', 'install', 'firebase', 'gcloud', 'github', 'claude', 'codex')
        original = self.base / 'outside-xdg' / 'unrelated-login'
        original.parent.mkdir()
        original.write_text('fictional-independent-session')
        self.stub('unrelated', '''import os
from pathlib import Path
assert Path(os.environ['XDG_CONFIG_HOME'], 'unrelated-login').read_text() == 'fictional-independent-session'
assert os.environ['OPENAI_API_KEY'] == 'wrong-inherited-key'
assert os.environ['GOOGLE_APPLICATION_CREDENTIALS'] == '/wrong/adc'
print('unrelated-ok')
''')
        self.stub('firebase', '''import json, os, subprocess, sys
from pathlib import Path
p = Path(os.environ['LOKI_HOME']) / 'profiles' / 'personal'
assert os.environ['XDG_CONFIG_HOME'] == str(p / 'xdg')
assert os.environ['CLOUDSDK_CONFIG'] == str(p / 'gcloud')
assert 'FIREBASE_TOKEN' not in os.environ
assert os.environ['OPENAI_API_KEY'] == 'wrong-inherited-key'
print(json.dumps(sys.argv[1:]))
print(sys.stdin.read())
print('native-stderr', file=sys.stderr)
sys.exit(7)
''')
        args = ['--help', '--', 'with space', '$(false)', 'quotes"\'']
        result = self.run_in_persona('personal', 'firebase', *args, input='stdin-value', check=False)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [json.dumps(args), 'stdin-value'])
        self.assertEqual(result.stderr.strip(), 'native-stderr')
        # Subprocesses and `command` find the launcher; unrelated tools keep
        # their existing configuration and token values in the same shell.
        script = 'command firebase --version </dev/null; unrelated'
        result = self.run_in_persona('personal', 'bash', '--noprofile', '--norc', '-c', script)
        self.assertIn('unrelated-ok', result.stdout)
        child = self.run_in_persona('personal', sys.executable, '-c',
                                   'import subprocess; raise SystemExit(subprocess.call(["firebase", "--version"]))',
                                   input='', check=False)
        self.assertEqual(child.returncode, 7)
        self.assertEqual(original.read_text(), 'fictional-independent-session')

    def test_disabled_launchers_pass_through_and_listing_finds_native_binary(self):
        self.run_loki('tools', 'install', 'firebase')
        rows = json.loads(self.run_in_persona('personal', str(LOKI), 'tools', 'list', '--json').stdout)
        self.assertEqual(rows['tools'][0]['cli_path'], str(self.fake / 'firebase'))
        self.run_loki('tools', 'remove', 'firebase')
        values = json.loads(self.run_in_persona('personal', 'firebase', 'loki-test-env',
                                              'XDG_CONFIG_HOME', 'FIREBASE_TOKEN').stdout)
        self.assertEqual(values, {key: self.env[key] for key in values})

    def test_git_uses_persona_identity_without_recursion_or_shell_git_overrides(self):
        self.run_loki('tools', 'install', 'github')
        self.run_loki('tools', 'configure', 'github', '--set', 'git_name=Persona Test',
                      '--set', 'git_email=persona@example.invalid')
        env = dict(self.env, GIT_AUTHOR_NAME='Independent Author')
        result = self.run_in_persona('personal', 'git', 'var', 'GIT_AUTHOR_IDENT', env=env)
        self.assertTrue(result.stdout.startswith('Persona Test <persona@example.invalid>'))
        result = self.run_in_persona('personal', sys.executable, '-c',
                                    'import os; print(os.environ["GIT_AUTHOR_NAME"])', env=env)
        self.assertEqual(result.stdout.strip(), 'Independent Author')
        self.assertEqual(json.loads(self.run_in_persona('personal', str(LOKI), 'status').stdout)['profile'], 'personal')

    def test_adc_is_an_explicit_sdk_opt_in_and_restores_new_direnv_values(self):
        self.run_loki('tools', 'install', 'firebase', 'gcloud')
        with patch.dict(os.environ, self.env, clear=True):
            enabled = module.environment('personal')
        self.assertEqual(enabled['GOOGLE_APPLICATION_CREDENTIALS'], '/wrong/adc')
        self.run_loki('tools', 'install', 'adc')
        with patch.dict(os.environ, self.env, clear=True):
            enabled = module.environment('personal')
        self.assertEqual(enabled['GOOGLE_APPLICATION_CREDENTIALS'], str(self.directory('personal') / 'gcloud/application_default_credentials.json'))
        self.run_loki('tools', 'remove', 'adc')
        with patch.dict(os.environ, enabled, clear=True):
            restored = module.environment('personal')
        self.assertEqual(restored['GOOGLE_APPLICATION_CREDENTIALS'], '/wrong/adc')
        enabled['GOOGLE_APPLICATION_CREDENTIALS'] = '/another-project/adc.json'
        with patch.dict(os.environ, enabled, clear=True):
            switched = module.environment('work')
        self.assertEqual(switched['GOOGLE_APPLICATION_CREDENTIALS'], '/another-project/adc.json')

    def test_adc_restoration_across_real_direnv_folder_switches(self):
        for name in ('personal', 'work'):
            path = self.projects[name] / '.envrc'
            path.write_text(f'export GOOGLE_APPLICATION_CREDENTIALS=/independent/{name}.json\n')
            self.run_loki('use', name, '--no-shell', cwd=self.projects[name])
        self.run_loki('tools', 'install', 'adc')
        for shell in ('zsh', 'bash'):
            executable = shutil.which(shell)
            if not executable:
                continue
            with self.subTest(shell=shell):
                self.run_loki('tools', 'install', 'adc')
                persona_adc = str(self.directory('personal') / 'gcloud/application_default_credentials.json')
                script = f'eval "$({shlex.quote(str(LOKI))} hook {shell})"\n'
                script += f'[[ $GOOGLE_APPLICATION_CREDENTIALS == {shlex.quote(persona_adc)} ]] || exit 1\n'
                script += f'cd {shlex.quote(str(self.projects["work"]))}\n'
                script += '[[ $GOOGLE_APPLICATION_CREDENTIALS == /independent/work.json ]] || exit 2\n'
                script += f'cd {shlex.quote(str(self.projects["personal"]))}\n'
                script += f'[[ $GOOGLE_APPLICATION_CREDENTIALS == {shlex.quote(persona_adc)} ]] || exit 3\n'
                script += 'loki tools remove adc\n[[ $GOOGLE_APPLICATION_CREDENTIALS == /independent/personal.json ]] || exit 4\n'
                args = [executable, '-df', '-c', script] if shell == 'zsh' else [executable, '--noprofile', '--norc', '-c', script]
                result = subprocess.run(args, cwd=self.projects['personal'], env=self.env,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_custom_process_scopes_can_share_variables_but_not_conflict_on_one_command(self):
        self.run_loki('tools', 'install', 'firebase')
        for name, command in [('independent', 'acme'), ('conflict', 'firebase')]:
            definition = self.base / name
            definition.mkdir()
            (definition / 'tool.toml').write_text(f'''schema_version=1
id="{name}"
name="{name}"
description="Scoped configuration test"
[cli]
command="{command}"
[env]
scope="process"
[env.set]
XDG_CONFIG_HOME="{{profile}}/other-xdg"
''')
            result = self.run_loki('tools', 'install', '--from', str(definition), check=False)
            if name == 'independent':
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('conflicting values for XDG_CONFIG_HOME', result.stderr)
        self.stub('acme', 'import os; print(os.environ["XDG_CONFIG_HOME"])\n')
        self.assertEqual(self.run_in_persona('personal', 'acme').stdout.strip(), str(self.directory('personal') / 'other-xdg'))
        values = json.loads(self.run_in_persona('personal', 'firebase', 'loki-test-env', 'XDG_CONFIG_HOME').stdout)
        self.assertEqual(values['XDG_CONFIG_HOME'], str(self.directory('personal') / 'xdg'))

    def test_path_restoration_keeps_user_changes_and_old_exports_are_restored(self):
        self.run_loki('tools', 'install', 'firebase')
        with patch.dict(os.environ, self.env, clear=True):
            enabled = module.environment('personal')
        extra = str(self.base / 'project-bin')
        enabled['PATH'] = extra + os.pathsep + enabled['PATH']
        with patch.dict(os.environ, enabled, clear=True):
            restored = module.environment(None)
        self.assertEqual(restored['PATH'], extra + os.pathsep + self.env['PATH'])
        self.assertNotIn('LOKI_SHIM_PATH', restored)
        self.assertNotIn('LOKI_ENV_APPLIED', restored)
        # Upgrade from the former shell-wide export format without logging the
        # snapshot or reading any real credentials.
        legacy = dict(self.env, XDG_CONFIG_HOME='old-persona-xdg',
                      LOKI_ENV_STATE=base64.urlsafe_b64encode(json.dumps({
                          'XDG_CONFIG_HOME': self.env['XDG_CONFIG_HOME']}).encode()).decode())
        with patch.dict(os.environ, legacy, clear=True):
            upgraded = module.environment('personal')
        self.assertEqual(upgraded['XDG_CONFIG_HOME'], self.env['XDG_CONFIG_HOME'])


class SetupFileTests(LokiFixture):
    initial_tools = ()

    def reset(self, home):
        with patch.object(module.Path, 'home', return_value=home), \
             patch('lokishell.reset.interactive_input', return_value=True):
            result = CliRunner().invoke(main, ['reset'], input='y\nRESET LOKISHELL\n', env=self.env)
        self.assertEqual(result.exit_code, 0, result.output)

    def metadata(self, path):
        info = path.stat()
        attrs = {key: os.getxattr(path, key) for key in os.listxattr(path)} if hasattr(os, 'listxattr') else {}
        if sys.platform == 'darwin':
            keys = subprocess.check_output(['/usr/bin/xattr', str(path)], text=True).splitlines()
            attrs = {key: subprocess.check_output(['/usr/bin/xattr', '-px', key, str(path)]) for key in keys}
        return stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid, attrs

    def test_use_clean_and_reset_preserve_modes_metadata_and_crlf(self):
        home = self.base / 'metadata-home'
        home.mkdir()
        project = self.base / 'metadata-project'
        project.mkdir()
        files = {project / '.envrc': b'# keep\r\nexport PROJECT_VALUE=untouched\r\n',
                 home / '.zshrc': b'# shell customization\r\n', home / '.zshenv': b''}
        for path, content in files.items():
            path.write_bytes(content)
            path.chmod(0o754 if path.name == '.envrc' else 0o640)
            if sys.platform == 'darwin':
                subprocess.run(['/usr/bin/xattr', '-w', 'com.lokishell.test', 'fictional-metadata', str(path)], check=True)
                subprocess.run(['/bin/chmod', '+a', 'everyone allow read', str(path)], check=True)
        before = {path: self.metadata(path) for path in files}
        acl = {path: subprocess.check_output(['/bin/ls', '-lde', str(path)]).splitlines()[1:]
               for path in files} if sys.platform == 'darwin' else {}
        for _ in range(2):
            self.run_loki('use', 'personal', '--no-shell', cwd=project)
            self.assertEqual(self.metadata(project / '.envrc'), before[project / '.envrc'])
        self.run_loki('clean', cwd=project)
        self.assertEqual((project / '.envrc').read_bytes(), files[project / '.envrc'])
        self.run_loki('use', 'personal', '--no-shell', cwd=project)
        with patch.dict(os.environ, dict(self.env, SHELL='/bin/zsh'), clear=True), \
             patch.object(module.Path, 'home', return_value=home), \
             patch.object(sys, 'argv', [str(LOKI)]), patch('sys.stdout', new=io.StringIO()):
            module.install_shell()
            module.install_shell()
        self.assertEqual(stat.S_IMODE((self.directory('personal') / 'profile.json').stat().st_mode), 0o600)
        self.reset(home)
        for path, content in files.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(self.metadata(path), before[path])
            if path in acl:
                self.assertEqual(subprocess.check_output(['/bin/ls', '-lde', str(path)]).splitlines()[1:], acl[path])

    def test_empty_user_files_survive_but_loki_created_files_are_removed(self):
        home = self.base / 'empty-home'
        home.mkdir()
        for content in ('', ' \n\t\n'):
            project = self.base / ('empty-project' if not content else 'whitespace-project')
            project.mkdir()
            path = project / '.envrc'
            path.write_text(content)
            path.chmod(0o644)
            self.run_loki('use', 'personal', '--no-shell', cwd=project)
            self.run_loki('clean', cwd=project)
            self.assertEqual(path.read_text(), content)
            self.run_loki('use', 'personal', '--no-shell', cwd=project)
        self.reset(home)
        self.assertEqual((self.base / 'empty-project/.envrc').read_text(), '')
        self.assertEqual((self.base / 'whitespace-project/.envrc').read_text(), ' \n\t\n')
        self.assertFalse((self.projects['personal'] / '.envrc').exists())

    def test_file_copy_failure_or_concurrent_edit_preserves_original(self):
        path = self.base / 'preserved.envrc'
        path.write_text('original\n')
        path.chmod(0o644)
        with patch('lokishell.setup_files.subprocess.run', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):
                setup_files.write(module, path, 'replacement\n', 'original\n')
        self.assertEqual(path.read_text(), 'original\n')
        copy = subprocess.run
        def edit_during_copy(*args, **kwargs):
            result = copy(*args, **kwargs)
            path.write_text('edited by user\n')
            return result
        with patch('lokishell.setup_files.subprocess.run', side_effect=edit_during_copy):
            with self.assertRaisesRegex(ValueError, 'changed during editing'):
                setup_files.write(module, path, 'replacement\n', 'original\n')
        self.assertEqual(path.read_text(), 'edited by user\n')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
