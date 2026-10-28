"""GitHub credential import must never switch the shared active account."""
import io
import json
import os
import sys
import urllib.request
from unittest.mock import patch
from tests.support import LokiFixture, LOKI, module
from lokishell.registry import Context


def sys_module():
    return sys


class GitHubTests(LokiFixture):
    initial_tools = ('github',)

    def test_github_import_checks_identity_without_login_or_switch(self):
        config=self.directory('personal')/'profile.json'
        old=json.loads(config.read_text())
        old.pop('git_name',None); old.pop('git_email',None)
        old.get('tool_config',{}).get('github',{}).pop('git_name',None)
        old.get('tool_config',{}).get('github',{}).pop('git_email',None)
        config.write_text(json.dumps(old))
        with patch.dict(os.environ,self.env,clear=True):
            tool=module.registry().get('github')
        account={'login':'example-personal','id':42,'name':'Example Person','email':None}
        with patch.dict(os.environ,self.env,clear=True), patch.object(sys_module(),'argv',[str(LOKI)]):
            with patch.object(urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(account).encode())):
                tool.call('login', Context(module, tool, 'personal', {'user':'example-personal'}))
        self.assertEqual((self.directory('personal')/'auth/github-token').read_text().strip(),'fictional-github-token')
        self.assertEqual(json.loads(config.read_text())['tool_config']['github']['git_email'],'42+example-personal@users.noreply.github.com')
        with patch.dict(os.environ,self.env,clear=True):
            with patch.object(urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(account).encode())):
                with self.assertRaises(ValueError):
                    tool.call('login', Context(module, tool, 'work', {'user':'different-account'}))
        self.assertFalse((self.directory('work')/'auth/github-token').exists())
