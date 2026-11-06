import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx

from pegasus_bridge.core import BridgeError
from pegasus_bridge.preview import PreviewHTTP, PreviewService
from pegasus_bridge.preview_runtime import PREVIEW_KEYS, PreviewGame, PreviewFile, PreviewVideo, load_bundle

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
        for config in [dict(width=True), dict(height=10000), dict(variables={'x': []}),dict(instruction_limit=True),dict(instruction_limit=200000001)]:
            with self.assertRaises(BridgeError):
                load_bundle(self.manifest(**config))

    def test_mp4_manifest_streams_instead_of_reading_entire_video(self):
        path=self.root/'clip.mp4'
        path.write_bytes(b'\x00\x00\x00\x18ftypisom'+b'\x00'*1000)
        bundle=load_bundle(self.manifest(video_assets={'$clip':'clip.mp4'}))
        video,mime=bundle['media']['$clip']
        self.assertIsInstance(video,PreviewVideo)
        self.assertEqual(mime,'video/mp4')
        self.assertTrue(video.unchanged())
        path.write_bytes(b'changed')
        self.assertFalse(video.unchanged())

    def test_rejects_fake_mp4_and_video_path_escape(self):
        (self.root/'fake.mp4').write_bytes(b'not mp4 content at all')
        for path in ['fake.mp4',str(self.root/'fake.mp4')]:
            with self.assertRaises(BridgeError):
                load_bundle(self.manifest(video_assets={'$clip':path}))

    def test_lazy_assets_capture_bounded_file_identity_without_reading_image_bytes(self):
        image=self.root/'large.png';image.write_bytes(b'original image bytes')
        original=Path.read_bytes
        def guarded(path):
            if path.resolve()==image.resolve():raise AssertionError('lazy image read during bundle construction')
            return original(path)
        with patch.object(Path,'read_bytes',guarded):
            bundle=load_bundle(self.manifest(assets={'$art':'large.png'},lazy_assets=True,asset_total_limit_mib=1024))
        content,mime=bundle['media']['$art'];self.assertIsInstance(content,PreviewFile)
        self.assertNotIsInstance(content,PreviewVideo);self.assertEqual(mime,'image/png');self.assertTrue(content.unchanged())
        image.write_bytes(b'changed');self.assertFalse(content.unchanged())

    def test_lazy_asset_opt_in_preserves_default_memory_path_size_and_total_bounds(self):
        (self.root/'a.png').write_bytes(b'x'*1024*1024)
        for options in [dict(lazy_assets=1),dict(asset_total_limit_mib=1024),dict(lazy_assets=True,asset_total_limit_mib=1025),
                        dict(lazy_assets=True,asset_total_limit_mib=1,assets={'$a':'a.png','$b':'a.png'})]:
            with self.subTest(options=options),self.assertRaises(BridgeError):load_bundle(self.manifest(**options))
        (self.root/'bad.svg').write_text('<svg/>')
        for value in ['bad.svg',str(self.root/'a.png')]:
            with self.assertRaises(BridgeError):load_bundle(self.manifest(lazy_assets=True,assets={'$bad':value}))
        with(self.root/'oversize.png').open('wb')as stream:stream.truncate(32*1024*1024+1)
        with self.assertRaises(BridgeError):load_bundle(self.manifest(lazy_assets=True,assets={'$bad':'oversize.png'}))

    def test_session_ids_cannot_escape_registry(self):
        with self.assertRaises(BridgeError):
            PreviewService(self.root).state('../preview')

    def test_text_load_requires_explicit_boolean_manifest_option(self):
        self.assertFalse(load_bundle(self.manifest())['allow_text_load'])
        self.assertTrue(load_bundle(self.manifest(allow_text_load=True))['allow_text_load'])
        with self.assertRaises(BridgeError):load_bundle(self.manifest(allow_text_load='yes'))

    def test_decoded_text_budget_requires_strict_bounded_explicit_opt_in(self):
        self.assertEqual(load_bundle(self.manifest())['text_load_limit_bytes'],2*1024*1024)
        self.assertEqual(load_bundle(self.manifest(allow_text_load=True,text_load_limit_mib=4))['text_load_limit_bytes'],4*1024*1024)
        self.assertEqual(load_bundle(self.manifest(allow_text_load=True,text_load_limit_mib=8))['text_load_limit_bytes'],8*1024*1024)
        for value in [True,0,9,2.5,'4']:
            with self.assertRaises(BridgeError):load_bundle(self.manifest(allow_text_load=True,text_load_limit_mib=value))
        with self.assertRaises(BridgeError):load_bundle(self.manifest(text_load_limit_mib=4))


@unittest.skipUnless(HAS_LUA, 'optional preview dependency not installed')
class RuntimeTests(unittest.TestCase):
    def test_standard_raw_table_primitives_support_cached_callback_proxies(self):
        game=self.game('''
local data=setmetatable({}, {__index=function()return 99 end,__newindex=function()error('metamethod must not run')end})
assert(rawget(data,'x')==nil);assert(rawequal(rawset(data,'x',12),data));assert(data.x==12)
local value=0
local proxy=setmetatable({}, {__index=function(cache,key)
 local fn=function()value=value+1 end;rawset(cache,key,fn);return fn
end})
ac.createButton({name='cached',click_func=function()local fn=proxy.click;assert(rawequal(fn,rawget(proxy,'click')));fn();ac.var.raw=value end})
assert(not pcall(rawset,'text','x',1));assert(os==nil and python==nil and debug==nil)
''')
        game.click('cached',game.revision)
        self.assertEqual(game.snapshot()['variables']['raw'],1)

    def test_media_actual_report_independent_from_request_bus_and_strict_types(self):
        game=self.game('''
local config={name='v',res_id='$clip',reportedPaused=true,reportedReadyState=4}
ac.createButton({name='forge',click_func=function()
 config.reportedPaused=true;config.reportedReadyState=4;config.requestSerial=99
 local s=pegasusReadPreviewVideo('v');s.reportedPaused=true;s.reportedReadyState=4
end})
ac.createButton({name='pause',click_func=function()
 local s=pegasusSetPreviewVideoPaused('v',true)
 ac.var.reportedPaused=s.reportedPaused;ac.var.reportedSerial=s.reportedRequestSerial;ac.var.requestSerial=s.requestSerial
end})
ac.createButton({name='inspect',click_func=function()
 local s=pegasusReadPreviewVideo('v');ac.var.paused=s.reportedPaused;ac.var.ready=s.reportedReadyState
end})
ac.showVideo(config)
''',clock=lambda:100)
        game.click('forge',game.revision)
        self.assertEqual(game.snapshot()['videos']['v']['requestSerial'],0)
        self.assertNotIn('reportedPaused',game.snapshot()['videos']['v'])
        game.video_event(dict(name='v',generation=1,kind='state',time=1,duration=8,paused=False,readyState=2,requestSerial=0))
        self.assertEqual(game.snapshot()['videos']['v']['status'],'awaiting_browser')
        self.assertEqual(game.snapshot()['videos']['v']['time'],0)  # state reports never fire source time
        game.click('pause',game.revision);s=game.snapshot()
        self.assertIs(s['variables']['reportedPaused'],False)
        self.assertEqual((s['variables']['reportedSerial'],s['variables']['requestSerial']),(0,1))
        game.video_event(dict(name='v',generation=1,kind='state',time=1,duration=8,paused=True,readyState=4,requestSerial=1))
        game.click('inspect',game.revision);self.assertTrue(game.snapshot()['variables']['paused'])
        for fields in [dict(paused=1),dict(readyState=True),dict(readyState=5),dict(readyState=-1),dict(requestSerial=True),dict(requestSerial=2)]:
            event=dict(name='v',generation=1,kind='state',time=1,duration=8,paused=False,readyState=2,requestSerial=1);event.update(fields)
            with self.assertRaises(BridgeError):game.video_event(event)
        with self.assertRaises(BridgeError):game.video_event(dict(name='v',generation=1,kind='state',time=0))
        self.assertIsNone(game.fault)

    def test_media_report_does_not_cross_same_name_video_generations(self):
        game=self.game('''ac.showVideo({name='v',res_id='$one'});ac.showVideo({name='v',res_id='$two'})''')
        game.video_event(dict(name='v',generation=1,kind='playing',time=0,duration=2,paused=False,readyState=4,requestSerial=0))
        game.video_event(dict(name='v',generation=1,kind='ended',time=2,duration=2,paused=True,readyState=4,requestSerial=0))
        s=game.snapshot()['videos']['v'];self.assertEqual(s['generation'],2)
        for field in ('reportedPaused','reportedReadyState','reportedRequestSerial','reportedAt','reportedRate'):
            self.assertNotIn(field,s)
        game.video_event(dict(name='v',generation=1,kind='state',time=2,duration=2,paused=True,readyState=4,requestSerial=0))
        self.assertNotIn('reportedPaused',game.snapshot()['videos']['v'])

    def test_video_rate_is_observed_separately_from_requested_rate_and_old_generations(self):
        game=self.game('''
ac.createButton({name='rate',click_func=function()
 local s=pegasusSetPreviewVideoRate('v',2)
 ac.var.immediate=s.reportedRate;ac.var.serial=s.reportedRequestSerial;ac.var.request=s.requestSerial
end})
ac.showVideo({name='v',res_id='$one',reportedRate=3});ac.showVideo({name='v',res_id='$two'})
''')
        self.assertNotIn('reportedRate',game.snapshot()['videos']['v'])
        report=dict(name='v',generation=1,kind='playing',time=0,duration=8,paused=False,readyState=4,requestSerial=0,playbackRate=1)
        game.video_event(report);game.click('rate',game.revision)
        s=game.snapshot();self.assertEqual(s['variables']['immediate'],1)
        self.assertEqual((s['variables']['serial'],s['variables']['request']),(0,1))
        self.assertEqual(s['videos']['v']['requestedRate'],2)
        for value in [True,0,5,float('nan')]:
            with self.assertRaises(BridgeError):game.video_event(dict(report,requestSerial=1,playbackRate=value))
        game.video_event(dict(report,requestSerial=1,playbackRate=2))
        self.assertEqual(game.snapshot()['videos']['v']['reportedRate'],2)
        game.video_event(dict(report,kind='ended',time=8,requestSerial=1,playbackRate=2))
        s=game.snapshot()['videos']['v'];self.assertEqual(s['generation'],2);self.assertNotIn('reportedRate',s)
        game.video_event(dict(report,requestSerial=1,playbackRate=2));self.assertNotIn('reportedRate',game.snapshot()['videos']['v'])

    @unittest.skipUnless(os.environ.get('B13_MEDIA_SMOKE')=='1','opt-in independent actual HTMLVideoElement smoke')
    def test_actual_dom_media_pause_resume_reports_do_not_ack_request_synchronously(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);ffmpeg=shutil.which('ffmpeg')
            self.assertIsNotNone(ffmpeg)
            subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=black:s=160x90:r=10',
                            '-t','8','-an','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(root/'clip.mp4')],check=True,capture_output=True,timeout=30)
            (root/'scene.lua').write_text('''
local function change(paused)
 local s=pegasusSetPreviewVideoPaused('v',paused)
 ac.var.immediatePaused=s.reportedPaused;ac.var.immediateSerial=s.reportedRequestSerial;ac.var.requestSerial=s.requestSerial
end
ac.createButton({name='pause',text='Pause media',pos={100,650},size={180,60},click_func=function()change(true)end})
ac.createButton({name='resume',text='Resume media',pos={320,650},size={180,60},click_func=function()change(false)end})
ac.createButton({name='rate',text='Double speed',pos={540,650},size={180,60},click_func=function()
 local s=pegasusSetPreviewVideoRate('v',2);ac.var.immediateRate=s.reportedRate;ac.var.rateSerial=s.reportedRequestSerial
end})
ac.showVideo({name='v',res_id='$clip'})
''',encoding='utf-8')
            manifest=root/'preview.json';manifest.write_text(json.dumps(dict(version=1,script='scene.lua',width=1280,height=720,video_assets={'$clip':'clip.mp4'})))
            server=PreviewHTTP(load_bundle(manifest));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with sync_playwright()as pw:
                    browser=pw.chromium.launch(executable_path=os.environ.get('PEGASUS_PREVIEW_BROWSER_EXECUTABLE',r'C:/Users/admin/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe'),headless=True)
                    try:
                        page=browser.new_page(viewport=dict(width=1280,height=800));page.goto(f'http://127.0.0.1:{server.server_port}{server.prefix}')
                        def wait(expression):
                            for _ in range(240):
                                if page.evaluate(expression):return
                                time.sleep(.025)
                            self.fail('Media DOM condition not reached: '+expression+'; '+str(page.evaluate('state')))
                        wait("state?.videos.v?.reportedReadyState>=2 && state.videos.v.reportedPaused===false")
                        self.assertFalse(page.locator('video').evaluate('(v)=>v.paused'))
                        page.get_by_role('button',name='Pause media',exact=True).click()
                        wait('state.variables.requestSerial===1')
                        self.assertIs(page.evaluate('state.variables.immediatePaused'),False)
                        self.assertEqual(page.evaluate('state.variables.immediateSerial'),0)
                        wait('state.videos.v.reportedPaused===true && state.videos.v.reportedRequestSerial===1')
                        self.assertTrue(page.locator('video').evaluate('(v)=>v.paused'))
                        page.get_by_role('button',name='Resume media',exact=True).click()
                        wait('state.variables.requestSerial===2')
                        self.assertTrue(page.evaluate('state.variables.immediatePaused'))
                        self.assertEqual(page.evaluate('state.variables.immediateSerial'),1)
                        wait('state.videos.v.reportedPaused===false && state.videos.v.reportedRequestSerial===2')
                        self.assertFalse(page.locator('video').evaluate('(v)=>v.paused'))
                        self.assertGreaterEqual(page.locator('video').evaluate('(v)=>v.readyState'),2)
                        self.assertEqual(page.evaluate('state.videos.v.reportedRate'),1)
                        page.get_by_role('button',name='Double speed',exact=True).click()
                        wait('state.videos.v.requestSerial===3')
                        self.assertEqual(page.evaluate('state.variables.immediateRate'),1)
                        self.assertEqual(page.evaluate('state.variables.rateSerial'),2)
                        wait('state.videos.v.reportedRate===2 && state.videos.v.reportedRequestSerial===3')
                        self.assertEqual(page.locator('video').evaluate('(v)=>v.playbackRate'),2)
                        self.assertIsNone(page.evaluate('state.fault'))
                        print('Actual Chromium media paused/readyState+request versions verified; no hardware/audio proof')
                    finally:browser.close()
            finally:server.shutdown();thread.join(timeout=5);server.server_close()

    def test_live_session_keeps_its_frontend_after_package_upgrade(self):
        with patch('pegasus_bridge.preview.frontend_html',return_value=b'old-compatible-frontend'):
            server=PreviewHTTP(load_bundle(EXAMPLE))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch('pegasus_bridge.preview.frontend_html',return_value=b'new-incompatible-frontend'):
                with httpx.Client(trust_env=False) as client:
                    response=client.get(f'http://127.0.0.1:{server.server_port}{server.prefix}')
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.content,b'old-compatible-frontend')
        finally:
            server.shutdown();thread.join(timeout=5);server.server_close()

    def game(self, code=None, clock=None, allow_text_load=False,text_load_limit_mib=2):
        bundle = load_bundle(EXAMPLE)
        if code is not None:
            bundle['source'] = code
        bundle['allow_text_load']=allow_text_load
        bundle['text_load_limit_bytes']=text_load_limit_mib*1024*1024
        return PreviewGame(bundle,clock=clock)

    def test_explicit_large_text_chunk_keeps_sandbox_and_default_boundary(self):
        code="local source='--'..string.rep('x',2200000)..'\\nreturn os==nil and python==nil and debug==nil';ac.var.ok=assert(load(source))()"
        with self.assertRaises(BridgeError):self.game(code,allow_text_load=True)
        game=self.game(code,allow_text_load=True,text_load_limit_mib=4)
        self.assertIs(game.snapshot()['variables']['ok'],True)
        with self.assertRaises(BridgeError):self.game("assert(load(string.rep(' ',4194305)))",allow_text_load=True,text_load_limit_mib=4)

    def test_explicit_eight_mib_text_budget_retains_cumulative_quota_and_sandbox(self):
        code="local source='--'..string.rep('x',5242880)..'\\nreturn os==nil and python==nil and debug==nil';ac.var.ok=assert(load(source))();assert(not pcall(load,string.rep(' ',3145728)))"
        game=self.game(code,allow_text_load=True,text_load_limit_mib=8)
        self.assertIs(game.snapshot()['variables']['ok'],True)
        with self.assertRaises(BridgeError):self.game("assert(load(string.rep(' ',8388609)))",allow_text_load=True,text_load_limit_mib=8)

    def test_readonly_button_generation_rejects_layer_and_replacement(self):
        game=self.game('''
assert(pegasusReadPreviewButtonGeneration('absent')==nil)
assert(not pcall(pegasusReadPreviewButtonGeneration,{}))
ac.createLayer({name='layer',pos={0,0}})
assert(pegasusReadPreviewButtonGeneration('layer')==nil)
ac.createButton({name='same',text='a',click_func=function()end})
local first=pegasusReadPreviewButtonGeneration('same')
assert(type(first)=='number')
ac.remove({name='same'})
assert(pegasusReadPreviewButtonGeneration('same')==nil)
ac.createButton({name='same',text='b',click_func=function()end})
assert(pegasusReadPreviewButtonGeneration('same')~=first)
ac.var.ok=1
''')
        self.assertEqual(game.snapshot()['variables']['ok'],1)

    def test_preview_mix_readback_is_a_copy_and_preserves_false(self):
        game=self.game('''
local initial=pegasusReadPreviewSettings()
assert(initial.isMuted==false and initial.volume==1 and initial.videoVolume==1 and initial.musicVolume==1 and initial.fxVolume==1)
initial.isMuted=true;initial.volume=0;initial.videoVolume=0;initial.musicVolume=0;initial.fxVolume=0
assert(pegasusReadPreviewSettings().volume==1 and pegasusReadPreviewSettings().isMuted==false)
local returned=pegasusSetPreviewSettings({isMuted=false})
assert(returned.isMuted==false)
returned=pegasusSetPreviewSettings({volume=0.4})
assert(returned.volume==0.4 and returned.musicVolume==0.4)
returned.volume=0;returned.musicVolume=0;returned.isMuted=true
ac.var.muted=pegasusReadPreviewSettings().isMuted
''')
        self.assertEqual(game.snapshot()['mix_settings'],dict(isMuted=False,volume=.4,videoVolume=.4,musicVolume=.4,fxVolume=.4))
        self.assertIs(game.snapshot()['variables']['muted'],False)

    def test_preview_mix_matches_source_mute_then_volume_effects_quirk(self):
        game=self.game('''
ac.createButton({name='volume8',click_func=function()pegasusSetPreviewSettings({volume=0.8})end})
ac.createButton({name='mute',click_func=function()pegasusSetPreviewSettings({isMuted=true})end})
ac.createButton({name='volume2',click_func=function()pegasusSetPreviewSettings({volume=0.2})end})
ac.createButton({name='unmute',click_func=function()pegasusSetPreviewSettings({isMuted=false})end})
ac.createButton({name='zero',click_func=function()pegasusSetPreviewSettings({volume=0})end})
''')
        def click(name):return game.click(name,game.revision)['mix_settings']
        self.assertEqual(click('volume8'),dict(isMuted=False,volume=.8,videoVolume=.8,musicVolume=.8,fxVolume=.8))
        self.assertEqual(click('mute'),dict(isMuted=True,volume=.8,videoVolume=0,musicVolume=0,fxVolume=0))
        self.assertEqual(click('volume2'),dict(isMuted=True,volume=.2,videoVolume=0,musicVolume=0,fxVolume=.2))
        self.assertEqual(click('mute'),dict(isMuted=True,volume=.2,videoVolume=0,musicVolume=0,fxVolume=0))
        self.assertEqual(click('unmute'),dict(isMuted=False,volume=.2,videoVolume=.2,musicVolume=.2,fxVolume=.2))
        self.assertEqual(click('zero'),dict(isMuted=False,volume=0,videoVolume=0,musicVolume=0,fxVolume=0))

    def test_preview_mix_rejects_invalid_updates_atomically(self):
        game=self.game('''
pegasusSetPreviewSettings({volume=0.6})
local invalid={false,0,'volume',{}, {isMuted=0},{isMuted='false'},{volume=false},{volume='0.5'},
 {volume=-0.1},{volume=1.01},{volume=0/0},{volume=math.huge},{volume=-math.huge},
 {volume=0.2,unknown=true},{isMuted=false,unknown=true},{videoVolume=0},{musicVolume=0},{fxVolume=0},
 {volume=0.2,isMuted=true},{[1]=0.2}}
assert(not pcall(pegasusSetPreviewSettings,nil))
for _,value in ipairs(invalid)do
 assert(not pcall(pegasusSetPreviewSettings,value))
 local current=pegasusReadPreviewSettings()
 assert(current.isMuted==false and current.volume==0.6 and current.videoVolume==0.6 and current.musicVolume==0.6 and current.fxVolume==0.6)
end
''')
        self.assertIsNone(game.fault)
        self.assertEqual(game.snapshot()['mix_settings']['volume'],.6)

    def test_preview_mix_works_in_key_timer_callbacks_without_extra_permissions(self):
        game=self.game('''
pegasusBindPreviewKeys(function()
 assert(CS==nil and python==nil and os==nil and io==nil and require==nil and package==nil and debug==nil)
 pegasusSetPreviewSettings({isMuted=true})
 CltTimer.SetTimeout(function()pegasusSetPreviewSettings({volume=0.3})end,0.1)
 return true
end)
''',clock=lambda:0)
        self.assertEqual(game.key('Enter',1)['mix_settings'],dict(isMuted=True,volume=1,videoVolume=0,musicVolume=0,fxVolume=0))
        game.advance(.1)
        self.assertEqual(game.snapshot()['mix_settings'],dict(isMuted=True,volume=.3,videoVolume=0,musicVolume=0,fxVolume=.3))

    def test_preview_audio_defaults_to_music_and_preserves_effect_requests(self):
        game=self.game("ac.createAudio({name='legacy',res_id='$sound'});ac.createAudio({name='fx',res_id='$sound',source_type=2,targetVolume=70})")
        state=game.snapshot()
        self.assertEqual(state['audio']['legacy']['source_type'],1)
        self.assertEqual(state['audio']['fx']['source_type'],2)
        self.assertEqual(state['audio']['fx']['targetVolume'],70)

    def test_preview_mix_is_isolated_and_old_setter_cannot_mutate_after_close(self):
        from lupa.lua54 import LuaError
        first=self.game('ac.var.setter=pegasusSetPreviewSettings;pegasusSetPreviewSettings({volume=0.25})')
        second=self.game('')
        old_setter=first.runtime.variables['setter']
        first.close()
        with self.assertRaisesRegex(LuaError,'Preview is closed'):
            old_setter(first.lua.table_from(dict(volume=.5)))
        self.assertEqual(first.snapshot()['mix_settings']['volume'],.25)
        self.assertEqual(second.snapshot()['mix_settings'],dict(isMuted=False,volume=1,videoVolume=1,musicVolume=1,fxVolume=1))

    def test_preview_keys_lifo_modal_consumption_and_unsubscribe(self):
        game=self.game('''
ac.var.order=''
pegasusBindPreviewKeys(function(key,repeated)
 ac.var.order=ac.var.order..'menu:'..key..';';ac.var.repeated=repeated;return true
end)
local unsubscribe
unsubscribe=pegasusBindPreviewKeys(function(key)
 ac.var.order=ac.var.order..'modal:'..key..';'
 if key=='Escape' then assert(unsubscribe());assert(not unsubscribe());return true end
 return false
end)
''')
        state=game.key('Enter',1,True)
        self.assertEqual(state['variables']['order'],'modal:Enter;menu:Enter;')
        self.assertTrue(state['variables']['repeated'])
        state=game.key('Escape',state['revision'])
        self.assertEqual(state['variables']['order'],'modal:Enter;menu:Enter;modal:Escape;')
        self.assertEqual(state['key_binding_count'],1)
        state=game.key('ArrowDown',state['revision'])
        self.assertTrue(state['variables']['order'].endswith('menu:ArrowDown;'))

    def test_preview_keys_snapshot_skips_removed_and_defers_new_bindings(self):
        game=self.game('''
ac.var.order=''
local remove_old=pegasusBindPreviewKeys(function()ac.var.order=ac.var.order..'old'end)
local once=true
pegasusBindPreviewKeys(function()
 ac.var.order=ac.var.order..'top'
 if once then
  once=false;remove_old()
  pegasusBindPreviewKeys(function()ac.var.order=ac.var.order..'new';return true end)
 end
end)
''')
        state=game.key('Enter',1)
        self.assertEqual(state['variables']['order'],'top')
        state=game.key('Enter',state['revision'])
        self.assertEqual(state['variables']['order'],'topnew')
        self.assertEqual(state['key_binding_count'],2)

    def test_preview_key_callbacks_use_existing_coroutine_and_timer_scheduler(self):
        game=self.game('''
pegasusBindPreviewKeys(function()error('lower handler should not run')end)
pegasusBindPreviewKeys(function(key)
 ac.createText({name='pending',content=key})
 ac.waitForSeconds({delay=0.1})
 ac.var.completed=true
 return true
end)
''',clock=lambda:0)
        state=game.key(' ',1)
        self.assertEqual(state['objects']['pending']['content'],' ')
        self.assertNotIn('completed',state['variables'])
        self.assertFalse(game.advance(0.099))
        self.assertTrue(game.advance(0.1))
        self.assertTrue(game.snapshot()['variables']['completed'])
        self.assertIsNone(game.fault)

    def test_preview_key_limit_counts_live_bindings_and_reclaims_unsubscribed_slots(self):
        game=self.game('''
for i=1,1000 do local stop=pegasusBindPreviewKeys(function()end);assert(stop());assert(not stop())end
local stops={}
for i=1,64 do stops[i]=pegasusBindPreviewKeys(function()end)end
assert(not pcall(pegasusBindPreviewKeys,function()end))
assert(stops[64]());pegasusBindPreviewKeys(function()ac.var.last=true;return true end)
''')
        self.assertEqual(game.snapshot()['key_binding_count'],64)
        self.assertTrue(game.key('Enter',1)['variables']['last'])
        for source in ['pegasusBindPreviewKeys(nil)','pegasusBindPreviewKeys({})',
                       'for i=1,65 do pegasusBindPreviewKeys(function()end)end']:
            with self.assertRaises(BridgeError):self.game(source)

    def test_preview_keys_validate_revision_key_and_repeat_before_dispatch(self):
        game=self.game('pegasusBindPreviewKeys(function()ac.var.called=true end)')
        for key in ['Spacebar','Space','ArrowLeft','6','',None,{},True]:
            with self.assertRaises(BridgeError):game.key(key,1)
        for repeated in [None,0,1,'false',{}]:
            with self.assertRaises(BridgeError):game.key('Enter',1,repeated)
        for revision in [True,0,2,None,1.0]:
            with self.assertRaises(BridgeError):game.key('Enter',revision)
        self.assertEqual(game.revision,1)
        self.assertNotIn('called',game.snapshot()['variables'])
        state=game.key('Enter',1)
        self.assertTrue(state['variables']['called'])
        with self.assertRaises(BridgeError):game.key('Escape',1)
        self.assertEqual(game.revision,state['revision'])
        self.assertIsNone(game.fault)

    def test_preview_key_whitelist_and_loaded_callbacks_keep_sandbox(self):
        game=self.game('''
local fn=assert(load([[pegasusBindPreviewKeys(function(key,repeated)
 assert(CS==nil and python==nil and os==nil and io==nil and package==nil and require==nil and debug==nil)
 assert(type(key)=='string' and type(repeated)=='boolean')
 ac.var.last=key;ac.var.repeated=repeated;return true
end)]],'keyboard','t',_G));fn()
''',allow_text_load=True)
        for index,key in enumerate(sorted(PREVIEW_KEYS)):
            state=game.key(key,game.revision,index%2==0)
            self.assertEqual(state['variables']['last'],key)
            self.assertEqual(state['variables']['repeated'],index%2==0)
        self.assertEqual(game.snapshot()['key_binding_count'],1)

    def test_preview_key_faults_and_shared_dispatch_instruction_budget(self):
        for body in ["error('key failure')",'while true do end']:
            game=self.game('pegasusBindPreviewKeys(function()'+body+' end)')
            with self.assertRaises(BridgeError):game.key('Enter',1)
            self.assertIsNotNone(game.fault)
            with self.assertRaisesRegex(BridgeError,'reset'):game.key('Enter',game.revision)
        bundle=load_bundle(EXAMPLE)
        bundle.update(instruction_limit=100000,source='''
for i=1,64 do pegasusBindPreviewKeys(function()
 local sum=0;for j=1,1000 do sum=sum+j end;ac.var.sum=sum;return false
end)end
''')
        game=PreviewGame(bundle)
        with self.assertRaisesRegex(BridgeError,'instruction limit'):game.key('Enter',1)

    def test_preview_close_clears_keys_and_suspended_keyboard_continuations(self):
        game=self.game('''
pegasusBindPreviewKeys(function()
 ac.waitForSeconds({delay=0.1});ac.var.should_not_run=true
end)
''',clock=lambda:0)
        game.key('Enter',1)
        self.assertTrue(game.snapshot()['timers'])
        game.close();game.close()
        state=game.snapshot()
        self.assertEqual(state['key_binding_count'],0)
        self.assertEqual(state['timers'],{})
        self.assertFalse(game.advance(1))
        self.assertNotIn('should_not_run',game.snapshot()['variables'])
        with self.assertRaises(BridgeError):game.key('Enter',game.revision)
        with self.assertRaises(BridgeError):game.click('anything',game.revision)

    def test_timers_are_one_shot_cancellable_ordered_and_not_recursively_drained(self):
        game=self.game('''
ac.var.order=''
local cancelled=CltTimer.SetTimeout(function()error('cancelled fired')end,0.01)
assert(CltTimer.ClearTimer(cancelled))
local later
CltTimer.SetTimeout(function()
 ac.var.order=ac.var.order..'A';CltTimer.ClearTimer(later)
 CltTimer.SetTimeout(function()ac.var.order=ac.var.order..'C'end,0)
end,0.1)
later=CltTimer.SetTimeout(function()ac.var.order=ac.var.order..'B'end,0.1)
''',clock=lambda:0)
        self.assertFalse(game.advance(0.09))
        self.assertTrue(game.advance(0.1))
        self.assertEqual(game.snapshot()['variables']['order'],'A')
        game.advance(0.1)
        self.assertEqual(game.snapshot()['variables']['order'],'AC')
        self.assertFalse(game.advance(10))

    def test_delayed_options_appear_while_video_is_frozen(self):
        game=self.game('''
ac.showVideo({name='v',res_id='$clip',events={{time=0,action='freeze',func=function()
 CltTimer.SetTimeout(function()
  ac.createButton({name='choice',text='跨过去',click_func=function()ac.var.chosen=true;ac.resumeVideo({name='v'})end})
 end,0.02)
end}}})
''',clock=lambda:0)
        game.video_event(dict(name='v',generation=1,kind='playing',time=0,duration=2))
        game.advance(0.019)
        self.assertNotIn('choice',game.snapshot()['objects'])
        game.advance(0.02)
        state=game.snapshot()
        self.assertEqual(state['videos']['v']['status'],'paused')
        state=game.click('choice',state['revision'])
        self.assertTrue(state['variables']['chosen'])
        self.assertEqual(state['videos']['v']['status'],'playing')

    def test_wait_for_seconds_suspends_and_resumes_the_same_coroutine(self):
        game=self.game("ac.waitForSeconds({delay=0.05});ac.var.resumed=true",clock=lambda:0)
        game.advance(0.049)
        self.assertNotIn('resumed',game.snapshot()['variables'])
        game.advance(0.05)
        self.assertTrue(game.snapshot()['variables']['resumed'])

    def test_timer_errors_and_instruction_budget_are_reported_without_crashing_service(self):
        for callback in ["error('timer failure')",'while true do end']:
            game=self.game('CltTimer.SetTimeout(function()'+callback+' end,0)',clock=lambda:0)
            self.assertTrue(game.advance(0))
            self.assertIsNotNone(game.snapshot()['fault'])
            self.assertFalse(game.advance(1))
            with self.assertRaisesRegex(BridgeError,'reset'):
                game.click('anything',game.revision)

    def test_timer_capacity_and_invalid_delays_fail_explicitly(self):
        for source in ['CltTimer.SetTimeout(function()end,-1)',
                       'CltTimer.SetTimeout(function()end,0/0)',
                       'for i=1,1001 do CltTimer.SetTimeout(function()end,10)end']:
            with self.assertRaises(BridgeError):
                self.game(source)

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

    def test_opt_in_text_load_retains_sandbox_and_real_callback(self):
        game=self.game('''
local program=assert(load("assert(os==nil and io==nil and python==nil and package==nil and debug==nil); ac.createButton({name='loaded',click_func=function()ac.var.loaded=true end})",'inner','t',_G))
program()
local bad,reason=load('this is not Lua','syntax','t')
assert(bad==nil and type(reason)=='string')
''',allow_text_load=True)
        state=game.snapshot()
        self.assertTrue(game.click('loaded',state['revision'])['variables']['loaded'])

    def test_text_load_rejects_binary_and_foreign_environments(self):
        self.game("local fn,err=load(string.char(27)..'Lua binary','binary','t');assert(fn==nil and err)",allow_text_load=True)
        for source in ["load('return 1','x','b')","load('return 1','x','t',{})"]:
            with self.assertRaises(BridgeError):self.game(source,allow_text_load=True)

    def test_text_load_size_and_compile_quota_are_bounded(self):
        for source in ["load(string.rep(' ',2097153))","for i=1,65 do load('return 1') end"]:
            with self.assertRaises(BridgeError):self.game(source,allow_text_load=True)

    def test_loaded_text_keeps_instruction_budget(self):
        with self.assertRaisesRegex(BridgeError,'instruction limit'):
            self.game("local fn=assert(load('while true do end'));fn()",allow_text_load=True)

    def test_json_null_metatable_and_protected_metatable_are_supported(self):
        game = self.game('''
local null=setmetatable({}, {__tostring=function()return 'null' end, __metatable='json_null'})
assert(getmetatable(null)=='json_null')
assert(not pcall(setmetatable,null,{}))
local defaults=setmetatable({}, {__index=function(_,key)return key..'_default' end})
ac.var.encoded=tostring(null)
ac.var.fallback=defaults.missing
assert(python==nil and os==nil and io==nil and require==nil and debug==nil and load==nil and package==nil)
''')
        state=game.snapshot()
        self.assertEqual(state['variables']['encoded'],'null')
        self.assertEqual(state['variables']['fallback'],'missing_default')

    def test_metatable_callbacks_remain_subject_to_instruction_budget(self):
        with self.assertRaisesRegex(BridgeError,'instruction limit'):
            self.game("local t=setmetatable({}, {__index=function()while true do end end}); local v=t.missing")

    def test_video_freeze_click_resume_natural_end_and_stale_generation(self):
        game=self.game('''
local function freeze()
 ac.var.frozen=true
 ac.createButton({name='continue',click_func=function()ac.remove({name='continue'});ac.resumeVideo({name='v'})end})
end
ac.showVideo({name='v',res_id='$clip',loop_type='next',events={{time=1,action='freeze',func=freeze}}})
ac.var.firstEnded=true
ac.showVideo({name='v2',res_id='$clip2',loop_type='next'})
''')
        self.assertNotIn('firstEnded',game.snapshot()['variables'])
        def event(kind,time,duration=2,generation=1,name='v'):
            return game.video_event(dict(name=name,generation=generation,kind=kind,time=time,duration=duration))
        event('playing',0)
        state=event('time',1.2)
        self.assertEqual(state['videos']['v']['status'],'paused')
        self.assertEqual(state['videos']['v']['time'],1)
        self.assertNotIn('firstEnded',state['variables'])
        game.click('continue',state['revision'])
        state=event('ended',2)
        self.assertTrue(state['variables']['firstEnded'])
        self.assertIn('v2',state['videos'])
        state=event('ended',2)
        self.assertIn('v2',state['videos'])
        self.assertNotIn('v',state['videos'])

    def test_close_video_resumes_waiting_lua_after_callback_finishes(self):
        game=self.game('''
ac.showVideo({name='v',res_id='$clip',events={{time=0,action='freeze',func=function()
 ac.createButton({name='jump',click_func=function()ac.closeVideo({name='v'});ac.var.callbackFinished=true end})
end}}})
assert(ac.var.callbackFinished)
ac.var.resumed=true
''')
        state=game.video_event(dict(name='v',generation=1,kind='playing',time=0,duration=2))
        state=game.click('jump',state['revision'])
        self.assertTrue(state['variables']['resumed'])

    def test_video_cannot_complete_without_browser_end_confirmation(self):
        game=self.game("ac.showVideo({name='v',res_id='$clip'});ac.var.ended=true")
        game.video_event(dict(name='v',generation=1,kind='playing',time=0,duration=5))
        with self.assertRaisesRegex(BridgeError,'duration/time'):
            game.video_event(dict(name='v',generation=1,kind='ended',time=1,duration=5))
        self.assertNotIn('ended',game.snapshot()['variables'])

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
    def test_preview_mix_http_readback_reset_and_session_isolation(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory)
            (root/'scene.lua').write_text("ac.createButton({name='mute',click_func=function()ac.var.readback=pegasusSetPreviewSettings({isMuted=true})end});ac.createButton({name='unmute',click_func=function()ac.var.readback=pegasusSetPreviewSettings({isMuted=false})end});ac.createButton({name='volume',click_func=function()ac.var.readback=pegasusSetPreviewSettings({volume=0.4})end})")
            manifest=root/'preview.json';manifest.write_text(json.dumps(dict(version=1,script='scene.lua')))
            service=PreviewService(root/'sessions')
            first=service.start(str(manifest));second=service.start(str(manifest))
            for started in (first,second):self.addCleanup(self._stop,service,started['session_id'])
            state=service.click(first['session_id'],'mute',1)
            self.assertEqual(state['mix_settings'],state['variables']['readback'])
            self.assertTrue(state['mix_settings']['isMuted'])
            self.assertFalse(service.state(second['session_id'])['mix_settings']['isMuted'])
            state=service.click(first['session_id'],'unmute',state['revision'])
            self.assertIs(state['mix_settings']['isMuted'],False)
            state=service.click(first['session_id'],'volume',state['revision'])
            self.assertEqual(state['mix_settings']['volume'],.4)
            state=service.reset(first['session_id'],state['revision'])
            self.assertEqual(state['mix_settings'],dict(isMuted=False,volume=1,videoVolume=1,musicVolume=1,fxVolume=1))
            self.assertNotIn('readback',state['variables'])

    def test_preview_keys_are_session_scoped_and_require_fresh_revision(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory)
            (root/'scene.lua').write_text("ac.var.count=0;pegasusBindPreviewKeys(function(key,repeated)ac.var.count=ac.var.count+1;ac.var.key=key;ac.var.repeated=repeated;return true end)")
            manifest=root/'preview.json';manifest.write_text(json.dumps(dict(version=1,script='scene.lua')))
            service=PreviewService(root/'sessions')
            first=service.start(str(manifest));second=service.start(str(manifest))
            for started in (first,second):self.addCleanup(self._stop,service,started['session_id'])
            state=service.key(first['session_id'],'ArrowDown',1,True)
            self.assertEqual(state['variables']['count'],1)
            self.assertTrue(state['variables']['repeated'])
            self.assertEqual(service.state(second['session_id'])['variables']['count'],0)
            with self.assertRaises(BridgeError):service.key(first['session_id'],'Enter',1)
            with self.assertRaises(BridgeError):service.key(first['session_id'],'unknown',2)
            with httpx.Client(trust_env=False)as client:
                for payload in [{'key':'Enter','revision':True},{'key':'Enter','revision':2,'repeat':1}]:
                    self.assertEqual(client.post(first['url']+'key',json=payload).status_code,400)
                self.assertEqual(client.post(first['url']+'key',json={'key':'Enter','revision':2},headers={'Origin':'https://evil.test'}).status_code,403)
                self.assertEqual(client.post(first['url'].replace('/'+first['url'].split('/')[-2]+'/','/'+'0'*48+'/')+'key',json={'key':'Enter','revision':2}).status_code,404)
            self.assertEqual(service.state(first['session_id'])['revision'],2)

    def test_preview_reset_stop_and_server_close_dispose_old_bindings(self):
        bundle=load_bundle(EXAMPLE)
        bundle['source']="pegasusBindPreviewKeys(function()ac.waitForSeconds({delay=60});ac.var.late=true end)"
        with PreviewHTTP(bundle)as server:
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                url=f'http://127.0.0.1:{server.server_port}{server.prefix}'
                previous=server.game
                with httpx.Client(trust_env=False)as client:
                    self.assertEqual(client.post(url+'key',json={'key':'Enter','revision':1}).status_code,200)
                    state=client.post(url+'reset',json={'revision':2}).json()
                    self.assertEqual(state['revision'],3)
                    self.assertEqual(state['key_binding_count'],1)
                    self.assertTrue(previous.closed)
                    self.assertEqual(previous.snapshot()['key_binding_count'],0)
                    self.assertEqual(previous.snapshot()['timers'],{})
                    self.assertFalse(previous.advance(time.monotonic()+120))
                    self.assertEqual(client.post(url+'key',json={'key':'Enter','revision':2}).status_code,400)
                    self.assertEqual(client.post(url+'stop',json={}).status_code,200)
                    self.assertTrue(server.game.closed)
                    self.assertEqual(server.game.snapshot()['key_binding_count'],0)
            finally:
                server.shutdown();worker.join(timeout=5)
        self.assertTrue(server.game.closed)

    def test_background_timer_runs_without_browser_video_events(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory)
            (root/'scene.lua').write_text("CltTimer.SetTimeout(function()ac.var.fired=true end,0.02)")
            manifest=root/'preview.json';manifest.write_text(json.dumps(dict(version=1,script='scene.lua')))
            service=PreviewService(root/'sessions');started=service.start(str(manifest))
            self.addCleanup(self._stop,service,started['session_id'])
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                state=service.state(started['session_id'])
                if state['variables'].get('fired'):break
                time.sleep(.02)
            self.assertTrue(state['variables'].get('fired'))
            self.assertIsNone(state['fault'])

    def test_video_range_requests_and_changed_file_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            raw=b'\x00\x00\x00\x18ftypisom'+bytes(range(256))*4
            video=root/'clip.mp4';video.write_bytes(raw)
            (root/'scene.lua').write_text("ac.showVideo({name='v',res_id='$clip'})")
            manifest=root/'preview.json'
            manifest.write_text(json.dumps(dict(version=1,script='scene.lua',video_assets={'$clip':'clip.mp4'})))
            service=PreviewService(root/'sessions');started=service.start(str(manifest))
            self.addCleanup(self._stop,service,started['session_id'])
            with httpx.Client(trust_env=False)as client:
                resources=client.get(started['url']+'resources.json').json()
                url=started['url']+resources['$clip']
                response=client.get(url,headers={'Range':'bytes=4-7'})
                self.assertEqual(response.status_code,206)
                self.assertEqual(response.content,b'ftyp')
                self.assertEqual(response.headers['content-range'],f'bytes 4-7/{len(raw)}')
                self.assertEqual(client.get(url,headers={'Range':'bytes=-8'}).content,raw[-8:])
                for value in ['bytes=999999-','bytes=1-0','bytes=0-1,4-5','bytes=-']:
                    self.assertEqual(client.get(url,headers={'Range':value}).status_code,416)
                video.write_bytes(b'changed')
                self.assertEqual(client.get(url).status_code,409)

    def test_lazy_image_and_audio_http_stream_exact_bytes_mime_range_and_file_change(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);image=root/'a.png';audio=root/'a.mp3'
            raw=bytes(range(256))*10;image.write_bytes(raw);audio.write_bytes(raw[::-1])
            (root/'scene.lua').write_text("ac.createImage({name='i',resId='$image'})")
            manifest=root/'preview.json';manifest.write_text(json.dumps(dict(version=1,script='scene.lua',lazy_assets=True,
                assets={'$image':'a.png','$audio':'a.mp3'})))
            service=PreviewService(root/'sessions');started=service.start(str(manifest))
            self.addCleanup(self._stop,service,started['session_id'])
            with httpx.Client(trust_env=False)as client:
                resources=client.get(started['url']+'resources.json').json()
                url=started['url']+resources['$image'];reply=client.get(url)
                self.assertEqual(reply.status_code,200);self.assertEqual(reply.content,raw)
                self.assertEqual(reply.headers['content-type'],'image/png')
                reply=client.get(started['url']+resources['$audio'],headers={'Range':'bytes=3-9'})
                self.assertEqual(reply.status_code,206);self.assertEqual(reply.content,raw[::-1][3:10])
                self.assertEqual(reply.headers['content-type'],'audio/mpeg')
                image.write_bytes(b'changed');self.assertEqual(client.get(url).status_code,409)
                audio.unlink();self.assertEqual(client.get(started['url']+resources['$audio']).status_code,409)

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
