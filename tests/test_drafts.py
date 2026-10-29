import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

from pegasus_bridge.core import BridgeError
from pegasus_bridge.drafts import DraftService


def row(ident=9, name='原有备份', game_id=42):
    return dict(id=ident, gameId=game_id, backupName=name, mode=8,
                isAuto=0, createTime=1793251170000)


def ok(value):
    return httpx.Response(200, json={'state': {'code': 200000}, 'data': value})


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cp = Path(self.temp.name) / 'draft.json'
        self.rows = [row()]
        self.calls = []
        self.bridge = Mock()
        self.bridge._auth.return_value = ({'Cookie': 'TOKEN=private'}, {'csrf_token': 'private'})

    def service(self, handler=None):
        def request(req):
            self.calls.append(req.method)
            self.assertEqual(req.url.host, 'avg.163.com')
            self.assertEqual(req.url.path, '/avg-editor-api/game/42/backup')
            self.assertEqual(req.headers['cookie'], 'TOKEN=private')
            if handler:
                return handler(req)
            if req.method == 'GET':
                return ok(self.rows)
            payload = json.loads(req.content)
            self.assertEqual(payload, dict(id=0, gameId=0, mode=0,
                                          backupName='测试草稿', isAuto=0, createTime=0))
            self.assertEqual(json.loads(self.cp.read_text(encoding='utf-8'))['status'], 'requesting')
            self.rows.append(row(10, '测试草稿'))
            return ok(self.rows[-1])
        return DraftService(self.bridge, httpx.MockTransport(request))

    def save(self, service=None):
        return (service or self.service()).save_draft(42, '测试草稿', str(self.cp))

    def test_list_native_metadata_whitelist(self):
        self.rows[0]['token'] = 'never-return'
        result = self.service().list_drafts(42)
        self.assertEqual(result['count'], 1)
        self.assertNotIn('token', result['drafts'][0])
        self.assertEqual(len(result['sha256']), 64)
        self.assertEqual(self.calls, ['GET'])

    def test_create_protocol_and_verified_reuse(self):
        service = self.service()
        result = self.save(service)
        self.assertTrue(result['complete'])
        self.assertEqual(result['draft']['id'], 10)
        self.assertFalse(result['editor_buffer_flushed'])
        self.assertFalse(result['publish_requested'])
        self.assertFalse(result['native_ui_observed'])
        self.assertTrue(self.save(service)['reused'])
        self.assertEqual(self.calls.count('POST'), 1)
        self.assertNotIn('private', self.cp.read_text(encoding='utf-8'))

    def test_response_loss_reconciles_without_another_post(self):
        def handler(req):
            if req.method == 'GET': return ok(self.rows)
            self.rows.append(row(10, '测试草稿'))
            raise httpx.ReadTimeout('https://example.com?csrf_token=private')
        service = self.service(handler)
        self.assertTrue(self.save(service)['verified'])
        self.assertTrue(self.save(service)['verified'])
        self.assertEqual(self.calls.count('POST'), 1)

    def test_uncertain_request_never_reposted_and_can_later_reconcile(self):
        def handler(req):
            if req.method == 'GET': return ok(self.rows)
            raise httpx.ReadTimeout('secret')
        service = self.service(handler)
        self.assertFalse(self.save(service)['complete'])
        self.assertFalse(self.save(service)['complete'])
        self.rows.append(row(10, '测试草稿'))
        self.assertTrue(self.save(service)['complete'])
        self.assertEqual(self.calls.count('POST'), 1)

    def test_preexisting_name_refuses_creation(self):
        self.rows.append(row(10, '测试草稿'))
        with self.assertRaisesRegex(BridgeError, '同名'): self.save()
        self.assertNotIn('POST', self.calls)
        self.assertFalse(self.cp.exists())

    def test_invalid_name_checked_before_authentication(self):
        for name in ('', ' ', ' trailing ', 'x\ny', 'x'*101, '\x00'):
            with self.subTest(name=name), self.assertRaises(BridgeError):
                self.service().save_draft(42, name, str(self.cp))
        self.bridge._auth.assert_not_called()

    def test_wrong_game_list_rejected(self):
        self.rows[0]['gameId'] = 43
        with self.assertRaises(BridgeError): self.save()
        self.assertNotIn('POST', self.calls)

    def test_duplicate_ids_and_invalid_metadata_rejected(self):
        for rows in ([row(), row()], [dict(row(), createTime=True)], [dict(row(), id=0)], {}):
            self.rows = rows
            with self.subTest(rows=rows), self.assertRaises(BridgeError):
                self.service().list_drafts(42)

    def test_checkpoint_identity_mismatch_before_authentication(self):
        self.cp.write_text(json.dumps(dict(version=1, game_id=99, name='测试草稿',
                                          status='requesting', before_ids=[])), encoding='utf-8')
        with self.assertRaises(BridgeError): self.save()
        self.bridge._auth.assert_not_called()

    def test_lock_prevents_parallel_request(self):
        self.cp.with_suffix('.json.lock').write_text('1')
        with self.assertRaisesRegex(BridgeError, '锁'): self.save()
        self.bridge._auth.assert_not_called()

    def test_checkpoint_failure_prevents_post(self):
        with patch('pegasus_bridge.drafts.new_text', side_effect=OSError('full')):
            with self.assertRaises(BridgeError): self.save()
        self.assertNotIn('POST', self.calls)

    def test_failed_readback_preserves_checkpoint(self):
        def handler(req):
            if req.method == 'POST':
                self.rows.append(row(10, '测试草稿')); return ok(self.rows[-1])
            if 'POST' in self.calls: raise httpx.ReadTimeout('private')
            return ok(self.rows)
        result = self.save(self.service(handler))
        self.assertFalse(result['complete'])
        self.assertNotIn('private', json.dumps(result))
        self.assertTrue(self.save()['complete'])
        self.assertEqual(self.calls.count('POST'), 1)

    def test_missing_verified_backup_does_not_create_replacement(self):
        self.save()
        self.rows = [row()]
        self.assertFalse(self.save()['verified'])
        self.assertEqual(self.calls.count('POST'), 1)

    def test_ambiguous_new_names_not_claimed_as_success(self):
        def handler(req):
            if req.method == 'GET': return ok(self.rows)
            self.rows.extend([row(10, '测试草稿'), row(11, '测试草稿')])
            raise httpx.ReadTimeout('lost')
        self.assertFalse(self.save(self.service(handler))['verified'])

    def test_response_id_must_match_readback(self):
        def handler(req):
            if req.method == 'GET': return ok(self.rows)
            self.rows.append(row(10, '测试草稿'))
            return ok(row(11, '测试草稿'))
        self.assertFalse(self.save(self.service(handler))['verified'])

    def test_backend_rejection_not_retried(self):
        def handler(req):
            if req.method == 'GET': return ok(self.rows)
            return httpx.Response(200, json={'state': {'code': 502001, 'message': 'private'}})
        service = self.service(handler)
        self.assertFalse(self.save(service)['complete'])
        self.assertFalse(self.save(service)['complete'])
        self.assertEqual(self.calls.count('POST'), 1)


if __name__ == '__main__':
    unittest.main()
