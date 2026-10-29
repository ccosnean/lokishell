"""Optional verification against an actual installed Firebase CLI library."""
import json
import os
import subprocess
from unittest.mock import patch
from tests.support import LokiFixture, module


class FirebaseTests(LokiFixture):
    initial_tools = ('firebase',)

    def test_real_firebase_store_if_available(self):
        module_path=os.environ.get('LOKI_TEST_FIREBASE_CONFIGSTORE_MODULE')
        if not module_path:
            self.skipTest('Optional installed Firebase library check')
        for name in ('personal','work'):
            expression='process.stdout.write(require('+json.dumps(module_path)+').configstore.path)'
            with patch.dict(os.environ, self.env, clear=True):
                env = module.environment(name, tools=['firebase'])
            result=subprocess.run(['node','-e',expression],env=env,check=True,text=True,capture_output=True)
            self.assertEqual(result.stdout,str(self.directory(name)/'xdg/configstore/firebase-tools.json'))
