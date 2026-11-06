import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pegasus_bridge.core import BridgeError
from pegasus_bridge.scene import editor_session


@unittest.skipUnless(os.name == 'nt', 'Windows editor registry')
class ClientSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def registry(self, rows):
        key = Mock()
        key.__enter__ = Mock(return_value=key)
        key.__exit__ = Mock(return_value=False)
        return (patch('winreg.OpenKey', return_value=key),
                patch('winreg.QueryInfoKey', return_value=(0, len(rows), 0)),
                patch('winreg.EnumValue', side_effect=lambda _, index: rows[index]))

    def test_plain_editor_key_is_reused(self):
        a, b, c = self.registry([('CACHE_GAME_SID_42', 'editor-client', 1)])
        with a, b, c:
            self.assertEqual(editor_session(42), 'editor-client')
        self.assertFalse((self.root / 'PegasusBridge').exists())

    def test_hashed_editor_key_is_reused(self):
        a, b, c = self.registry([('CACHE_GAME_SID_42_h12345', b'editor-client\0', 3)])
        with a, b, c:
            self.assertEqual(editor_session(42), 'editor-client')

    def test_missing_editor_nonce_creates_stable_project_scoped_client(self):
        with patch('winreg.OpenKey', side_effect=FileNotFoundError):
            first = editor_session(42)
            self.assertEqual(editor_session(42), first)
            self.assertNotEqual(editor_session(43), first)
        self.assertRegex(first, r'^[A-Za-z0-9_-]{4}_[A-Za-z0-9_-]{13}_[A-Za-z0-9_-]{20}$')
        record = json.loads((self.root / 'PegasusBridge/client-sessions/42.json').read_text())
        self.assertEqual(record['game_id'], 42)
        self.assertEqual(record['owner'], 'pegasus-bridge')

    def test_opened_but_never_saved_project_needs_no_registry_sid(self):
        a, b, c = self.registry([('unrelated_preference', 'ignored', 1)])
        with a, b, c:
            self.assertEqual(editor_session(42), editor_session(42))

    def test_other_project_is_not_used_as_identity(self):
        a, b, c = self.registry([('CACHE_GAME_SID_420_h1', 'another-project', 1)])
        with a, b, c:
            self.assertNotEqual(editor_session(42), 'another-project')

    def test_invalid_owned_record_is_preserved_and_refused(self):
        path = self.root / 'PegasusBridge/client-sessions/42.json'
        path.parent.mkdir(parents=True)
        path.write_text('{"game_id":43,"sid":"invalid"}')
        before = path.read_bytes()
        with patch('winreg.OpenKey', side_effect=FileNotFoundError), self.assertRaises(BridgeError):
            editor_session(42)
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_project_id_creates_no_cache(self):
        with self.assertRaises(BridgeError):
            editor_session(-1)
        self.assertFalse((self.root / 'PegasusBridge').exists())
