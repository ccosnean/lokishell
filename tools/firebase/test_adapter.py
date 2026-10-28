"""Optional verification against an actual installed Firebase CLI library."""
import json
import os
from tests.support import LokiFixture


class FirebaseTests(LokiFixture):
    initial_tools = ('firebase',)

    def test_real_firebase_store_if_available(self):
        module_path=os.environ.get('LOKI_TEST_FIREBASE_CONFIGSTORE_MODULE')
        if not module_path:
            self.skipTest('Optional installed Firebase library check')
        for name in ('personal','work'):
            expression='process.stdout.write(require('+json.dumps(module_path)+').configstore.path)'
            result=self.run_loki('--profile',name,'exec','node','-e',expression)
            self.assertEqual(result.stdout,str(self.directory(name)/'xdg/configstore/firebase-tools.json'))
