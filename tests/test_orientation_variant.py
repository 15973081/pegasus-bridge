import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import httpx
from pegasus_bridge.core import BridgeError
from pegasus_bridge.orientation_variant import OrientationVariantService


class OrientationVariantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cp = Path(self.tmp.name) / 'variant.json'
        self.source = dict(id=42, gameName='source', subtitle='story', langType=1,
                           creativeType=1, sourceType=5, areaType=0, studioId=9,
                           authorId=7, status=0, orientationMode=1)
        self.original = copy.deepcopy(self.source)
        self.games = {42: self.source}
        self.modes = {42: dict(mode=8, orientationMode=1, adaptMode=1)}
        self.calls = []
        self.behavior = ''
        self.bridge = Mock()
        self.bridge._auth.return_value = ({'Cookie': 'TOKEN=secret'}, {'csrf_token': 'secret'})
        self.s = OrientationVariantService(self.bridge, httpx.MockTransport(self.handle))

    def handle(self, req):
        path = req.url.path.removeprefix('/avg-editor-api')
        self.calls.append((req.method, path))
        self.assertEqual(req.url.host, 'avg.163.com')
        self.assertEqual(req.headers['cookie'], 'TOKEN=secret')
        if req.method == 'POST':
            cp = json.loads(self.cp.read_text(encoding='utf8'))
            body = json.loads(req.content)
            if path == '/game':
                self.assertEqual(cp['status'], 'create_pending')
                self.assertEqual(body['matchId'], -1)
                if self.behavior == 'reject_create':
                    return httpx.Response(200, json={'state': {'code': 500003}})
                self.games[99] = dict(body, id=99, authorId=7, status=0)
                if self.behavior == 'lose_create':
                    raise httpx.ReadTimeout('csrf_token=secret')
                return self.ok({'id': 42 if self.behavior == 'source_id' else 99})
            self.assertEqual(path, '/game/99/config')
            self.assertEqual(cp['status'], 'init_pending')
            self.assertEqual(body['mode'], 8)
            self.assertEqual(body['templateId'], 32 if body['orientationMode'] == 2 else 28)
            if self.behavior == 'reject_init':
                return httpx.Response(200, json={'state': {'code': 507002}})
            self.modes[99] = dict(mode=8, orientationMode=body['orientationMode'], adaptMode=1)
            if self.behavior == 'mismatch': self.modes[99]['orientationMode'] = 1
            if self.behavior == 'wrong_engine': self.modes[99]['mode'] = 2
            if self.behavior == 'lose_init': raise httpx.ReadTimeout('csrf_token=secret')
            return self.ok(None)
        self.assertEqual(req.method, 'GET')
        if path == '/game': return self.ok(list(self.games.values()))
        gid = int(path.split('/')[2])
        if path.endswith('/edit/mode'):
            if gid not in self.modes:
                return httpx.Response(200, json={'state': {'code': 404}})
            return self.ok(self.modes[gid])
        if gid == 99 and self.behavior == 'wrong_owner':
            return self.ok(dict(self.games[gid], authorId=999))
        return self.ok(self.games[gid])

    def ok(self, data):
        return httpx.Response(200, json={'state': {'code': 200000}, 'data': copy.deepcopy(data)})

    def create(self, orientation='vertical', sha=None):
        sha = sha or self.s.get_orientation(42)['sha256']
        return self.s.create_variant(42, 'variant', orientation, sha, str(self.cp))

    def posts(self): return [p for m,p in self.calls if m == 'POST']

    def test_portrait_verified_source_untouched(self):
        r = self.create()
        self.assertTrue(r['verified'])
        self.assertEqual(r['game_id'], 99)
        self.assertEqual(r['profile']['canvas_width'], 1080)
        self.assertFalse(r['content_copied'])
        self.assertEqual(r['migration']['status'], 'not_started')
        self.assertEqual(self.posts(), ['/game', '/game/99/config'])
        self.assertEqual(self.source, self.original)
        self.assertNotIn('secret', self.cp.read_text(encoding='utf8'))

    def test_landscape(self):
        r = self.create('horizontal')
        self.assertTrue(r['verified'])
        self.assertEqual(r['profile']['template_id'], 28)

    def test_repeat_only_reads(self):
        self.create()
        self.assertTrue(self.create()['reused'])
        self.assertEqual(len(self.posts()), 2)

    def test_lost_create_no_duplicate_or_adoption(self):
        self.behavior = 'lose_create'
        self.assertFalse(self.create()['verified'])
        r = self.create()
        self.assertEqual(r['candidate_game_ids'], [99])
        self.assertIsNone(r['game_id'])
        self.assertEqual(self.posts(), ['/game'])

    def test_lost_init_readback(self):
        self.behavior = 'lose_init'
        self.assertTrue(self.create()['verified'])
        self.create()
        self.assertEqual(len(self.posts()), 2)

    def test_rejected_init_never_retried(self):
        self.behavior = 'reject_init'
        self.assertFalse(self.create()['verified'])
        self.assertFalse(self.create()['verified'])
        self.assertEqual(len(self.posts()), 2)

    def test_rejected_create_never_replayed(self):
        self.behavior = 'reject_create'
        self.create(); self.create()
        self.assertEqual(self.posts(), ['/game'])

    def test_inconsistent_direction(self):
        self.behavior = 'mismatch'
        self.assertEqual(self.create()['status'], 'unconfirmed')

    def test_wrong_engine(self):
        self.behavior = 'wrong_engine'
        self.assertFalse(self.create()['verified'])

    def test_source_id_never_initialized(self):
        self.behavior = 'source_id'
        self.assertFalse(self.create()['verified'])
        self.assertEqual(self.posts(), ['/game'])

    def test_wrong_owner_never_initialized(self):
        self.behavior = 'wrong_owner'
        with self.assertRaises(BridgeError): self.create()
        self.assertEqual(self.posts(), ['/game'])

    def test_duplicate_name_not_adopted(self):
        self.games[98] = dict(self.source, id=98, gameName='variant')
        with self.assertRaises(BridgeError): self.create()
        self.assertEqual(self.posts(), [])

    def test_stale_hash(self):
        sha = self.s.get_orientation(42)['sha256']
        self.source['subtitle'] = 'changed'
        with self.assertRaises(BridgeError): self.create(sha=sha)
        self.assertFalse(self.cp.exists())
        self.assertEqual(self.posts(), [])

    def test_checkpoint_mismatch(self):
        self.create()
        with self.assertRaises(BridgeError): self.create('horizontal')
        self.assertEqual(len(self.posts()), 2)

    def test_invalid_input(self):
        for name in ['', ' test', 'x\n', 'x'*31]:
            with self.assertRaises(BridgeError):
                self.s.create_variant(42,name,'vertical','0'*64,str(self.cp))
        self.assertEqual(self.calls, [])

    def test_changed_target_on_resume(self):
        self.create()
        self.games[99]['gameName'] = 'user edited'
        self.assertFalse(self.create()['verified'])
        self.assertEqual(len(self.posts()), 2)

    def test_interruption_before_initialization_can_resume_once(self):
        original = self.s.data
        def interrupted(response):
            if response.request.url.path == '/avg-editor-api/game/99':
                raise KeyboardInterrupt()
            return original(response)
        self.s.data = interrupted
        with self.assertRaises(KeyboardInterrupt): self.create()
        self.assertEqual(json.loads(self.cp.read_text(encoding='utf8'))['status'], 'created')
        self.s.data = original
        self.assertTrue(self.create()['verified'])
        self.assertEqual(self.posts(), ['/game', '/game/99/config'])

    def test_source_race_blocks_create(self):
        original = self.s._snapshot
        count = 0
        def snapshot(*args):
            nonlocal count
            count += 1
            if count == 3: self.source['subtitle'] = 'changed'
            return original(*args)
        self.s._snapshot = snapshot
        with self.assertRaises(BridgeError): self.create()
        self.assertEqual(self.posts(), [])

    def test_published_source_blocked(self):
        self.source['status'] = 2
        with self.assertRaises(BridgeError): self.create()
        self.assertEqual(self.posts(), [])


if __name__ == '__main__': unittest.main()
