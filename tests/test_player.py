from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from pegasus_bridge.core import Pegasus, BridgeError
from pegasus_bridge.player import PlayerService, validate_snapshot, MARKER


def sample(game=42):
    return dict(schema_version=1, game_id=game, scene_id=7, sequence=1, node='ward', chapter=1,
                highest_chapter=1, flags={'key': True}, endings={},
                inventory=[dict(id='key', name='病房钥匙', quantity=1, category='evidence')])


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bridge = Pegasus(self.root, self.root)
        self.service = PlayerService(self.bridge)

    def emit(self, data, age=0, prefix=True, newline=True):
        timestamp = (datetime.now() - timedelta(seconds=age)).strftime('[%m-%d %H:%M:%S] [INFO] ')
        with self.bridge.log.open('a', encoding='utf-8') as f:
            f.write((timestamp if prefix else '') + MARKER + json.dumps(data, ensure_ascii=False) + ('\n' if newline else ''))

    def test_missing_is_unknown_not_empty(self):
        result = self.service.inventory(42)
        self.assertIsNone(result['items'])
        self.assertIsNone(result['total_quantity'])
        self.assertFalse(result['is_live'])

    def test_actual_empty_inventory_is_distinct(self):
        state = sample(); state['inventory'] = []
        self.emit(state)
        result = self.service.inventory(42)
        self.assertEqual(result['items'], [])
        self.assertEqual(result['total_quantity'], 0)
        self.assertTrue(result['fresh'])

    def test_filters_other_game_and_returns_latest_matching_event(self):
        self.emit(sample())
        other = sample(43); other['inventory'][0]['quantity'] = 99
        self.emit(other)
        self.assertEqual(self.service.inventory(42)['total_quantity'], 1)

    def test_stale_event_not_refreshed_by_file_write(self):
        self.emit(sample(), age=600)
        with self.bridge.log.open('a', encoding='utf-8') as f:
            f.write('unrelated new log\n')
        result = self.service.player_state(42)
        self.assertEqual(result['status'], 'stale')
        self.assertFalse(result['fresh'])
        self.assertGreater(result['age_seconds'], 590)

    def test_no_timestamp_or_future_timestamp_not_fresh(self):
        self.emit(sample(), prefix=False)
        self.assertFalse(self.service.player_state(42)['fresh'])
        self.emit(sample(), age=-600)
        self.assertFalse(self.service.player_state(42)['fresh'])

    def test_invalid_or_partial_data_never_becomes_empty_bag(self):
        bad = sample(); bad['inventory'][0]['quantity'] = -1
        self.emit(bad)
        self.emit(sample(), newline=False)
        result = self.service.inventory(42)
        self.assertIsNone(result['items'])
        self.assertEqual(result['rejected_events'], 1)

    def test_extra_and_sensitive_fields_not_returned(self):
        state = sample(); state['token'] = 'SECRET'; state['flags']['access_token'] = True
        state['inventory'][0]['authorization'] = 'SECRET'
        self.emit(state)
        rendered = json.dumps(self.service.player_state(42))
        self.assertNotIn('SECRET', rendered)
        self.assertNotIn('access_token', rendered)

    def test_duplicate_and_bad_item_quantities_rejected(self):
        for value in (-1, True, 1.5, '1'):
            state = sample(); state['inventory'][0]['quantity'] = value
            with self.assertRaises(ValueError): validate_snapshot(state)
        state = sample(); state['inventory'].append(state['inventory'][0])
        with self.assertRaises(ValueError): validate_snapshot(state)

    def test_duplicate_json_fields_rejected(self):
        self.bridge.log.write_text(MARKER+'{"game_id":42,"game_id":43}\n', encoding='utf-8')
        self.assertEqual(self.service.player_state(42)['rejected_events'], 1)

    def test_invalid_ids_and_age(self):
        for gid, age in ((0, 30), (True, 30), (42, 0), (42, True), (42, 3601)):
            with self.assertRaises(BridgeError): self.service.player_state(gid, age)

    @unittest.skipUnless(importlib.util.find_spec('lupa'), 'optional Lua integration runtime')
    def test_lua_producer_roundtrip_with_quotes_and_empty_bag(self):
        from lupa import LuaRuntime
        runtime = LuaRuntime(unpack_returned_tuples=True)
        output = []
        runtime.globals()['print'] = output.append
        source = (Path(__file__).parents[1] / 'src/pegasus_bridge/runtime_observer.lua').read_text(encoding='utf-8')
        source += '''
local s={game_id=42,scene_id=7,node="ward",chapter=1,highest_chapter=1,flags={key=true},endings={},inventory={}}
pegasusEmitPlayerState(s)
s.inventory={{id="key",name='钥匙 "A"',quantity=2,origin="collected"}}
pegasusEmitPlayerState(s)
'''
        runtime.execute(source)
        first = validate_snapshot(json.loads(output[0][len(MARKER):]))
        second = validate_snapshot(json.loads(output[1][len(MARKER):]))
        self.assertEqual(first['inventory'], [])
        self.assertEqual(second['sequence'], 2)
        self.assertEqual(second['inventory'][0]['name'], '钥匙 "A"')
        self.assertEqual(second['inventory'][0]['quantity'], 2)


if __name__ == '__main__':
    unittest.main()
