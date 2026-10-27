import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import httpx
from pegasus_bridge.core import BridgeError
from pegasus_bridge.scene import SceneService, source_hash


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = '-- 旧剧情 😀\nreturn 1'
        self.new = '-- 新剧情\nreturn 2'
        self.remote = self.old
        self.path = self.root / 'scene.lua'
        self.path.write_text(self.new, encoding='utf-8')
        self.requests = []
        self.bridge = Mock()
        self.bridge._auth.return_value = ({}, {})
        self.handler = self.handle

    def handle(self, req):
        self.requests.append(req)
        if req.method == 'PUT':
            self.remote = next(c['content'] for c in json.loads(req.content)['commandList'] if c['type']=='INSERT')
            data = {}
        elif req.url.path.endswith('/token'):
            data = {'token': 'private'}
        else:
            data = {'command': self.remote}
        return httpx.Response(200, json={'state': {'code': 200000}, 'data': data})

    def apply(self, expected=None):
        return SceneService(self.bridge, httpx.MockTransport(self.handler), lambda gid: 'session').apply_scene(
            42, 123, str(self.path), expected or source_hash(self.old), str(self.root / 'backups'))

    def test_utf16_offsets_backup_and_readback(self):
        result = self.apply()
        self.assertTrue(result['verified'])
        backup = json.loads(Path(result['backup']).read_text(encoding='utf-8'))
        self.assertEqual(backup['before'], self.old)
        put = next(req for req in self.requests if req.method == 'PUT')
        self.assertEqual(json.loads(put.content)['commandList'][0]['length'], len(self.old.encode('utf-16-le')) // 2)
        self.assertNotIn('private', json.dumps(backup))

    def test_conflict(self):
        with self.assertRaises(BridgeError): self.apply('0' * 64)
        self.assertFalse(any(r.method == 'PUT' for r in self.requests))

    def test_empty_scene_only_inserts(self):
        self.old=self.remote=''
        self.assertTrue(self.apply()['verified'])
        put=next(req for req in self.requests if req.method=='PUT')
        self.assertEqual([c['type'] for c in json.loads(put.content)['commandList']],['INSERT'])

    def test_concurrent_change(self):
        def handler(req):
            if req.url.path.endswith('/token'): self.remote = '-- user edit'
            return self.handle(req)
        self.handler = handler
        with self.assertRaises(BridgeError): self.apply()
        self.assertFalse(any(r.method == 'PUT' for r in self.requests))

    def test_backup_failure(self):
        (self.root / 'backups').write_text('blocked')
        with self.assertRaises(BridgeError): self.apply()
        self.assertFalse(any(r.method == 'PUT' for r in self.requests))

    def test_readback_mismatch_no_retry(self):
        def handler(req):
            response = self.handle(req)
            if req.method == 'PUT': self.remote = '-- competing change'
            return response
        self.handler = handler
        with self.assertRaises(BridgeError): self.apply()
        self.assertEqual(sum(r.method == 'PUT' for r in self.requests), 1)

    def test_identical_no_write(self):
        self.remote = self.new
        self.assertFalse(self.apply(source_hash(self.new))['changed'])
        self.assertFalse(any(r.method == 'PUT' for r in self.requests))
