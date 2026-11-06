import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from pegasus_bridge.core import BridgeError
from pegasus_bridge.video import VideoService


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root/'source.mp4'
        self.video.write_bytes(b'VIDEO'*20)
        self.cp = self.root/'upload.json'
        self.calls = []
        self.posts = []
        self.fail_register = False
        bridge = Mock()
        bridge._auth.return_value = ({'Cookie':'PRIVATE'}, {'csrf_token':'PRIVATE'})
        bridge._tianma_auth.return_value = ({'Cookie':'NATIVE_PRIVATE'}, {'csrf_token':'NATIVE_PRIVATE'})
        self.service = VideoService(bridge,httpx.MockTransport(self.handle))
        self.probe = patch('pegasus_bridge.video.subprocess.check_output',return_value=json.dumps({
            'streams':[{'codec_type':'video','codec_name':'h264','width':1920,'height':1080}],
            'format':{'duration':'12.5'}}).encode())
        self.probe.start()
        self.addCleanup(self.probe.stop)
        ffmpeg = patch('pegasus_bridge.video.subprocess.run',side_effect=lambda cmd,**kw: Path(cmd[-1]).write_bytes(b'cover'))
        ffmpeg.start()
        self.addCleanup(ffmpeg.stop)

    def handle(self,req):
        self.calls.append(req)
        if req.url.host == 'fp.ps.netease.com':
            self.assertNotIn('cookie',req.headers)
            self.assertNotIn('csrf_token',req.url.params)
            self.assertNotIn(b'PRIVATE',req.read())
            self.assertIn(b'UPLOAD_SCOPED',req.read())
            return httpx.Response(201,headers={'response-fid':f'file{len(self.calls)}'})
        if req.url.path.endswith('/filepicker/token'):
            if req.url.path.startswith('/tianma-server-api/'):
                self.assertEqual(req.headers['cookie'],'NATIVE_PRIVATE')
                self.assertEqual(req.url.params['csrf_token'],'NATIVE_PRIVATE')
            data = {'token':'UPLOAD_SCOPED'}
        elif req.url.path.endswith('/catalog/3d/v2'):
            data = {'ownerList':[{'id':11,'isDefault':1}]}
        elif req.method == 'POST':
            self.assertTrue(json.loads(self.cp.read_text())['registration_pending'])
            self.posts.append(json.loads(req.content))
            if self.fail_register:
                return httpx.Response(503)
            data = {'id':123,'gameMaterialId':456}
        else:
            data = []
        return httpx.Response(200,json={'state':{'code':200000},'data':data})

    def upload(self):
        return self.service.upload_video(42,str(self.video),'source',str(self.cp))

    def test_upload_scopes_auth_and_reuses_checkpoint_without_cloud_calls(self):
        result = self.upload()
        self.assertEqual(result['id'],123)
        self.assertFalse(result['registration_pending'])
        self.assertEqual(self.posts[0]['categoryId'],4)
        self.assertEqual(self.posts[0]['duration'],12.5)
        self.assertNotIn('PRIVATE',self.cp.read_text())
        count = len(self.calls)
        self.assertTrue(self.upload()['reused'])
        self.assertEqual(len(self.calls),count)

    def test_uncertain_registration_is_not_repeated(self):
        self.fail_register = True
        with self.assertRaises(BridgeError):
            self.upload()
        self.assertTrue(json.loads(self.cp.read_text())['registration_pending'])
        count = len(self.calls)
        with self.assertRaises(BridgeError):
            self.upload()
        self.assertEqual(len(self.calls),count)
        self.assertEqual(len(self.posts),1)

    def test_changed_file_refuses_checkpoint(self):
        self.upload()
        self.video.write_bytes(b'CHANGED'*20)
        with self.assertRaises(BridgeError):
            self.upload()
        self.assertEqual(len(self.posts),1)

    def test_foreign_checkpoint_url_is_refused_even_with_id(self):
        self.cp.write_text(json.dumps(dict(game_id=42,sha256=hashlib.sha256(self.video.read_bytes()).hexdigest(),
                                          alias='source',category_id=4,id=123,url='https://example.org/file')))
        with self.assertRaises(BridgeError):
            self.upload()
        self.assertEqual(self.calls,[])

    def test_non_h264_fails_before_cloud_io(self):
        with patch('pegasus_bridge.video.subprocess.check_output',return_value=json.dumps({
                'streams':[{'codec_type':'video','codec_name':'hevc'}],'format':{'duration':'1'}}).encode()):
            with self.assertRaises(BridgeError):
                self.upload()
        self.assertEqual(self.calls,[])

    def test_catalog_is_read_only_and_bounds_pagination(self):
        result=self.service.catalog(42,11,100,25)
        self.assertEqual(result['items'],[])
        self.assertFalse(result['possibly_more'])
        self.assertTrue(all(x.method == 'GET' for x in self.calls))
        self.assertEqual(self.calls[0].url.params['offset'],'100')
        with self.assertRaises(BridgeError):
            self.service.catalog(42,11,0,101)
    def test_dotted_alias_rejected_before_any_cloud_side_effect(self):
        with self.assertRaises(BridgeError):
            self.service.upload_video(42,str(self.video),'cut_1.000_2.000',str(self.cp))
        self.assertEqual(self.calls,[])

    def test_diagnostic_returns_no_grant_or_authentication_values(self):
        result=self.service.upload_status(42)
        self.assertEqual([r['code'] for r in result['routes']],[200000,200000])
        self.assertNotIn('UPLOAD_SCOPED',json.dumps(result))
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertTrue(all(r.method=='GET' for r in self.calls))
        self.assertEqual(result['materials_registered'],0)

    def test_diagnostic_foreign_url_rejected_before_authentication(self):
        with self.assertRaises(BridgeError):self.service.upload_status(42,['https://example.com/x'])
        self.assertEqual(self.calls,[])
