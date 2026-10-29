import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import httpx
from pegasus_bridge.core import BridgeError
from pegasus_bridge.orientation import OrientationService
from pegasus_bridge.ui import digest


class OrientationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cp = Path(self.temp.name) / 'orientation.json'
        self.game = dict(id=42, gameName='test', subtitle='', langType=1, creativeType=1,
                         sourceType=5, areaType=0, studioId=9, status=0, orientationMode=1)
        self.mode = dict(mode=8, orientationMode=1, adaptMode=1)
        self.calls = []
        self.bridge = Mock()
        self.bridge._auth.return_value = ({'Cookie': 'TOKEN=secret'}, {'csrf_token': 'secret'})
        self.service = OrientationService(self.bridge, httpx.MockTransport(self.handle))

    def handle(self, req):
        self.calls.append((req.method, req.url.path))
        self.assertEqual(req.method, 'GET', 'Compatibility tool must never mutate backend')
        self.assertEqual(req.url.host, 'avg.163.com')
        value = self.mode if req.url.path.endswith('/edit/mode') else self.game
        return httpx.Response(200, json={'state': {'code': 200000}, 'data': copy.deepcopy(value)})

    def switch(self, direction='vertical', sha=None):
        return self.service.set_orientation(42, direction,
            sha or self.service.get_orientation(42)['sha256'], str(self.cp))

    def test_changed_direction_is_unsupported(self):
        result = self.switch()
        self.assertEqual(result['status'], 'unsupported')
        self.assertFalse(result['verified'])
        self.assertFalse(result['remote_write'])
        self.assertEqual(self.game['orientationMode'], 1)
        self.assertNotIn('secret', self.cp.read_text(encoding='utf8'))
        self.assertEqual(self.switch()['status'], 'unsupported')

    def test_noop_is_verified(self):
        self.assertTrue(self.switch('horizontal')['verified'])

    def test_profiles_and_capabilities(self):
        v = self.service.get_orientation(42)
        self.assertFalse(v['supported'])
        self.assertTrue(v['create_variant_supported'])
        self.assertEqual(v['profile']['canvas_width'], 1920)
        self.game['orientationMode'] = self.mode['orientationMode'] = 2
        v = self.service.get_orientation(42)
        self.assertEqual(v['profile']['canvas_width'], 1080)
        self.assertEqual(v['profile']['canvas_height'], 1920)
        self.assertEqual(v['profile']['template_id'], 32)

    def test_inconsistent_direction_has_no_profile(self):
        self.mode['orientationMode'] = 2
        self.assertIsNone(self.service.get_orientation(42)['profile'])

    def test_old_checkpoint_readonly_reconciliation(self):
        before = dict(game=copy.deepcopy(self.game), editor=copy.deepcopy(self.mode))
        sha = digest(before)
        self.cp.write_text(json.dumps(dict(version=1, kind='orientation', game_id=42,
            orientation='vertical', expected_sha256=sha, before=before, status='requesting')), encoding='utf8')
        self.assertFalse(self.switch(sha=sha)['verified'])
        self.game['orientationMode'] = self.mode['orientationMode'] = 2
        self.assertTrue(self.switch(sha=sha)['verified'])

    def test_changed_engine_never_verifies(self):
        sha = self.service.get_orientation(42)['sha256']
        self.switch(sha=sha)
        self.mode.update(mode=2, orientationMode=2)
        self.game['orientationMode'] = 2
        self.assertFalse(self.switch(sha=sha)['verified'])

    def test_stale_hash_and_wrong_checkpoint(self):
        sha = self.service.get_orientation(42)['sha256']
        self.game['gameName'] = 'changed'
        with self.assertRaises(BridgeError): self.switch(sha=sha)
        self.switch()
        with self.assertRaises(BridgeError): self.switch('horizontal')

    def test_invalid_input_and_source(self):
        for direction, sha in [('portrait', '0'*64), ('vertical', 'bad')]:
            with self.assertRaises(BridgeError): self.switch(direction, sha)
        self.game['status'] = 2
        with self.assertRaises(BridgeError): self.switch()
        self.mode['orientationMode'] = True
        with self.assertRaises(BridgeError): self.service.get_orientation(42)


if __name__ == '__main__': unittest.main()
