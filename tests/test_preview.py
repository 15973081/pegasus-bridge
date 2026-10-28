import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import httpx

from pegasus_bridge.core import BridgeError
from pegasus_bridge.preview import PreviewService
from pegasus_bridge.preview_runtime import PreviewGame, load_bundle

HAS_LUA = importlib.util.find_spec('lupa') is not None
EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/preview/preview.json'


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scene.lua').write_text('ac.var.counter=1', encoding='utf-8')

    def manifest(self, **kwargs):
        path = self.root / 'preview.json'
        path.write_text(json.dumps(dict(version=1, script='scene.lua', **kwargs)), encoding='utf-8')
        return path

    def test_freezes_source_and_assets(self):
        (self.root / 'art.png').write_bytes(b'png-test')
        bundle = load_bundle(self.manifest(assets={'$art': 'art.png'}))
        (self.root / 'art.png').write_bytes(b'changed')
        self.assertEqual(bundle['media']['$art'][0], b'png-test')
        self.assertEqual(len(bundle['script_sha256']), 64)

    def test_rejects_paths_outside_manifest_and_active_content(self):
        (self.root / 'active.svg').write_text('<svg/>')
        for file in [str(self.root / 'scene.lua'), 'active.svg']:
            with self.assertRaises(BridgeError):
                load_bundle(self.manifest(assets={'$art': file}))
        sub = self.root / 'sub'
        sub.mkdir()
        path = sub / 'preview.json'
        path.write_text(json.dumps(dict(version=1, script='../scene.lua')))
        with self.assertRaises(BridgeError):
            load_bundle(path)

    def test_bad_dimensions_and_variables(self):
        for config in [dict(width=True), dict(height=10000), dict(variables={'x': []})]:
            with self.assertRaises(BridgeError):
                load_bundle(self.manifest(**config))

    def test_session_ids_cannot_escape_registry(self):
        with self.assertRaises(BridgeError):
            PreviewService(self.root).state('../preview')


@unittest.skipUnless(HAS_LUA, 'optional preview dependency not installed')
class RuntimeTests(unittest.TestCase):
    def game(self, code=None):
        bundle = load_bundle(EXAMPLE)
        if code is not None:
            bundle['source'] = code
        return PreviewGame(bundle)

    def test_actual_callbacks_and_stale_click_rejected(self):
        game = self.game()
        self.assertFalse(game.snapshot()['variables']['has_key'])
        self.assertIn('需要', game.click('open_door', 1)['objects']['message']['content'])
        self.assertTrue(game.click('take_key', 2)['variables']['has_key'])
        with self.assertRaises(BridgeError):
            game.click('open_door', 2)
        self.assertIn('门打开了', game.click('open_door', 3)['objects']['message']['content'])

    def test_no_python_io_or_dynamic_load_in_game_environment(self):
        self.game('assert(python==nil and os==nil and io==nil and require==nil and debug==nil and load==nil and package==nil)')

    def test_loop_budget_even_inside_pcall(self):
        for source in ['while true do end', 'while true do pcall(function()while true do end end)end']:
            with self.assertRaisesRegex(BridgeError, 'instruction limit'):
                self.game(source)

    def test_unsupported_native_api_fails_and_showui_reports_only(self):
        with self.assertRaisesRegex(BridgeError, 'Unsupported ac command'):
            self.game('ac.purchase({})')
        game = self.game("ac.showUI({name='save',ui_id='save',close_cb=function()ac.var.unexpected=true end})")
        state = game.snapshot()
        self.assertEqual(state['requests'][0]['ui_id'], 'save')
        self.assertNotIn('unexpected', state['variables'])
        self.assertFalse(state['native_runtime'])

    def test_fault_prevents_further_clicks(self):
        game = self.game("local function f()error('boom')end; ac.createButton({name='bad',click_func=f})")
        with self.assertRaises(BridgeError):
            game.click('bad', 1)
        self.assertIn('boom', game.snapshot()['fault'])
        with self.assertRaisesRegex(BridgeError, 'reset'):
            game.click('bad', 1)

    def test_hidden_parent_cannot_be_clicked(self):
        game = self.game("ac.createLayer({name='hidden',visible=false});local function f()ac.var.x=1 end;ac.createButton({name='b',inlayer='hidden',click_func=f})")
        with self.assertRaises(BridgeError):
            game.click('b', 1)
        self.assertNotIn('x', game.snapshot()['variables'])


@unittest.skipUnless(HAS_LUA, 'optional preview dependency not installed')
class LifecycleTests(unittest.TestCase):
    def test_standalone_cli_start_survives_command_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PEGASUS_PREVIEW_DIR=directory)
            result = subprocess.run([sys.executable, '-m', 'pegasus_bridge.cli', 'preview-start', str(EXAMPLE)],
                                    env=env, capture_output=True, encoding='utf-8', timeout=15, check=True)
            started = json.loads(result.stdout)
            service = PreviewService(directory)
            try:
                self.assertEqual(service.state(started['session_id'])['revision'], 1)
            finally:
                service.stop(started['session_id'])

    def test_cli_independent_service_lifecycle_and_security(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PreviewService(directory)
            started = service.start(str(EXAMPLE))
            session = started['session_id']
            self.addCleanup(self._stop, service, session)
            other_client = PreviewService(directory)
            self.assertTrue(other_client.click(session, 'take_key', 1)['variables']['has_key'])
            with self.assertRaises(BridgeError):
                service.click(session, 'open_door', 1)
            reset = service.reset(session, 2)
            self.assertEqual(reset['revision'], 3)
            self.assertFalse(reset['variables']['has_key'])
            with httpx.Client(trust_env=False) as client:
                self.assertEqual(client.get(started['url']).status_code, 200)
                self.assertEqual(client.get(started['url'] + 'state', headers={'Host': 'evil.test'}).status_code, 403)
                self.assertEqual(client.post(started['url'] + 'stop', json={}, headers={'Origin': 'https://evil.test'}).status_code, 403)
                self.assertEqual(client.get(started['url'] + '../state').status_code, 404)
                self.assertEqual(client.post(started['url'] + 'click', content='x' * 4097).status_code, 400)
            self.assertTrue(service.stop(session)['stopped'])
            for _ in range(30):
                if not service.descriptor(session).exists():
                    break
                time.sleep(.05)
            self.assertFalse(service.descriptor(session).exists())
            with self.assertRaises(BridgeError):
                service.state(session)

    @staticmethod
    def _stop(service, session):
        try:
            service.stop(session)
        except BridgeError:
            pass

    def test_start_failure_leaves_no_running_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bad.lua').write_text('while true do end')
            (root / 'preview.json').write_text(json.dumps({'version': 1, 'script': 'bad.lua'}))
            service = PreviewService(root / 'sessions')
            with self.assertRaisesRegex(BridgeError, 'instruction limit'):
                service.start(str(root / 'preview.json'))
            self.assertEqual(list((root / 'sessions').glob('*')), [])


if __name__ == '__main__':
    unittest.main()
