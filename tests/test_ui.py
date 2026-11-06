import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock

import httpx
from PIL import Image
from pegasus_bridge.core import BridgeError
from pegasus_bridge.ui import UIService, digest


class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = {'playback': {'color': '#fff', 'background': {'resource': 123}}, 'cover': {'resource': 456}}
        self.remote = copy.deepcopy(self.old)
        self.new = {'color': '#a00', 'background': {'resource': 999}}
        self.requests = []
        self.bridge = Mock()
        self.bridge._auth.return_value = ({'Cookie': 'TOKEN=secret'}, {'csrf_token': 'secret'})
        self.bridge._tianma_auth.return_value = ({'Cookie':'TOKEN=native-secret;TIANMA_TOKEN=native-secret'}, {'csrf_token':'native-secret'})
        self.handler = self.handle

    def handle(self, req):
        self.requests.append(req)
        if req.method == 'PUT':
            self.remote['playback'] = json.loads(json.loads(req.content)['template'])
            data = {}
        else:
            data = {'template': json.dumps(self.remote)}
        return httpx.Response(200, json={'state': {'code': 200000}, 'data': data})

    def service(self):
        return UIService(self.bridge, httpx.MockTransport(self.handler))

    def apply(self, expected=None):
        return self.service().apply_template(42, 'playback', self.new,
                expected or digest(self.old['playback']), str(self.root / 'backups'))

    def test_conflict_never_puts(self):
        with self.assertRaises(BridgeError): self.apply('0' * 64)
        self.assertFalse(any(r.method == 'PUT' for r in self.requests))

    def test_concurrent_edit_after_backup_never_puts(self):
        count = 0
        def handler(req):
            nonlocal count
            count += 1
            if count == 2: self.remote['playback']['color'] = 'changed'
            return self.handle(req)
        self.handler = handler
        with self.assertRaises(BridgeError): self.apply()
        self.assertFalse(any(r.method == 'PUT' for r in self.requests))

    def test_backup_failure_never_puts(self):
        (self.root / 'backups').write_text('blocked', encoding='utf-8')
        with self.assertRaises(BridgeError): self.apply()
        self.assertFalse(any(r.method == 'PUT' for r in self.requests))

    def test_verified_write_keeps_backup_and_other_sections(self):
        result = self.apply()
        self.assertTrue(result['verified'])
        self.assertTrue(result['other_sections_unchanged'])
        backup = json.loads(Path(result['backup']).read_text(encoding='utf-8'))
        self.assertEqual(backup['before'], self.old)
        self.assertEqual(self.remote['cover'], self.old['cover'])

    def test_mismatch_never_claims_success_or_blind_rolls_back(self):
        def handler(req):
            response = self.handle(req)
            if req.method == 'PUT': self.remote['playback']['color'] = 'different'
            return response
        self.handler = handler
        with self.assertRaises(BridgeError): self.apply()
        self.assertEqual(sum(r.method == 'PUT' for r in self.requests), 1)

    def test_field_removal_rejected(self):
        self.new.pop('background')
        with self.assertRaises(BridgeError): self.apply()
        self.assertFalse(any(r.method == 'PUT' for r in self.requests))

    def test_identical_template_does_not_write_or_create_backup(self):
        self.new = copy.deepcopy(self.old['playback'])
        result = self.apply()
        self.assertFalse(result['changed'])
        self.assertTrue(result['verified'])
        self.assertFalse((self.root / 'backups').exists())
        self.assertFalse(any(r.method == 'PUT' for r in self.requests))

    def test_redirect_and_api_rejection_do_not_write(self):
        for response in [httpx.Response(302, headers={'location': 'https://example.com'}),
                         httpx.Response(200, json={'state': {'code': 502001}})]:
            self.requests = []
            def handler(req):
                self.requests.append(req)
                return response
            self.handler = handler
            with self.assertRaises(BridgeError): self.apply()
            self.assertEqual(len(self.requests), 1)
            self.assertFalse(any(r.method == 'PUT' for r in self.requests))

    def test_invalid_png_rejected_before_auth(self):
        png = self.root / 'invalid.png'
        png.write_bytes(b'not a png but long enough to pass the size check')
        with self.assertRaises(BridgeError):
            self.service().upload_image(42, str(png), 'test', 2, str(self.root / 'upload.json'))
        self.bridge._auth.assert_not_called()

    def test_upload_isolates_credentials_and_checkpoints_identity(self):
        png = self.root / 'image.png'
        Image.new('RGBA', (481, 305), '#123456').save(png)
        calls = []
        def handler(req):
            calls.append(req)
            if req.url.host == 'fp.ps.netease.com':
                self.assertNotIn('cookie', req.headers)
                self.assertNotIn('csrf_token', req.url.params)
                self.assertNotIn(b'secret', req.content)
                return httpx.Response(201, headers={'response-fid': 'valid-file'})
            native='filepicker' in req.url.path
            self.assertEqual(req.headers['cookie'], 'TOKEN=native-secret;TIANMA_TOKEN=native-secret' if native else 'TOKEN=secret')
            self.assertEqual(req.url.params['csrf_token'], 'native-secret' if native else 'secret')
            if native:self.assertEqual(req.url.path,'/tianma-server-api/filepicker/token')
            if 'catalog' in req.url.path: data = {'ownerList': [{'id': 5, 'isDefault': 1}]}
            elif 'filepicker' in req.url.path: data = {'token': 'scoped-file-token'}
            else: data = {'id': 987, 'gameMaterialId': 88}
            return httpx.Response(200, json={'state': {'code': 200000}, 'data': data})
        self.handler = handler
        service = self.service()
        args = (42, str(png), 'test-image', 2, str(self.root / 'upload.json'))
        result = service.upload_image(*args)
        self.assertEqual(result['id'], 987)
        before_count = len(calls)
        self.assertTrue(service.upload_image(*args)['reused'])
        self.assertEqual(len(calls), before_count)
        with self.assertRaises(BridgeError): service.upload_image(43, *args[1:])

    def test_image_registration_timeout_blocks_second_post(self):
        png = self.root / 'image.png'
        Image.new('RGBA', (8, 8)).save(png)
        cp = self.root / 'upload.json'
        import hashlib
        cp.write_text(json.dumps(dict(game_id=42, sha256=hashlib.sha256(png.read_bytes()).hexdigest(),
            alias='uncertain', category_id=2, url='https://a13.fp.ps.netease.com/file/test',
            file=str(png), width=8, height=8)), encoding='utf-8')
        calls = []
        def handler(req):
            calls.append(req)
            if req.method == 'POST':
                self.assertTrue(json.loads(cp.read_text())['registration_pending'])
                raise httpx.ReadTimeout('response lost', request=req)
            return httpx.Response(200, json={'state': {'code': 200000},
                'data': {'ownerList': [{'id': 5, 'isDefault': 1}]}})
        self.handler = handler
        args = (42, str(png), 'uncertain', 2, str(cp))
        with self.assertRaises(BridgeError): self.service().upload_image(*args)
        before = len(calls)
        with self.assertRaisesRegex(BridgeError, '结果不确定'): self.service().upload_image(*args)
        self.assertEqual(len(calls), before)
        self.assertEqual(sum(r.method == 'POST' for r in calls), 1)
        self.assertFalse(cp.with_suffix('.json.lock').exists())

    def test_image_lock_prevents_concurrent_registration(self):
        png = self.root / 'image.png'
        Image.new('RGBA', (8, 8)).save(png)
        cp = self.root / 'upload.json'
        cp.with_suffix('.json.lock').write_text('other process')
        with self.assertRaisesRegex(BridgeError, '任务锁'):
            self.service().upload_image(42, str(png), 'locked', 2, str(cp))
        self.bridge._auth.assert_not_called()

    def test_truncated_png_header_is_not_uploadable(self):
        png = self.root / 'broken.png'
        png.write_bytes(b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 481, 305))
        with self.assertRaises(BridgeError):
            self.service().upload_image(42, str(png), 'broken', 2, str(self.root / 'upload.json'))
        self.bridge._auth.assert_not_called()

    def test_image_catalog_uses_explicit_image_category(self):
        def handler(req):
            self.assertEqual(req.url.params['categoryId'], '2')
            self.assertEqual(req.url.params['materialType'], 'imageV2')
            return httpx.Response(200, json={'state': {'code': 200000}, 'data': {'ownerList': []}})
        self.handler = handler
        self.assertEqual(self.service().image_catalog(42), {'ownerList': []})
        with self.assertRaises(BridgeError): self.service().image_catalog(42, True)

    def test_png_with_valid_chunk_crc_but_bad_compressed_pixels_is_rejected(self):
        import zlib
        png=self.root/'corrupt.png'
        Image.new('RGBA',(8,8)).save(png)
        data=png.read_bytes();pos=8;parts=[data[:8]]
        while pos<len(data):
            length=struct.unpack('>I',data[pos:pos+4])[0];kind=data[pos+4:pos+8];body=data[pos+8:pos+8+length]
            if kind==b'IDAT':body=b'bad-compressed-pixels'
            parts.append(struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff))
            pos+=length+12
        png.write_bytes(b''.join(parts))
        with self.assertRaises(BridgeError):
            self.service().upload_image(42,str(png),'corrupt',2,str(self.root/'upload.json'))
        self.bridge._auth.assert_not_called()

    def test_registered_image_checkpoint_rejects_external_url(self):
        import hashlib
        png=self.root/'image.png';Image.new('RGBA',(8,8)).save(png)
        cp=self.root/'upload.json'
        cp.write_text(json.dumps(dict(game_id=42,sha256=hashlib.sha256(png.read_bytes()).hexdigest(),
            alias='test',category_id=2,id=123,url='https://example.com/file')),encoding='utf-8')
        with self.assertRaises(BridgeError):self.service().upload_image(42,str(png),'test',2,str(cp))
        self.bridge._auth.assert_not_called()

    def test_image_upload_rate_limit_surfaces_numeric_retry_after(self):
        png=self.root/'image.png';Image.new('RGBA',(8,8)).save(png)
        def handler(req):
            if req.url.host=='fp.ps.netease.com':return httpx.Response(429,headers={'retry-after':'120'})
            data={'ownerList':[{'id':5,'isDefault':1}]} if 'catalog' in req.url.path else {'token':'scoped'}
            return httpx.Response(200,json={'state':{'code':200000},'data':data})
        self.handler=handler
        cp=self.root/'upload.json'
        with self.assertRaisesRegex(BridgeError,'Retry-After=120'):
            self.service().upload_image(42,str(png),'limited',2,str(cp))
        self.assertFalse(cp.exists())

    def test_network_errors_do_not_expose_auth_url(self):
        def handler(req): raise httpx.ConnectError('https://host/?csrf_token=secret', request=req)
        self.handler = handler
        with self.assertRaises(BridgeError) as ctx: self.apply()
        self.assertNotIn('secret', str(ctx.exception))


if __name__ == '__main__': unittest.main()
