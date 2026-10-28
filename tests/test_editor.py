import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from pegasus_bridge.core import BridgeError, Pegasus
from pegasus_bridge.editor import EditorService, log_events, local_sdk_uid


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bridge = Pegasus(self.root, self.root)
        self.service = EditorService(self.bridge)
        self.log = self.root / 'log.txt'
        self.log.write_text('[SchemeLaunch] RequestOpusData 成功回调: gameId=42, hasData=True\n', encoding='utf-8')

    def append(self, text):
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write(text)

    def test_baseline_does_not_report_old_success(self):
        baseline = log_events(self.log)
        self.assertEqual(baseline['events'], [])
        self.assertEqual(log_events(self.log, baseline['cursor'])['events'], [])

    def test_only_fresh_allowlisted_fields_no_secrets(self):
        baseline = log_events(self.log)
        self.append('[NetworkManager] token=PRIVATE\n[SchemeLaunch] RequestOpusData 成功回调: gameId=43, hasData=True, uid=PRIVATE\n')
        result = log_events(self.log, baseline['cursor'])
        self.assertEqual(result['events'], [{'event': 'metadata_loaded', 'game_id': 43}])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_incomplete_line_keeps_cursor(self):
        baseline = log_events(self.log)
        self.append('[SchemeLaunch] RequestOpusData 开始请求: gameId=4')
        first = log_events(self.log, baseline['cursor'])
        self.assertEqual(first['events'], [])
        self.append('2\n')
        self.assertEqual(log_events(self.log, first['cursor'])['events'][0]['game_id'], 42)

    def test_rewrite_and_rotation_do_not_report_stale_events(self):
        baseline = log_events(self.log)
        self.log.write_text('new log\n', encoding='utf-8')
        self.assertEqual(log_events(self.log, baseline['cursor'])['continuity'], 'reset')
        self.log.unlink()
        self.log.write_text('[SchemeLaunch] RequestOpusData 成功回调: gameId=99, hasData=True\n' * 10, encoding='utf-8')
        self.assertEqual(log_events(self.log, baseline['cursor'])['events'], [])

    def test_invalid_cursor_rejected(self):
        for cursor in ({}, {'offset': 0}, dict(device=1, inode=1, offset=-1, anchor='a'*64)):
            with self.assertRaises(BridgeError):
                log_events(self.log, cursor)

    def test_missing_log(self):
        self.log.unlink()
        self.assertIsNone(log_events(self.log)['cursor'])

    def test_failed_metadata_is_not_success(self):
        baseline = log_events(self.log)
        self.append('[SchemeLaunch] RequestOpusData 失败回调: gameId=42, error=PRIVATE\n')
        result = log_events(self.log, baseline['cursor'])
        self.assertEqual(result['events'][0]['event'], 'metadata_failed')
        self.assertNotIn('PRIVATE', json.dumps(result))

    @unittest.skipUnless(os.name == 'nt', 'native launcher')
    def test_launch_uses_argument_array_and_does_not_claim_open(self):
        (self.root / 'PegasusEditorLauncher.exe').touch()
        with patch.object(self.bridge, 'read') as read, patch('pegasus_bridge.editor.local_sdk_uid', return_value='private-uid'), patch('pegasus_bridge.editor.subprocess.Popen') as popen:
            popen.return_value.pid = 123
            popen.return_value.poll.return_value = 0
            result = self.service.open_game(42, 0)
            read.assert_called_once_with('game', 42)
            argv = popen.call_args.args[0]
            self.assertEqual(argv[1], 'pegasus-editor://open?gameId=42&uid=private-uid')
            self.assertNotIn('shell', popen.call_args.kwargs)
            self.assertNotIn('private-uid', json.dumps(result))
            self.assertEqual(result['progress'], 'sent_unconfirmed')
            self.assertIsNone(result['editor_ready'])

    @unittest.skipUnless(os.name == 'nt', 'native launcher')
    def test_launcher_nonzero_is_error(self):
        (self.root / 'PegasusEditorLauncher.exe').touch()
        with patch.object(self.bridge, 'read'), patch('pegasus_bridge.editor.local_sdk_uid', return_value='uid'), patch('pegasus_bridge.editor.subprocess.Popen') as popen:
            popen.return_value.poll.return_value = 4
            with self.assertRaises(BridgeError):
                self.service.open_game(42, 0)

    def test_launcher_delivery_is_distinct_from_loading(self):
        baseline = log_events(self.log, launcher=True)
        self.append('[2026-10-28 08:00:00] 已转发到运行中的编辑器\n')
        result = log_events(self.log, baseline['cursor'], launcher=True)
        self.assertEqual(result['events'], [{'event': 'forwarded_to_editor', 'game_id': None}])

    @unittest.skipUnless(os.name == 'nt', 'PlayerPrefs registry')
    def test_account_identity_uses_exact_native_key(self):
        with patch('winreg.OpenKey') as key, patch('winreg.QueryInfoKey', return_value=(0, 2, 0)), patch('winreg.EnumValue', side_effect=[('sdkUid_h12', b'wrong\0', 3), ('tianmaSdkUid_h34', b'correct\0', 3)]):
            self.assertEqual(local_sdk_uid(), 'correct')

    @unittest.skipUnless(os.name == 'nt', 'PlayerPrefs registry')
    def test_other_uid_never_substitutes_for_native_identity(self):
        with patch('winreg.OpenKey'), patch('winreg.QueryInfoKey', return_value=(0, 1, 0)), patch('winreg.EnumValue', return_value=('sdkUid_h12', b'wrong\0', 3)):
            with self.assertRaises(BridgeError):
                local_sdk_uid()

    @unittest.skipUnless(os.name == 'nt', 'native launcher')
    def test_new_load_event_still_does_not_claim_editor_ready(self):
        (self.root / 'PegasusEditorLauncher.exe').touch()
        def launch(*args, **kwargs):
            self.append('[SchemeLaunch] RequestOpusData 成功回调: gameId=42, hasData=True\n')
            return MagicMock(pid=123, poll=MagicMock(return_value=0))
        with patch.object(self.bridge, 'read'), patch('pegasus_bridge.editor.local_sdk_uid', return_value='uid'), patch('pegasus_bridge.editor.subprocess.Popen', side_effect=launch):
            result = self.service.open_game(42, 1)
        self.assertEqual(result['progress'], 'metadata_loaded')
        self.assertIsNone(result['editor_ready'])

    def test_oversized_line_does_not_stall_polling(self):
        baseline = log_events(self.log)
        self.append('x' * (1024 * 1024 + 1))
        result = log_events(self.log, baseline['cursor'])
        self.assertEqual(result['continuity'], 'oversized_line')
        self.assertEqual(result['events'], [])

    @unittest.skipUnless(os.name == 'nt', 'native launcher')
    def test_access_failure_never_launches(self):
        (self.root / 'PegasusEditorLauncher.exe').touch()
        with patch.object(self.bridge, 'read', side_effect=BridgeError('denied')), patch('pegasus_bridge.editor.subprocess.Popen') as popen:
            with self.assertRaises(BridgeError):
                self.service.open_game(42, 0)
            popen.assert_not_called()

    def test_bad_open_parameters_fail_before_launch(self):
        with patch('pegasus_bridge.editor.subprocess.Popen') as popen:
            for gid, wait in ((0, 0), (True, 0), (42, 16), (42, True), (42, -1)):
                with self.assertRaises(BridgeError):
                    self.service.open_game(gid, wait)
            popen.assert_not_called()

    def test_saved_state_partial_failure_and_scope(self):
        def read(resource, gid, sid):
            if resource == 'arrays':
                raise BridgeError('unavailable')
            return {'response': {'data': {'resource': resource}}}
        with patch.object(self.bridge, 'read', side_effect=read):
            result = self.service.game_state(42, 7, True)
        self.assertFalse(result['complete'])
        self.assertEqual(result['errors'], {'arrays': 'unavailable'})
        self.assertIsNone(result['runtime_state'])
        self.assertIn('ui_template', result['resources'])
        self.assertIn('commands', result['resources'])
        self.assertEqual(len(result['resources']['game']['sha256']), 64)

    def test_native_state_never_infers_current_game_from_old_log(self):
        with patch.object(self.bridge, 'status', return_value={'processes': [{'pid': 1, 'executable': 'Release_Windows_Tianma.exe'}]}), patch('pegasus_bridge.editor.pipe_available', return_value=True):
            result = self.service.state()
        self.assertTrue(result['running'])
        self.assertIsNone(result['current_game_id'])
        self.assertIsNone(result['gameplay_running'])
        self.assertEqual(result['observation']['events'], [])


if __name__ == '__main__':
    unittest.main()
