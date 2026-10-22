import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch, MagicMock

from pegasus_bridge.core import Pegasus, BridgeError, redact


class BridgeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows process enumeration")
    def test_detects_current_process(self):
        import win32api
        import win32process
        actual = win32process.GetModuleFileNameEx(win32api.GetCurrentProcess(), 0)
        processes = Pegasus(Path(actual).parent).status()["processes"]
        self.assertIn(os.getpid(), [p["pid"] for p in processes])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bridge = Pegasus(self.root, self.root)
        (self.root / "log.txt").write_text(
            '[10-22 16:00:00] [NetworkManager][Request] method=GET headerToken=PRIVATE url=https://avg.163.com/avg-editor-api/command-line/game/42/scene/7/command?csrf_token=CSRF\n'
            '[10-22 16:00:01] hello\n', encoding="utf-8")

    def test_redaction(self):
        result = json.dumps(redact({"token": "SECRET", "nested": [{"password": "PWD"}], "log": 'headerToken=PRIVATE url=x?csrf_token=CSRF&x=1'}))
        for secret in ("SECRET", "PWD", "PRIVATE", "CSRF"):
            self.assertNotIn(secret, result)

    def test_logs_and_routes_do_not_leak_credentials(self):
        for value in (self.bridge.logs(), self.bridge.routes(), self.bridge.recent_games()):
            self.assertNotIn("PRIVATE", json.dumps(value))
            self.assertNotIn("CSRF", json.dumps(value))
        self.assertEqual(self.bridge.recent_games()["games"][0]["scene_ids"], [7])

    def test_invalid_ids_and_resource_fail_before_auth(self):
        for resource, gid, sid in (("game", -1, None), ("commands", 42, None), ("../../x", 42, None)):
            with self.assertRaises(BridgeError):
                self.bridge.read(resource, gid, sid)

    def test_export_preserves_existing_file(self):
        (self.root / "Categories[0]_42.json").write_text('{"name":"分类"}', encoding="utf-8")
        output = self.root / "snapshot.json"
        self.bridge.export("catalog", 42, str(output))
        original = output.read_bytes()
        with self.assertRaises(BridgeError):
            self.bridge.export("catalog", 42, str(output))
        self.assertEqual(original, output.read_bytes())

    def test_auth_required(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(BridgeError):
            self.bridge.read("game", 42)

    def test_log_auth_is_explicit(self):
        with patch.dict(os.environ, {"PEGASUS_ALLOW_LOG_AUTH": "1"}, clear=True):
            headers, params = self.bridge._auth()
            self.assertEqual(headers["Cookie"], "TOKEN=CSRF")
            self.assertEqual(params["csrf_token"], "CSRF")

    def test_portal_token_uses_cookie_and_csrf(self):
        with patch.dict(os.environ, {"PEGASUS_TOKEN": "portal-value"}, clear=True):
            headers, params = self.bridge._auth()
            self.assertEqual(headers["Cookie"], "TOKEN=portal-value")
            self.assertEqual(params, {"csrf_token": "portal-value"})
            self.assertNotIn("token", headers)

    def test_explicit_cookie_is_preserved(self):
        with patch.dict(os.environ, {"PEGASUS_COOKIE": "TOKEN=explicit", "PEGASUS_ALLOW_LOG_AUTH": "1"}, clear=True):
            headers, _ = self.bridge._auth()
            self.assertEqual(headers["Cookie"], "TOKEN=explicit")

    def test_http_success_and_application_error(self):
        with patch.dict(os.environ, {"PEGASUS_TOKEN": "PRIVATE"}, clear=True), patch("pegasus_bridge.core.httpx.Client") as client:
            response = MagicMock(status_code=200)
            client.return_value.__enter__.return_value.get.return_value = response
            response.json.return_value = {"state": {"code": 200000}, "data": {"name": "作品", "token": "PRIVATE"}}
            self.assertNotIn("PRIVATE", json.dumps(self.bridge.read("game", 42)))
            client.assert_called_once_with(timeout=20, follow_redirects=False)
            response.json.return_value = {"state": {"code": 502001}}
            with self.assertRaises(BridgeError):
                self.bridge.export("game", 42, str(self.root / "failed.json"))
            self.assertFalse((self.root / "failed.json").exists())
            response.status_code = 302
            with self.assertRaises(BridgeError):
                self.bridge.read("game", 42)


if __name__ == "__main__":
    unittest.main()
