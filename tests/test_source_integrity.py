import hashlib,json,os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,Mock,patch

import httpx
from pegasus_bridge.batch import BatchService
from pegasus_bridge.core import Pegasus,BridgeError
from pegasus_bridge.ui import UIService


class SourceIntegrityTests(unittest.TestCase):
    def test_exact_source_hash_and_internal_read_preserve_legitimate_token_variable(self):
        source='local token=0\nreturn token\n'
        reply=MagicMock(status_code=200)
        reply.json.return_value={'state':{'code':200000},'data':{'command':source,'token':'SERVER_PRIVATE'}}
        with patch.dict(os.environ,{'PEGASUS_TOKEN':'AUTH_PRIVATE'}),patch('pegasus_bridge.core.httpx.Client') as client:
            client.return_value.__enter__.return_value.get.return_value=reply
            bridge=Pegasus()
            public=bridge.read('commands',42,1)
            self.assertTrue(public['source_redacted'])
            self.assertEqual(public['source_sha256'],hashlib.sha256(source.encode()).hexdigest())
            internal=bridge.read('commands',42,1,_raw_source=True)
            self.assertEqual(internal['response']['data']['command'],source)
            self.assertEqual(internal['response']['data']['token'],'<redacted>')
            self.assertNotIn('PRIVATE',json.dumps(internal))

    def test_batch_freezes_original_source_not_a_redacted_program(self):
        source='local token=3\nreturn token\n'
        reply=MagicMock(status_code=200)
        reply.json.return_value={'state':{'code':200000},'data':{'command':source,'token':'SERVER_PRIVATE'}}
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'PEGASUS_TOKEN':'AUTH_PRIVATE'}),patch('pegasus_bridge.core.httpx.Client') as client:
            client.return_value.__enter__.return_value.get.return_value=reply
            root=Path(temp);(root/'new.lua').write_text('return 7',encoding='utf-8')
            (root/'input.json').write_text(json.dumps({'game_id':42,'scenes':[{'scene_id':1,'script_path':'new.lua'}]}))
            writes=Mock()
            BatchService(Pegasus(),writes).plan(str(root/'input.json'),str(root/'plan'))
            self.assertEqual((root/'plan/001-before.lua').read_text(),source)
            writes.apply_scene.assert_not_called()

    def test_server_rejection_detail_keeps_tokens_redacted(self):
        response=httpx.Response(200,json={'state':{'code':503003,'message':'csrf_token=PRIVATE 上传限制'}})
        with self.assertRaises(BridgeError) as error:UIService.data(response)
        self.assertIn('503003',str(error.exception))
        self.assertNotIn('PRIVATE',str(error.exception))
