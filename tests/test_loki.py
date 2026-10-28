"""End-to-end checks use fictional credentials and disposable profile directories."""
from concurrent.futures import ThreadPoolExecutor
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

LOKI = Path(__file__).resolve().parents[1] / 'bin/loki'
loader = importlib.machinery.SourceFileLoader('loki_test_module', str(LOKI))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class LokiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lokishell-test-')
        self.base = Path(self.temp.name).resolve()
        self.store = self.base / 'profiles-home'
        self.fake = self.base / 'fake-bin'
        self.fake.mkdir()
        self.env = dict(os.environ, LOKI_HOME=str(self.store), DIRENV_CONFIG=str(self.base / 'direnv'),
                        XDG_DATA_HOME=str(self.base / 'data'), XDG_CACHE_HOME=str(self.base / 'cache'),
                        PATH=str(self.fake)+os.pathsep+os.environ['PATH'], GH_TOKEN='wrong-inherited-token',
                        FIREBASE_TOKEN='wrong-inherited-token', ANTHROPIC_API_KEY='wrong-inherited-key',
                        OPENAI_API_KEY='wrong-inherited-key', CODEX_SQLITE_HOME='/wrong/sqlite',
                        CLOUDSDK_AUTH_ACCESS_TOKEN='wrong-inherited-token',
                        GOOGLE_APPLICATION_CREDENTIALS='/wrong/adc', FAKE_LOG=str(self.base / 'calls.jsonl'))
        for name in ('firebase', 'gcloud', 'claude', 'codex', 'gh'):
            script = self.fake / name
            script.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name=Path(sys.argv[0]).name
if name=='gh':
 assert sys.argv[1:5]==['auth','token','--hostname','github.com'], sys.argv
 print('fictional-github-token')
else:
 with open(os.environ['FAKE_LOG'],'a') as f:
  f.write(json.dumps({'tool':name,'args':sys.argv[1:],'profile':os.environ.get('LOKI_PROFILE'),
                     'claude':os.environ.get('CLAUDE_CONFIG_DIR'),'codex':os.environ.get('CODEX_HOME'),
                     'adc':os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')})+'\\n')
 if os.environ.get('FAIL_TOOL')==name: sys.exit(9)
''')
            script.chmod(0o700)
        self.projects = {}
        for name in ('personal', 'work', 'personal-two'):
            project = self.base / name
            project.mkdir()
            self.projects[name] = project
            self.run_loki('init', '--profile', name.split('-')[0], '--no-shell', cwd=project)

    def tearDown(self):
        self.temp.cleanup()

    def run_loki(self, *args, cwd=None, env=None, check=True, input=None):
        return subprocess.run([str(LOKI), *args], cwd=cwd or self.projects.get('personal', self.base),
                              env=env or self.env, text=True, capture_output=True, check=check, input=input)

    def directory(self, name):
        return self.store / 'profiles' / name

    def test_parallel_profiles_and_ambient_credentials(self):
        for name in ('personal', 'work'):
            (self.directory(name) / 'auth/github-token').write_text('fictional-'+name)
        probe = '''import os,json
from pathlib import Path
n=os.environ['LOKI_PROFILE']; p=Path(os.environ['LOKI_HOME'])/'profiles'/n
assert os.environ['GH_TOKEN']=='fictional-'+n
assert os.environ['CLOUDSDK_CONFIG']==str(p/'gcloud')
assert os.environ['GOOGLE_APPLICATION_CREDENTIALS']==str(p/'gcloud/application_default_credentials.json')
assert os.environ['CODEX_HOME']==str(p/'codex')
assert os.environ['CLAUDE_CONFIG_DIR']==str(p/'claude')
for key in ['FIREBASE_TOKEN','ANTHROPIC_API_KEY','OPENAI_API_KEY','CODEX_SQLITE_HOME','CLOUDSDK_AUTH_ACCESS_TOKEN']:
 assert key not in os.environ, key
print(n)
'''
        def check(name):
            return self.run_loki('--profile', name, 'exec', 'python3', '-c', probe).stdout.strip()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(set(pool.map(check, ('personal', 'work'))), {'personal', 'work'})

    def test_shared_profile_and_local_clean_preserves_project_settings(self):
        project = self.projects['personal']
        original = 'export PROJECT_SETTING=keep\n'
        (project / '.envrc').write_text(original)
        self.run_loki('init', '--profile', 'personal', '--no-shell')
        self.run_loki('init', '--profile', 'personal', '--no-shell')
        self.assertEqual((project / '.envrc').read_text().count('# BEGIN lokishell'), 1)
        token = self.directory('personal') / 'auth/github-token'
        token.write_text('fictional-personal')
        self.run_loki('clean')
        self.assertEqual((project / '.envrc').read_text(), original)
        self.assertEqual(token.read_text(), 'fictional-personal')
        self.assertEqual(json.loads(self.run_loki('status', cwd=self.projects['personal-two']).stdout)['profile'], 'personal')

    @unittest.skipUnless(shutil.which('zsh'), 'zsh not available')
    def test_ordinary_commands_in_fresh_agent_shell_and_cd(self):
        dot = self.base / 'zdot'
        dot.mkdir()
        hook = self.run_loki('hook', 'zsh').stdout
        (dot / '.zshenv').write_text(hook)
        env = dict(self.env, ZDOTDIR=str(dot))
        command = 'echo "$LOKI_PROFILE"; cd '+str(self.projects['work'])+'; echo "$LOKI_PROFILE"'
        result = subprocess.run(['zsh', '-c', command], cwd=self.projects['personal'], env=env,
                                check=True, text=True, capture_output=True)
        self.assertEqual(result.stdout.splitlines(), ['personal', 'work'])
        result = subprocess.run(['zsh', '-c', 'loki clean; echo "profile=${LOKI_PROFILE-unset}"'],
                                cwd=self.projects['personal'], env=env, check=True, text=True, capture_output=True)
        self.assertIn('profile=personal', result.stdout)

    def test_global_shift_creates_persona_and_folder_override_wins(self):
        self.run_loki('shift','other','--no-shell',cwd=self.base)
        outside=json.loads(self.run_loki('status',cwd=self.base).stdout)
        self.assertEqual(outside['active_persona'],'other')
        self.assertEqual(outside['source'],'global')
        inside=json.loads(self.run_loki('status',cwd=self.projects['work']).stdout)
        self.assertEqual(inside['active_persona'],'work')
        self.assertEqual(inside['global_persona'],'other')
        fresh=self.base/'fresh'; fresh.mkdir()
        self.run_loki('init','--no-shell',cwd=fresh)
        self.assertEqual(json.loads(self.run_loki('status',cwd=fresh).stdout)['profile'],'other')

    @unittest.skipUnless(shutil.which('zsh'), 'zsh not available')
    def test_global_environment_outside_folders_and_live_shift(self):
        dot=self.base/'global-zdot'; dot.mkdir()
        (dot/'.zshenv').write_text(self.run_loki('hook','zsh').stdout)
        command='echo "$LOKI_PROFILE"; loki shift work --no-shell; echo "active=$LOKI_PROFILE"'
        result=subprocess.run(['zsh','-c',command],cwd=self.base,env=dict(self.env,ZDOTDIR=str(dot)),
                              check=True,text=True,capture_output=True)
        self.assertTrue(result.stdout.startswith('personal\n'))
        self.assertIn('active=work',result.stdout)

    def test_worktrees_inherit_repository_persona_and_can_override_independently(self):
        repo=self.base/'repo'; repo.mkdir()
        git=['git','-c','user.name=Test Person','-c','user.email=test@example.invalid',
             '-c','commit.gpgsign=false','-c','core.hooksPath=/dev/null']
        subprocess.run(git+['init','-b','main'],cwd=repo,check=True,capture_output=True)
        (repo/'file').write_text('fixture')
        subprocess.run(git+['add','file'],cwd=repo,check=True,capture_output=True)
        subprocess.run(git+['commit','-m','fixture'],cwd=repo,check=True,capture_output=True)
        self.run_loki('use','work','--no-shell',cwd=repo)
        tree=self.base/'tree'
        subprocess.run(git+['worktree','add','-b','feature',str(tree)],cwd=repo,check=True,capture_output=True)
        self.assertFalse((tree/'.envrc').exists())
        status=json.loads(self.run_loki('status',cwd=tree).stdout)
        self.assertEqual(status['profile'],'work')
        self.assertEqual(status['source'],'repository/worktree')
        if shutil.which('zsh'):
            dot=self.base/'worktree-zdot'; dot.mkdir()
            (dot/'.zshenv').write_text(self.run_loki('hook','zsh').stdout)
            fresh=subprocess.run(['zsh','-c','echo "$LOKI_PROFILE"; echo "$CLOUDSDK_CONFIG"'],cwd=tree,
                                 env=dict(self.env,ZDOTDIR=str(dot)),check=True,text=True,capture_output=True)
            self.assertEqual(fresh.stdout.splitlines(),['work',str(self.directory('work')/'gcloud')])
        self.run_loki('use','personal','--no-shell',cwd=tree)
        self.assertEqual(json.loads(self.run_loki('status',cwd=repo).stdout)['profile'],'work')
        self.assertEqual(json.loads(self.run_loki('status',cwd=tree).stdout)['profile'],'personal')
        self.run_loki('clean',cwd=tree)
        self.assertEqual(json.loads(self.run_loki('status',cwd=tree).stdout)['source'],'global (folder override removed)')
        self.assertEqual(json.loads(self.run_loki('status',cwd=repo).stdout)['profile'],'work')

    def test_concurrent_folder_registration_does_not_lose_pointers(self):
        paths=[self.base/f'parallel-{i}' for i in range(6)]
        for path in paths:
            path.mkdir()
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda path:self.run_loki('use','work','--no-shell',cwd=path),paths))
        data=json.loads((self.store/'settings.json').read_text())
        self.assertTrue(all(data['folders'][str(path)]=='work' for path in paths))

    def test_bash_commands_without_prefix(self):
        startup = self.base / 'bash-env'
        startup.write_text(self.run_loki('hook', 'bash').stdout)
        # Prevent recursion in direnv's own Bash subprocess by loading the hook explicitly.
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c',
                                 'source '+str(startup)+'; echo "$LOKI_PROFILE"; cd '+str(self.projects['work'])+'; echo "$LOKI_PROFILE"'],
                                cwd=self.projects['personal'], env=self.env, check=True, text=True, capture_output=True)
        self.assertEqual(result.stdout.splitlines(), ['personal', 'work'])

    def test_add_routes_logins_to_profile(self):
        self.run_loki('add', 'firebase', 'gcloud', 'adc', 'claude', 'codex')
        rows = [json.loads(x) for x in (self.base / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual([row['tool'] for row in rows], ['firebase','gcloud','gcloud','claude','codex'])
        self.assertTrue(all(row['profile']=='personal' for row in rows))
        self.assertIsNone(rows[2]['adc'])
        self.assertEqual(rows[3]['args'], ['auth', 'login', '--claudeai'])
        self.assertEqual(rows[4]['codex'], str(self.directory('personal')/'codex'))

    def test_global_clean_is_scoped_and_clears_registered_mcp(self):
        for name in ('personal','work'):
            for file in ('auth/github-token','gcloud/credentials.db','xdg/configstore/firebase-tools.json',
                         'codex/auth.json','codex/.credentials.json','claude/.credentials.json'):
                (self.directory(name)/file).write_text('fictional-'+name)
            (self.directory(name)/'codex/history.jsonl').write_text('keep history')
        self.run_loki('add', 'mcp', 'claude', 'tickets')
        self.run_loki('clean', '--global')
        personal, work = self.directory('personal'), self.directory('work')
        self.assertFalse((personal/'auth/github-token').exists())
        self.assertFalse((personal/'codex/auth.json').exists())
        self.assertFalse((personal/'codex/.credentials.json').exists())
        self.assertFalse((personal/'claude/.credentials.json').exists())
        self.assertEqual((work/'auth/github-token').read_text(), 'fictional-work')
        self.assertEqual((work/'gcloud/credentials.db').read_text(), 'fictional-work')
        self.assertEqual((personal/'codex/history.jsonl').read_text(), 'keep history')
        self.assertTrue((self.projects['personal']/'.envrc').exists())
        result=self.run_loki('exec','python3','-c','import os; print(os.environ["GH_TOKEN"])')
        self.assertEqual(result.stdout.strip(),'loki-login-required')
        calls=(self.base/'calls.jsonl').read_text()
        self.assertIn('"logout", "tickets"',calls)

    def test_global_clean_reports_failed_keychain_logout(self):
        (self.directory('personal')/'claude/.credentials.json').write_text('fictional')
        result=self.run_loki('clean','--global',env=dict(self.env,FAIL_TOOL='claude'),check=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Claude Keychain logout',result.stderr)

    def test_github_import_checks_identity_without_login_or_switch(self):
        config=self.directory('personal')/'profile.json'
        old=json.loads(config.read_text())
        old.pop('git_name',None); old.pop('git_email',None)
        config.write_text(json.dumps(old))
        account={'login':'example-personal','id':42,'name':'Example Person','email':None}
        with patch.dict(os.environ,self.env,clear=True), patch.object(sys_module(),'argv',[str(LOKI)]):
            with patch.object(module.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(account).encode())):
                module.github_login('personal','example-personal')
        self.assertEqual((self.directory('personal')/'auth/github-token').read_text().strip(),'fictional-github-token')
        self.assertEqual(json.loads(config.read_text())['git_email'],'42+example-personal@users.noreply.github.com')
        with patch.dict(os.environ,self.env,clear=True):
            with patch.object(module.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(account).encode())):
                with self.assertRaises(ValueError):
                    module.github_login('work','different-account')
        self.assertFalse((self.directory('work')/'auth/github-token').exists())

    def test_git_credential_helper_is_scoped_to_github(self):
        (self.directory('personal')/'auth/github-token').write_text('fictional-personal')
        result=self.run_loki('exec',str(LOKI),'_git-credential','get',input='protocol=https\nhost=github.com\n\n')
        self.assertIn('password=fictional-personal',result.stdout)
        result=self.run_loki('exec',str(LOKI),'_git-credential','get',input='protocol=https\nhost=other.example\n\n')
        self.assertEqual(result.stdout,'')

    def test_init_and_clean_preserve_foreign_files_and_other_profiles(self):
        result=self.run_loki('--profile','work','clean',check=False)
        self.assertNotEqual(result.returncode,0)
        (self.projects['personal']/'.envrc').unlink()
        target=self.base/'preserved'; target.write_text('preserve')
        (self.projects['personal']/'.envrc').symlink_to(target)
        result=self.run_loki('init','--profile','personal','--no-shell',check=False)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(target.read_text(),'preserve')

    def test_global_reset_refuses_symlinked_ancestor_before_deleting_tokens(self):
        token=self.directory('personal')/'auth/github-token'
        token.write_text('fictional-personal')
        outside=self.base/'outside'; outside.mkdir()
        (outside/'configstore').mkdir()
        saved=outside/'configstore/keep'; saved.write_text('preserve')
        xdg=self.directory('personal')/'xdg'
        shutil.rmtree(xdg)
        xdg.symlink_to(outside,target_is_directory=True)
        result=self.run_loki('clean','--global',check=False)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(saved.read_text(),'preserve')
        self.assertEqual(token.read_text(),'fictional-personal')

    def test_shell_install_preserves_customizations_and_is_idempotent(self):
        fake_home=self.base/'shell-home'; fake_home.mkdir()
        original='export EXISTING_SETTING=preserve\n'
        (fake_home/'.zshrc').write_text(original)
        with patch.dict(os.environ,{**self.env,'SHELL':'/bin/zsh'},clear=True), \
             patch.object(module.Path,'home',return_value=fake_home), \
             patch.object(sys_module(),'argv',[str(LOKI)]):
            with patch('sys.stdout',new=io.StringIO()):
                module.install_shell()
                first=(fake_home/'.zshrc').read_text()
                module.install_shell()
        self.assertTrue(first.startswith(original))
        self.assertEqual((fake_home/'.zshrc').read_text(),first)
        self.assertEqual(first.count('# BEGIN lokishell'),1)
        self.assertTrue((fake_home/'.zshenv').is_file())

    def test_real_firebase_store_if_available(self):
        module_path=os.environ.get('LOKI_TEST_FIREBASE_CONFIGSTORE_MODULE')
        if not module_path:
            self.skipTest('Optional installed Firebase library check')
        for name in ('personal','work'):
            expression='process.stdout.write(require('+json.dumps(module_path)+').configstore.path)'
            result=self.run_loki('--profile',name,'exec','node','-e',expression)
            self.assertEqual(result.stdout,str(self.directory(name)/'xdg/configstore/firebase-tools.json'))


def sys_module():
    import sys
    return sys


if __name__=='__main__':
    unittest.main()
