import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pegasus_bridge.core import Pegasus,BridgeError

class TianmaAuthTests(unittest.TestCase):
    def test_native_header_uses_cookie_pair_and_ignores_foreign_origins(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'PEGASUS_ALLOW_LOG_AUTH':'1'},clear=True):
            Path(folder,'log.txt').write_text(
                '[NetworkManager][Request] headerToken=OWN_NATIVE url=https://avg.163.com/avg-editor-api/game/42?csrf_token=PORTAL\n'
                '[NetworkManager][Request] headerToken=FOREIGN url=https://example.com/avg-editor-api/game/42\n')
            headers,params=Pegasus(data=folder)._tianma_auth()
            self.assertEqual(headers['Cookie'],'TOKEN=OWN_NATIVE;TIANMA_TOKEN=OWN_NATIVE')
            self.assertEqual(params,{'csrf_token':'OWN_NATIVE'})

    def test_log_session_requires_opt_in(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(BridgeError):Pegasus(data='.')._tianma_auth()

    def test_invalid_explicit_session_rejected(self):
        with patch.dict(os.environ,{'PEGASUS_TIANMA_TOKEN':'bad;cookie=other'},clear=True):
            with self.assertRaises(BridgeError):Pegasus(data='.')._tianma_auth()
