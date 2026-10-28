import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pegasus_bridge.archives import ArchiveService, SNAPSHOT_FIELDS
from pegasus_bridge.core import BridgeError, Pegasus


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = ArchiveService(Pegasus(data=self.root))
        self.folder = self.root / 'ArchiveFiles/42/Editor/Local/archive_1'
        self.folder.mkdir(parents=True)
        self.header = dict(id=0, userId=0, gameId=0, position=1, version=2,
                           isLocal=True, isAuto=0, profileName='测试档', createTime=1000,
                           modifyTime=2000, progress='C:/private/secret.json',
                           screenshot='https://example.invalid/private?token=PRIVATE')
        self.progress = dict(scene_id=7, display_start_scene_id=0, cmd_executes_times=2,
                             is_ui_open=False, cmd_exe_list=['SECRET LUA'],
                             event_record_infos=[{'callbackArgs': ['PRIVATE']}],
                             video_end_list=[], option_indexs=[1], random_values=[],
                             script_start_snapshot={k: {} for k in SNAPSHOT_FIELDS},
                             save_snapshot={k: {} for k in SNAPSHOT_FIELDS})
        self.write()

    def write(self, folder=None):
        folder = folder or self.folder
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'main_archive.json').write_text(json.dumps(self.header), encoding='utf-8')
        (folder / 'progress.json').write_text(json.dumps(self.progress), encoding='utf-8')

    def test_read_whitelisted_empty_snapshot_without_executing_or_following_paths(self):
        report = self.service.read_archive(42, 1)
        self.assertEqual(report['snapshot_state'], 'empty')
        self.assertFalse(report['live'])
        self.assertEqual(report['identity_scope'], 'legacy_unbound')
        self.assertEqual(report['progress']['scene_id'], 7)
        self.assertEqual(report['record_counts']['event_record_infos'], 1)
        rendered = json.dumps(report)
        for private in ('PRIVATE', 'SECRET LUA', 'secret.json', 'example.invalid'):
            self.assertNotIn(private, rendered)

    def test_list_and_read_missing_are_distinct_from_empty(self):
        self.assertEqual(self.service.list_archives(99)['status'], 'missing')
        empty = self.root / 'ArchiveFiles/99/Editor/Local'
        empty.mkdir(parents=True)
        self.assertEqual(self.service.list_archives(99)['status'], 'empty')
        with self.assertRaisesRegex(BridgeError, '缺失'):
            self.service.read_archive(99, 1)
        report = self.service.list_archives(42)
        self.assertEqual(report['archives'][0]['status'], 'present')
        self.assertFalse(report['archives'][0]['progress_validated'])

    def test_incomplete_header_and_corrupt_header_are_distinct(self):
        path = self.folder / 'main_archive.json'
        path.unlink()
        entry = self.service.list_archives(42)['archives'][0]
        self.assertEqual(entry['status'], 'incomplete')
        path.write_text('{broken', encoding='utf-8')
        entry = self.service.list_archives(42)['archives'][0]
        self.assertEqual(entry['status'], 'invalid')

    def test_missing_snapshot_is_not_an_empty_snapshot(self):
        self.progress['save_snapshot'] = None
        self.write()
        report = self.service.read_archive(42, 1)
        self.assertEqual(report['snapshot_state'], 'missing')
        self.assertIsNone(report['snapshots']['save_snapshot'])

    def test_return_values_redact_secret_variable_names(self):
        self.progress['save_snapshot']['normal_variables_base'] = {'hp': 12, 'accessToken': 'PRIVATE', 'note': 'password=PRIVATE'}
        self.progress['save_snapshot']['normal_variables_increment'] = {'hp': 3}
        self.progress['save_snapshot']['normal_arrays'] = {'items': ['key', 'map']}
        self.write()
        report = self.service.read_archive(42, 1)
        self.assertEqual(report['snapshot_state'], 'available')
        self.assertEqual(report['snapshots']['save_snapshot']['normal_variables_base']['hp'], 12)
        self.assertEqual(report['snapshots']['save_snapshot']['normal_variables_increment']['hp'], 3)
        self.assertNotIn('PRIVATE', json.dumps(report))

    def test_game_id_and_user_id_mismatches_rejected(self):
        for change in ({'gameId': 43}, {'userId': 77}, {'position': 2}, {'isLocal': False}, {'version': 1}):
            with self.subTest(change=change):
                original = self.header.copy()
                self.header.update(change)
                self.write()
                with self.assertRaises(BridgeError):
                    self.service.read_archive(42, 1)
                self.header = original

    def test_named_identity_never_falls_back_or_auto_selects(self):
        self.assertEqual(self.service.list_archives(42, user_id=77)['status'], 'missing')
        target = self.root / 'ArchiveFiles/42/77/Editor/Local/archive_1'
        self.header['userId'] = 77
        self.write(target)
        report = self.service.read_archive(42, 1, user_id=77)
        self.assertEqual(report['identity_scope'], 'explicit_user_directory')
        self.assertEqual(report['metadata']['userId'], 77)
        self.assertEqual(self.service.read_archive(42, 1)['metadata']['userId'], 0)
        self.assertEqual(self.service.list_archives(42, user_id=78)['status'], 'missing')

    def test_editor_runtime_and_cloud_are_separate(self):
        self.assertEqual(self.service.list_archives(42, mode='Runtime')['status'], 'missing')
        cloud = self.root / 'ArchiveFiles/42/Editor/Clouds/archive_2'
        self.write(cloud)
        self.assertEqual(len(self.service.list_archives(42)['archives']), 1)

    def test_invalid_selectors_cannot_construct_arbitrary_paths(self):
        for gid, slot, mode, uid in ((True, 1, 'Editor', None), (0, 1, 'Editor', None),
                                    (42, 0, 'Editor', None), (42, 51, 'Editor', None),
                                    (42, True, 'Editor', None), (42, 1, '../Editor', None),
                                    (42, 1, 'Editor', '../77'), (42, 1, 'Editor', True)):
            with self.subTest(args=(gid, slot, mode, uid)), self.assertRaises(BridgeError):
                self.service.read_archive(gid, slot, mode, uid)

    def test_corrupt_duplicate_and_nonfinite_json_rejected(self):
        for raw in ('[]', '{bad', '{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(raw=raw):
                (self.folder / 'progress.json').write_text(raw, encoding='utf-8')
                with self.assertRaises(BridgeError):
                    self.service.read_archive(42, 1)

    def test_invalid_snapshot_structure_and_numeric_values_rejected(self):
        original = copy.deepcopy(self.progress)
        for change in ('unknown', 'nonnumeric', 'infinite', 'bool_scene', 'missing_record', 'deep'):
            self.progress = copy.deepcopy(original)
            if change == 'unknown':
                self.progress['save_snapshot'] = {'arbitrary': 1}
            elif change == 'nonnumeric':
                self.progress['save_snapshot']['normal_variables_increment'] = {'hp': '1'}
            elif change == 'infinite':
                self.progress['save_snapshot']['normal_variables_base'] = {'hp': 1e309}
            elif change == 'bool_scene':
                self.progress['scene_id'] = True
            elif change == 'missing_record':
                del self.progress['cmd_exe_list']
            elif change == 'deep':
                value = []
                for _ in range(20):
                    value = [value]
                self.progress['save_snapshot']['normal_arrays'] = {'nested': value}
            self.write()
            with self.subTest(change=change), self.assertRaises(BridgeError):
                self.service.read_archive(42, 1)

    def test_file_size_limits(self):
        with patch('pegasus_bridge.archives.MAX_PROGRESS', 10), self.assertRaisesRegex(BridgeError, '过大'):
            self.service.read_archive(42, 1)

    def test_symlink_file_cannot_escape(self):
        target = self.root / 'outside.json'
        target.write_text('{}', encoding='utf-8')
        path = self.folder / 'progress.json'
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest('symlinks unavailable in this environment')
        with self.assertRaises(BridgeError):
            self.service.read_archive(42, 1)

    def test_directory_symlink_cannot_change_game_binding(self):
        target = self.root / 'ArchiveFiles/43'
        try:
            target.symlink_to(self.root / 'ArchiveFiles/42', target_is_directory=True)
        except OSError:
            self.skipTest('directory symlinks unavailable in this environment')
        with self.assertRaises(BridgeError):
            self.service.list_archives(43)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction')
    def test_windows_junction_cannot_change_game_binding(self):
        link = self.root / 'ArchiveFiles/43'
        target = self.root / 'ArchiveFiles/42'
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 'New-Item -ItemType Junction -Path $env:ARCHIVE_JUNCTION_TEST_LINK -Target $env:ARCHIVE_JUNCTION_TEST_TARGET | Out-Null'],
                                env=dict(os.environ, ARCHIVE_JUNCTION_TEST_LINK=str(link), ARCHIVE_JUNCTION_TEST_TARGET=str(target)),
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode:
            self.skipTest('junction creation unavailable')
        self.assertTrue(link.exists())
        with self.assertRaises(BridgeError):
            self.service.list_archives(43)

    def test_metadata_change_across_two_file_read_is_rejected(self):
        original = self.service._read_json
        def read(parts, maximum):
            value = original(parts, maximum)
            if parts[-1] == 'progress.json':
                self.header['modifyTime'] += 1
                self.write()
            return value
        with patch.object(self.service, '_read_json', side_effect=read), self.assertRaisesRegex(BridgeError, '变化'):
            self.service.read_archive(42, 1)


if __name__ == '__main__':
    unittest.main()
