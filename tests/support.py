"""Disposable tool/persona fixtures available to integration contributors."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOKI = ROOT / 'bin/loki'
sys.path.insert(0, str(ROOT / 'lib'))
from lokishell import cli as module


class LokiFixture(unittest.TestCase):
    initial_tools = ('firebase', 'gcloud', 'adc', 'github', 'claude', 'codex')

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
        for persona in ('personal', 'work'):
            if self.initial_tools:
                self.run_loki('--profile', persona, 'tools', 'install', *self.initial_tools)


    def tearDown(self):
        self.temp.cleanup()


    def run_loki(self, *args, cwd=None, env=None, check=True, input=None):
        return subprocess.run([str(LOKI), *args], cwd=cwd or self.projects.get('personal', self.base),
                              env=env or self.env, text=True, capture_output=True, check=check, input=input)


    def directory(self, name):
        return self.store / 'profiles' / name
