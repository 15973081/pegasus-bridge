import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import wave
import zipfile

import httpx
from PIL import Image
from pegasus_bridge.core import BridgeError
from pegasus_bridge.validation import validate_source,validate_scene
from pegasus_bridge.authoring import AuthoringService,build_scene
from pegasus_bridge.audio import AudioService
from pegasus_bridge.batch import BatchService
from pegasus_bridge.project import project_check,project_pack
from pegasus_bridge.preview_runtime import PreviewGame,load_bundle
from pegasus_bridge.qa import test_route
from pegasus_bridge.scene import SceneService,source_hash
from pegasus_bridge.workfiles import read_json,digest


class Files(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def write(self,name,value):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else value,encoding='utf-8');return p
    def png(self):
        p=self.root/'person.png';Image.new('RGBA',(32,48),(100,80,60,128)).save(p);return p


class ValidationTests(Files):
    def test_parse_error_and_anonymous_known_crash(self):
        self.assertFalse(validate_source('function !!!')['valid'])
        self.assertFalse(validate_source('button("a",function()return 1 end)')['valid'])
        self.assertFalse(validate_source('local cb=flag and function()return 1 end')['valid'])
        self.assertTrue(validate_source('local function cb()return 1 end; button("a",cb)')['valid'])
    def test_static_materials_and_control_reuse_warning(self):
        code='ac.createImage({name="same",resId="$12"});ac.createImage({name="same",resId="$12"})'
        self.assertFalse(validate_source(code,[])['valid'])
        report=validate_source(code,['$12']);self.assertTrue(report['valid'])
        self.assertIn('reused_control_name',[w['code'] for w in report['warnings']])
    def test_save_blocks_invalid_before_network(self):
        bridge=Mock();p=self.write('bad.lua','fn(function()end)')
        with self.assertRaises(BridgeError):SceneService(bridge).apply_scene(1,2,str(p),'0'*64,str(self.root/'backups'))
        bridge._auth.assert_not_called()
    def test_validate_cli_exit_status_and_json(self):
        p=self.write('bad.lua','end')
        r=subprocess.run([sys.executable,'-m','pegasus_bridge.cli','validate-scene',str(p)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(r.returncode,1);self.assertFalse(json.loads(r.stdout)['valid'])


SCRIPT='''
local show
local function nextNode()show("end")end
show=function(key)
 ac.createButton({name="next",inlayer="window",pos={50,50},size={60,40},visible=true,click_func=nextNode})
 -- PEGASUS-NODE-HOOK
end
show("start")
'''


class AuthoringTests(Files):
    def setUp(self):
        super().setUp();self.upload=Mock();self.upload.upload_image.return_value={'id':123}
        self.service=AuthoringService(Mock(),self.upload);self.path=self.root/'cast.json'
    def add(self):
        return self.service.character_add(str(self.path),42,'nurse','许岚',{'neutral':str(self.png())})
    def test_registry_hash_conflict_before_upload(self):
        report=self.add();self.upload.reset_mock()
        with self.assertRaises(BridgeError):self.service.character_add(str(self.path),42,'nurse','许岚',{'angry':str(self.png())},'0'*64)
        self.upload.upload_image.assert_not_called()
        self.assertEqual(self.service.read(str(self.path))['sha256'],report['sha256'])
    def test_validate_all_images_before_first_upload(self):
        with self.assertRaises(BridgeError):self.service.character_add(str(self.path),42,'nurse','许岚',{'neutral':str(self.png()),'missing':str(self.root/'missing.png')})
        self.upload.upload_image.assert_not_called()
    def test_external_manifest_edit_during_upload_is_preserved(self):
        report=self.add()
        def upload(*args):
            data=read_json(self.path);data['characters']['nurse']['name']='外部编辑'
            self.write(self.path.name,data);return {'id':124}
        self.upload.upload_image.side_effect=upload
        with self.assertRaises(BridgeError):
            self.service.character_add(str(self.path),42,'nurse','许岚',{'worried':str(self.png())},report['sha256'])
        self.assertEqual(read_json(self.path)['characters']['nurse']['name'],'外部编辑')
    def test_place_missing_character_and_nonfinite_layout(self):
        report=self.add()
        for key,layout in [('unknown',{}),('nurse',{'width':-1}),('nurse',{'x':float('nan')})]:
            with self.assertRaises(BridgeError):self.service.character_place(str(self.path),'start',key,'neutral',layout,report['sha256'])
        self.assertEqual(self.service.read(str(self.path))['sha256'],report['sha256'])
    def test_compile_lifecycle_expression_audio_and_clear(self):
        report=self.add()
        report=self.service.character_place(str(self.path),'start','nurse','neutral',{'x':300},report['sha256'])
        report=self.service.audio_bind(str(self.path),'start','ambient','$456',True,25,report['sha256'])
        source=self.write('source.lua',SCRIPT);out=self.root/'built.lua'
        built=build_scene(str(source),str(self.path),str(out));self.assertTrue(built['validation']['valid'])
        bundle=dict(source=out.read_text(encoding='utf-8'),variables={},width=1920,height=1080,title='QA',script_sha256=built['sha256'],media={})
        game=PreviewGame(bundle);state=game.snapshot()
        self.assertTrue(state['objects']['pgas_actor_nurse']['visible']);self.assertEqual(state['objects']['pgas_actor_nurse']['resId'],'$123')
        self.assertIn('pgas_audio_ambient',state['audio'])
        state=game.click('next',state['revision'])
        self.assertFalse(state['objects']['pgas_actor_nurse']['visible']);self.assertFalse(state['audio'])
    def test_no_hook_and_overwrite_rejected(self):
        self.add();source=self.write('source.lua','return 1')
        with self.assertRaises(BridgeError):build_scene(str(source),str(self.path),str(self.root/'built.lua'))
        self.assertFalse((self.root/'built.lua').exists())
    def test_audio_only_configuration(self):
        report=self.service.init(str(self.path),42)
        report=self.service.audio_bind(str(self.path),'start','bgm','$9',True,40,report['sha256'])
        self.assertEqual(self.service.read(str(self.path))['manifest']['audio']['start'][0]['volume'],40)
        with self.assertRaises(BridgeError):self.service.init(str(self.path),42)


class BatchTests(Files):
    def setUp(self):
        super().setUp();self.remote={1:'return 1',2:'return 2'};self.writes=[];self.lose_reply=False
        self.bridge=Mock();self.bridge.read.side_effect=lambda resource,gid,sid:{'response':{'data':{'command':self.remote[sid]}}}
        self.scene=Mock();self.scene.apply_scene.side_effect=self.apply
        self.service=BatchService(self.bridge,self.scene)
    def apply(self,gid,sid,path,expected,backup,**kwargs):
        if source_hash(self.remote[sid])!=expected:raise BridgeError('conflict')
        self.writes.append(sid);self.remote[sid]=Path(path).read_text(encoding='utf-8')
        if self.lose_reply:self.lose_reply=False;raise BridgeError('lost response')
        return {'verified':True,'sha256':source_hash(self.remote[sid])}
    def plan(self):
        self.write('one.lua','return 10');self.write('two.lua','return 20')
        p=self.write('input.json',{'game_id':42,'scenes':[{'scene_id':1,'script_path':'one.lua'},{'scene_id':2,'script_path':'two.lua'}]})
        return self.service.plan(str(p),str(self.root/'plan'))['plan_path']
    def test_plan_no_write_and_resume_lost_response(self):
        plan=self.plan();self.assertFalse(self.writes)
        self.lose_reply=True;r=self.service.apply(plan);self.assertFalse(r['complete']);self.assertEqual(self.writes,[1])
        r=self.service.resume(plan);self.assertTrue(r['complete']);self.assertEqual(self.writes,[1,2])
        self.assertTrue(self.service.resume(plan)['complete']);self.assertEqual(self.writes,[1,2])
    def test_conflict_in_later_scene_prevents_all_writes(self):
        plan=self.plan();self.remote[2]='return 999'
        r=self.service.apply(plan);self.assertEqual(r['status'],'conflict');self.assertFalse(self.writes)
    def test_target_tamper_or_lock_prevents_all_writes(self):
        plan=self.plan();target=self.root/'plan/001-target.lua';old=target.read_text();target.write_text('return 100')
        with self.assertRaises(BridgeError):self.service.apply(plan)
        target.write_text(old);self.write('plan/apply.lock','someone')
        with self.assertRaises(BridgeError):self.service.apply(plan)
        self.assertFalse(self.writes)
    def test_source_snapshot_is_frozen(self):
        plan=self.plan();self.write('one.lua','return 999');self.service.apply(plan)
        self.assertEqual(self.remote[1],'return 10')
    def test_target_edit_during_remote_preflight_cannot_change_saved_content(self):
        plan=self.plan()
        def read(resource,gid,sid):
            (self.root/'plan/001-target.lua').write_text('return 999')
            return {'response':{'data':{'command':self.remote[sid]}}}
        self.bridge.read.side_effect=read
        self.assertTrue(self.service.apply(plan)['complete'])
        self.assertEqual(self.remote[1],'return 10')
    def test_history_diff_restore_hash_guard(self):
        backup=self.write('backups/change.json',dict(game_id=42,scene_id=1,before='return 0',requested='return 1',expected_sha256=source_hash('return 0')))
        self.assertEqual(len(self.service.history(str(backup.parent))['backups']),1)
        self.assertIn('-return 0',self.service.diff(str(backup))['diff'])
        with self.assertRaises(BridgeError):self.service.restore(str(backup),'0'*64,str(self.root/'restore'))
        self.assertFalse(self.writes)
        self.service.restore(str(backup),source_hash('return 1'),str(self.root/'restore'));self.assertEqual(self.remote[1],'return 0')
    def test_history_rejects_tampered_backup(self):
        backup=self.write('bad.json',dict(game_id=42,scene_id=1,before='return 0',requested='return 1',expected_sha256='0'*64))
        with self.assertRaises(BridgeError):self.service.restore(str(backup),source_hash('return 1'),str(self.root/'restore'))
        self.assertFalse(self.writes)
    def test_actual_restore_to_empty_uses_delete_only(self):
        remote=['return 1'];requests=[]
        bridge=Mock();bridge._auth.return_value=({}, {})
        def handle(req):
            requests.append(req)
            if req.method=='PUT':
                commands=json.loads(req.content)['commandList']
                self.assertEqual([c['type'] for c in commands],['DELETE'])
                remote[0]='';data={}
            elif req.url.path.endswith('/token'):data={'token':'private'}
            else:data={'command':remote[0]}
            return httpx.Response(200,json={'state':{'code':200000},'data':data})
        service=BatchService(bridge,SceneService(bridge,httpx.MockTransport(handle),lambda gid:'sid'))
        backup=self.write('old.json',dict(game_id=42,scene_id=1,before='',requested='return 1',expected_sha256=source_hash('')))
        report=service.restore(str(backup),source_hash('return 1'),str(self.root/'backups'))
        self.assertTrue(report['verified']);self.assertEqual(remote[0],'')
        self.assertEqual(sum(r.method=='PUT' for r in requests),1)


class AudioTests(Files):
    def setUp(self):
        super().setUp();self.path=self.root/'tone.wav'
        with wave.open(str(self.path),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*800)
        self.bridge=Mock();self.bridge._auth.return_value=({'Cookie':'TOKEN=private'},{'csrf_token':'private'})
        self.requests=[];self.fail=False
        self.service=AudioService(self.bridge,httpx.MockTransport(self.handle))
    def handle(self,r):
        self.requests.append(r)
        if r.url.host=='fp.ps.netease.com':
            self.assertNotIn('cookie',r.headers);self.assertNotIn('csrf_token',r.url.params)
            return httpx.Response(200,headers={'response-fid':'audio-test'})
        if r.url.path.endswith('/token'):data={'token':'scoped'}
        elif r.method=='POST':
            if self.fail:raise httpx.ReadTimeout('private query')
            data={'id':101,'gameMaterialId':102}
        else:data={'ownerList':[{'id':9,'isDefault':1}]}
        return httpx.Response(200,json={'state':{'code':200000},'data':data})
    def upload(self):return self.service.upload_audio(42,str(self.path),'ambient',str(self.root/'checkpoint.json'))
    def test_real_wav_metadata_and_checkpoint_reuse(self):
        r=self.upload();self.assertEqual(r['duration'],0.1);count=len(self.requests)
        self.assertTrue(self.upload()['reused']);self.assertEqual(len(self.requests),count)
        saved=(self.root/'checkpoint.json').read_text();self.assertNotIn('private',saved);self.assertNotIn('scoped',saved)
    def test_uncertain_registration_is_not_retried(self):
        self.fail=True
        with self.assertRaises(BridgeError):self.upload()
        count=len(self.requests)
        with self.assertRaises(BridgeError):self.upload()
        self.assertEqual(len(self.requests),count)
    def test_truncated_wav_rejected_before_auth(self):
        raw=self.path.read_bytes();self.path.write_bytes(raw[:-10])
        with self.assertRaises(BridgeError):self.upload()
        self.bridge._auth.assert_not_called()


class ProjectAndRouteTests(Files):
    def project(self):
        self.write('scene.lua','ac.createImage({name="portrait",resId="$1"})');self.png()
        return self.write('manifest.json',dict(version=1,title='测试',game_id=42,scenes=[dict(scene_id=1,script='scene.lua')],assets={'$1':'person.png'}))
    def test_deterministic_pack_and_ledger(self):
        p=self.project();self.assertTrue(project_check(str(p))['valid'])
        a=project_pack(str(p),str(self.root/'a.zip'));b=project_pack(str(p),str(self.root/'b.zip'))
        self.assertEqual(a['sha256'],b['sha256'])
        with zipfile.ZipFile(a['path']) as z:
            hashes=json.loads(z.read('checksums.json'))
            for name,expected in hashes.items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),expected)
        with self.assertRaises(BridgeError):project_pack(str(p),str(self.root/'a.zip'))
    def test_missing_material_path_escape_and_credentials(self):
        p=self.project();v=read_json(p);v['assets']={};self.write(p.name,v)
        self.assertFalse(project_check(str(p))['valid'])
        with self.assertRaises(BridgeError):project_pack(str(p),str(self.root/'bad.zip'))
        v['assets']={'$1':'../other.png'};self.write(p.name,v)
        with self.assertRaises(BridgeError):project_check(str(p))
        v['assets']={'$1':'person.png'};v['supporting_files']=['secret.json'];self.write('secret.json',{'token':'sensitive'});self.write(p.name,v)
        with self.assertRaises(BridgeError):project_check(str(p))
    def test_route_valid_and_failed_assertions_saved(self):
        self.write('scene.lua','local function go()ac.var.done=true end; ac.createButton({name="go",inlayer="window",visible=true,click_func=go})')
        p=self.write('preview.json',dict(version=1,script='scene.lua',assets={}))
        route=self.write('route.json',dict(version=1,steps=[dict(click='go',expect={'variables':{'done':True}})]))
        r=test_route(str(p),str(route),str(self.root/'pass'));self.assertTrue(r['passed']);self.assertEqual(r['snapshots'],2)
        route=self.write('route.json',dict(version=1,steps=[dict(click='missing')]))
        r=test_route(str(p),str(route),str(self.root/'fail'));self.assertFalse(r['passed']);self.assertTrue(Path(r['report_path']).exists())
    def test_route_hidden_parent_cannot_be_clicked(self):
        self.write('scene.lua','local function go()end;ac.createLayer({name="hidden",inlayer="window",visible=false});ac.createButton({name="go",inlayer="hidden",visible=true,click_func=go})')
        p=self.write('preview.json',dict(version=1,script='scene.lua',assets={}))
        route=self.write('route.json',dict(version=1,initial={'visible':{'go':False}},steps=[dict(click='go')]))
        r=test_route(str(p),str(route),str(self.root/'out'));self.assertTrue(r['steps'][0]['passed']);self.assertFalse(r['passed'])


class BindingTests(unittest.TestCase):
    def test_cli_and_mcp_expose_same_workflow_names(self):
        import argparse,asyncio
        from pegasus_bridge.workflow_cli import add_arguments,COMMANDS
        from pegasus_bridge.server import create_server
        parser=argparse.ArgumentParser();subs=parser.add_subparsers();add_arguments(subs)
        self.assertEqual(set(subs.choices),COMMANDS)
        names={t.name for t in asyncio.run(create_server(Mock()).list_tools())}
        self.assertTrue({'pegasus_'+n.replace('-','_') for n in COMMANDS}<=names)

if __name__=='__main__':unittest.main()
