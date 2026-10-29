import argparse
import json
import unittest
from unittest.mock import Mock, patch

import httpx

from pegasus_bridge.core import BridgeError
from pegasus_bridge.games import GameDiscoveryService
from pegasus_bridge import workflow_cli


def row(gid, name='测试作品'):
    return dict(id=gid, gameName=name, mode=8, orientationMode=1, status=1,
                statusName='创作中', createTime=100, authorId='private', token='private')


def ok(data):
    return httpx.Response(200, json={'state': {'code': 200000}, 'data': data})


class GameDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Mock()
        self.bridge._auth.return_value = ({'Cookie': 'TOKEN=private'}, {'csrf_token': 'private'})
        self.bridge.recent_games.side_effect = AssertionError('Must not read history')
        self.calls = []
        self.rows = [row(1, '病房'), row(2, '视频测试'), row(3, '病房（竖版）'),
                     row(4, '病房'), row(5, 'Final')]

    def service(self, handler=None):
        def request(req):
            self.calls.append(req)
            self.assertEqual(req.method, 'GET')
            self.assertEqual(req.url.host, 'avg.163.com')
            self.assertEqual(req.url.path, '/avg-editor-api/game')
            self.assertEqual(req.url.params['sourceType'], '5')
            self.assertEqual(req.headers['cookie'], 'TOKEN=private')
            if handler:
                return handler(req)
            start, size = int(req.url.params['offset']), int(req.url.params['limit'])
            return ok({'list': self.rows[start:start+size], 'totalSize': len(self.rows)})
        return GameDiscoveryService(self.bridge, httpx.MockTransport(request))

    def test_paginate_all_and_whitelist_metadata(self):
        result = self.service().list_games(page_size=2)
        self.assertTrue(result['complete'])
        self.assertEqual(result['count'], 5)
        self.assertEqual(result['pages'], 3)
        self.assertEqual([r.url.params['offset'] for r in self.calls], ['0', '2', '4'])
        self.assertIsNone(result['next_offset'])
        self.assertEqual(result['games'][0]['orientation_mode'], 1)
        self.assertNotIn('private', json.dumps(result))
        self.bridge.recent_games.assert_not_called()

    def test_filter_scans_past_pages_with_no_matches(self):
        result = self.service().list_games('final', page_size=2)
        self.assertEqual([g['game_id'] for g in result['games']], [5])
        self.assertEqual(result['scanned_count'], 5)
        self.assertTrue(result['complete'])
        self.assertNotIn('gameName', self.calls[0].url.params)

    def test_unique_name_and_id_resolution(self):
        for selector in ({'name': '视频测试'}, {'game_id': 2}):
            with self.subTest(selector=selector):
                result = self.service().resolve_game(**selector, page_size=2)
                self.assertEqual(result['status'], 'resolved')
                self.assertEqual(result['game']['game_id'], 2)
                self.assertEqual(result['count'], 1)

    def test_same_name_across_pages_is_ambiguous(self):
        result = self.service().resolve_game(name='病房', page_size=2)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual([g['game_id'] for g in result['candidates']], [1, 4])
        self.assertIsNone(result['game'])

    def test_contains_is_opt_in_and_does_not_pick_first(self):
        result = self.service().resolve_game(name='病房', exact=False, page_size=2)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(result['count'], 3)
        self.assertIsNone(result['game'])

    def test_not_found_only_after_complete_scan(self):
        result = self.service().resolve_game(game_id=999, page_size=2)
        self.assertEqual(result['status'], 'not_found')
        self.assertTrue(result['complete'])
        self.assertIsNone(result['game'])

    def test_truncated_scan_never_resolves_or_reports_not_found(self):
        for name in ('视频测试', '病房', '不存在'):
            with self.subTest(name=name):
                result = self.service().resolve_game(name=name, page_size=2, max_pages=1)
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['next_offset'], 2)
                self.assertEqual(result['reason'], 'scan_limit')
                self.assertIsNone(result['game'])

    def test_continuation_marks_partial_range(self):
        result = self.service().list_games(offset=2, page_size=2)
        self.assertTrue(result['reached_end'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['reason'], 'partial_range')
        self.assertEqual(result['count'], 3)

    def test_legacy_array_requires_empty_page_after_exact_multiple(self):
        self.rows = self.rows[:4]
        def handler(req):
            start = int(req.url.params['offset'])
            return ok(self.rows[start:start+2])
        result = self.service(handler).list_games(page_size=2)
        self.assertTrue(result['complete'])
        self.assertEqual(result['pages'], 3)
        self.assertIsNone(result['total'])

    def test_short_page_with_total_does_not_stop_early(self):
        def handler(req):
            start = int(req.url.params['offset'])
            return ok({'list': self.rows[start:start+1], 'totalSize': 5})
        result = self.service(handler).list_games(page_size=100)
        self.assertTrue(result['complete'])
        self.assertEqual(result['pages'], 5)

    def test_repeated_pages_stop_without_claiming_completion(self):
        result = self.service(lambda _: ok(self.rows[:2])).resolve_game(name='视频测试', page_size=2)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['reason'], 'repeated_page')
        self.assertTrue(result['restart_required'])
        self.assertIsNone(result['next_offset'])
        self.assertEqual(len(self.calls), 2)

    def test_changed_total_or_overlapping_page_requires_restart(self):
        for second in ({'list': self.rows[2:], 'totalSize': 6},
                       {'list': self.rows[1:4], 'totalSize': 5}):
            with self.subTest(second=second):
                def handler(req):
                    return ok({'list': self.rows[:3], 'totalSize': 5}
                              if req.url.params['offset'] == '0' else second)
                result = self.service(handler).resolve_game(name='视频测试', page_size=3)
                self.assertEqual(result['status'], 'incomplete')
                self.assertTrue(result['restart_required'])
                self.assertIsNone(result['game'])

    def test_empty_page_before_total_is_incomplete(self):
        result = self.service(lambda _: ok({'list': [], 'totalSize': 5})).list_games()
        self.assertFalse(result['complete'])
        self.assertTrue(result['restart_required'])

    def test_empty_account_is_valid(self):
        self.rows = []
        result = self.service().resolve_game(name='不存在')
        self.assertEqual(result['status'], 'not_found')
        self.assertTrue(result['complete'])

    def test_invalid_parameters_do_not_authenticate(self):
        for args in ({'offset': -1}, {'page_size': 0}, {'page_size': 101}, {'max_pages': 0},
                     {'max_pages': 101}, {'offset': True}, {'query': ''}, {'query': ' '},
                     {'query': 'x\ny'}, {'query': 'x'*201}, {'match_mode': 'bad'}):
            with self.subTest(args=args), self.assertRaises(BridgeError):
                self.service().list_games(**args)
        for args in ({}, {'name': 'x', 'game_id': 1}, {'game_id': -1}, {'game_id': True},
                     {'name': 'x', 'exact': 'yes'}):
            with self.subTest(args=args), self.assertRaises(BridgeError):
                self.service().resolve_game(**args)
        self.bridge._auth.assert_not_called()

    def test_missing_auth_is_not_empty_list(self):
        self.bridge._auth.side_effect = BridgeError('private credential')
        with self.assertRaisesRegex(BridgeError, 'AUTH_REQUIRED') as cm:
            self.service().list_games()
        self.assertNotIn('private', str(cm.exception))
        self.assertFalse(self.calls)

    def test_api_failures_are_errors_not_absence_and_do_not_leak(self):
        for response, prefix in [(httpx.Response(401), 'AUTH_REQUIRED'),
                                 (httpx.Response(403), 'ACCESS_DENIED'),
                                 (httpx.Response(500), 'API_HTTP_ERROR'),
                                 (httpx.Response(302, headers={'location': 'https://example.org'}), 'API_HTTP_ERROR'),
                                 (httpx.Response(200, json={'state': {'code': 502001, 'msg': 'private'}}), 'API_REJECTED')]:
            with self.subTest(prefix=prefix), self.assertRaisesRegex(BridgeError, prefix) as cm:
                self.service(lambda _: response).resolve_game(name='x')
            self.assertNotIn('private', str(cm.exception))

    def test_malformed_response_rejected(self):
        for data in (None, {}, {'list': 'bad'}, {'list': [row(1)], 'totalSize': True},
                     [row(True)], [{'id': 1}], [row(1), row(2)]):
            with self.subTest(data=data), self.assertRaisesRegex(BridgeError, 'INVALID_RESPONSE'):
                self.service(lambda _: ok(data)).list_games(page_size=1)
        for payload in ([], {'state': []}, {'state': {'code': 'private'}}):
            with self.subTest(payload=payload), self.assertRaises(BridgeError) as cm:
                self.service(lambda _: httpx.Response(200, json=payload)).list_games()
            self.assertNotIn('private', str(cm.exception))

    def test_network_error_after_first_page_never_returns_partial_success(self):
        def handler(req):
            if req.url.params['offset'] == '0':
                return ok({'list': self.rows[:2], 'totalSize': 5})
            raise httpx.ReadTimeout('https://avg.163.com?csrf_token=private')
        with self.assertRaisesRegex(BridgeError, 'NETWORK_ERROR') as cm:
            self.service(handler).resolve_game(name='视频测试', page_size=2)
        self.assertNotIn('private', str(cm.exception))

    def test_cli_dispatch_uses_shared_service(self):
        parser = argparse.ArgumentParser()
        workflow_cli.add_arguments(parser.add_subparsers(dest='command'))
        with patch.object(workflow_cli, 'GameDiscoveryService', return_value=self.service()):
            listing = workflow_cli.run(parser.parse_args(['list-games', '--page-size', '2']), self.bridge)
            resolved = workflow_cli.run(parser.parse_args(['resolve-game', '--game-id', '2']), self.bridge)
            ambiguous = workflow_cli.run(parser.parse_args(['resolve-game', '--name', '病房', '--contains']), self.bridge)
        self.assertTrue(listing['complete'])
        self.assertEqual(resolved['game']['game_id'], 2)
        self.assertEqual(ambiguous['count'], 3)


if __name__ == '__main__':
    unittest.main()
