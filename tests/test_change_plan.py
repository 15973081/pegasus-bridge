import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from pegasus_bridge.change_plan import ChangePlanService
from pegasus_bridge.native_qa import NativeQAService, CHECKS
from pegasus_bridge.core import BridgeError
from pegasus_bridge.workfiles import read_json


class ChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bridge = Mock()
        self.service = ChangePlanService(self.bridge)
        self.tree = dict(gameId=42, sceneId=1, nodes=[dict(name='start', video_id='1', title_text='Start', story_res_id='$1')])
        self.values = {('scene',1):'return 1', ('ui','cover'):{'title':'old'}, ('story_tree',1):self.tree}
        self.initial = copy.deepcopy(self.values)
        def read(resource, gid, sid):
            if resource == 'commands': value = {'command':self.values['scene',sid]}
            elif resource == 'ui_template': value = {'template':json.dumps({'cover':self.values['ui','cover']})}
            else: value = self.values['story_tree',sid]
            return {'response':{'data':copy.deepcopy(value)}}
        self.bridge.read.side_effect = read
        self.writes = []
        self.lose_reply = False
        def save(kind, key, value, expected):
            self.assertEqual(self.service.hash({'kind':kind}, self.values[kind,key]), expected)
            self.values[kind,key] = copy.deepcopy(value)
            self.writes.append((kind,key))
            if self.lose_reply:
                self.lose_reply = False
                raise BridgeError('lost reply')
            return {'verified':True}
        self.service.scene.apply_scene = Mock(side_effect=lambda gid,sid,path,sha,backup,**kw:save('scene',sid,Path(path).read_text(encoding='utf-8'),sha))
        self.service.ui.apply_template = Mock(side_effect=lambda gid,key,value,sha,backup:save('ui',key,value,sha))
        self.service.tree.apply = Mock(side_effect=lambda gid,key,value,sha,backup:save('story_tree',key,value,sha))

    def write(self, name, value):
        file = self.root/name
        file.write_text(value if isinstance(value,str) else json.dumps(value),encoding='utf-8')
        return file

    def plan(self):
        self.write('scene.lua','return 2')
        self.write('ui.json',{'title':'new'})
        tree = copy.deepcopy(self.tree);tree['nodes'][0]['title_text'] = 'New'
        self.write('tree.json',tree)
        manifest = self.write('input.json',{'game_id':42,'items':[
            {'kind':'scene','scene_id':1,'path':'scene.lua'},
            {'kind':'ui','section':'cover','path':'ui.json'},
            {'kind':'story_tree','scene_id':1,'path':'tree.json'}]})
        return self.service.plan(manifest,self.root/'plan')['plan_path']

    def test_mixed_apply_and_reverse(self):
        plan = self.plan()
        self.assertEqual(self.writes,[])
        self.assertTrue(self.service.apply(plan)['complete'])
        self.assertEqual(len(self.writes),3)
        self.assertTrue(self.service.apply(plan)['complete'])
        self.assertEqual(len(self.writes),3)
        reverse = self.service.reverse_plan(plan,self.root/'reverse')['plan_path']
        self.assertTrue(self.service.apply(reverse)['complete'])
        self.assertEqual(self.values,self.initial)

    def test_lost_reply_reconciles_without_duplicate(self):
        plan = self.plan();self.lose_reply = True
        self.assertEqual(self.service.apply(plan)['status'],'needs_reconcile')
        self.assertTrue(self.service.apply(plan)['complete'])
        self.assertEqual(len(self.writes),3)

    def test_last_resource_conflict_blocks_all_writes(self):
        plan = self.plan();self.values['story_tree',1]['nodes'][0]['title_text'] = 'Concurrent'
        self.assertEqual(self.service.apply(plan)['status'],'conflict')
        self.assertEqual(self.writes,[])

    def test_reverse_preserves_concurrent_edit(self):
        plan = self.plan();self.service.apply(plan)
        reverse = self.service.reverse_plan(plan,self.root/'reverse')['plan_path']
        self.values['ui','cover'] = {'title':'external'}
        self.assertEqual(self.service.apply(reverse)['status'],'conflict')
        self.assertEqual(len(self.writes),3)

    def test_tamper_rejected_before_reads(self):
        plan = self.plan();value = read_json(plan);value['items'][0]['target']='return 9'
        Path(plan).write_text(json.dumps(value),encoding='utf-8');self.bridge.reset_mock()
        with self.assertRaises(BridgeError):self.service.apply(plan)
        self.bridge.read.assert_not_called()

    def test_frozen_source_and_lock(self):
        plan = self.plan();self.write('scene.lua','return 100')
        lock = self.root/'plan/apply.lock';lock.write_text('busy')
        with self.assertRaises(BridgeError):self.service.apply(plan)
        lock.unlink()
        self.service.apply(plan);self.assertEqual(self.values['scene',1],'return 2')

    def test_escape_rejected_without_reads(self):
        file = self.write('input.json',{'game_id':42,'items':[{'kind':'scene','scene_id':1,'path':'../escape.lua'}]})
        with self.assertRaises(BridgeError):self.service.plan(file,self.root/'bad')
        self.bridge.read.assert_not_called()

    def test_partial_apply_reverse_skips_untouched_resources(self):
        plan = self.plan();self.lose_reply = True;self.service.apply(plan)
        reverse = self.service.reverse_plan(plan,self.root/'reverse')['plan_path']
        self.assertTrue(self.service.apply(reverse)['complete'])
        self.assertEqual(self.values,self.initial)

    def test_empty_tree_reverse_refused(self):
        self.values['story_tree',1] = None
        plan = self.plan()
        with self.assertRaises(BridgeError):self.service.reverse_plan(plan,self.root/'reverse')
        self.assertFalse((self.root/'reverse').exists())


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = NativeQAService(Mock())
        self.service.editor = Mock()
        self.service.editor.state.return_value = {'observation':{'cursor':None},'running':True}
        self.service.editor.game_state.return_value = {'complete':True,'resources':{'game':{'sha256':'a'}}}
        self.session = self.service.begin(42,self.root/'qa')

    def observations(self, status='passed'):
        path = Path(self.session['observations_path'])
        value = read_json(path);value['reviewer']='tester'
        (path.parent/'evidence.txt').write_text('Native manual observation',encoding='utf-8')
        for check in value['checks'].values():check.update(status=status,note='Observed',evidence=['evidence.txt'])
        path.write_text(json.dumps(value),encoding='utf-8')
        return path

    def report(self, path):
        return self.service.report(self.session['session_path'],path,self.root/'report.json')

    def test_manual_success_never_automated(self):
        result = self.report(self.observations())
        self.assertEqual(result['status'],'manual_passed')
        self.assertFalse(result['automated_gameplay_verified'])
        self.assertTrue(result['evidence']['evidence.txt']['sha256'])

    def test_not_tested_is_incomplete(self):
        self.assertEqual(self.report(self.observations('not_tested'))['status'],'incomplete')

    def test_backend_change_needs_review(self):
        path = self.observations()
        self.service.editor.game_state.return_value['resources']['game']['sha256']='b'
        self.assertEqual(self.report(path)['status'],'needs_review')

    def test_missing_evidence_and_wrong_session_rejected(self):
        path = self.observations();(path.parent/'evidence.txt').unlink()
        with self.assertRaises(BridgeError):self.report(path)
        path = self.observations();value=read_json(path);value['session_digest']='bad'
        path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(BridgeError):self.report(path)


if __name__ == '__main__':unittest.main()
