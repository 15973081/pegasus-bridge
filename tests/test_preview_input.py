"""Bounded raw input, measured viewport and real-clock frame contracts."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

import httpx

from pegasus_bridge.core import BridgeError
from pegasus_bridge.preview import PreviewService
from pegasus_bridge.preview_runtime import PreviewGame,load_bundle


VIEWPORT = dict(left=10, top=20, width=960, height=540, scale=.5, pixelRatio=2,
                canvasWidth=1920, canvasHeight=1080, clientWidth=1000, clientHeight=640)
RECORDER = '''
assert(CS==nil and python==nil and os==nil and io==nil and require==nil and package==nil and debug==nil)
ac.var.events={}
pegasusBindPreviewInput(function(e)
 if e.kind~='viewport' then ac.var.events[#ac.var.events+1]=e end
end)
'''
HAS_LUA=importlib.util.find_spec('lupa') is not None


@unittest.skipUnless(HAS_LUA,'optional preview dependency not installed')
class RuntimeInputTests(unittest.TestCase):
    def game(self, source=RECORDER, limit=100000):
        self.clock = [100.]
        bundle = dict(source=source, variables={}, media={}, width=1920, height=1080,
                      title='Input fixture', script_sha256='fixture', instruction_limit=limit)
        game = PreviewGame(bundle, clock=lambda:self.clock[0])
        self.addCleanup(game.close)
        return game

    def payload(self, game, **event):
        state = game.snapshot()
        if event.get('kind')=='wheel':
            event.setdefault('x',100);event.setdefault('y',200)
        return dict(event, generation=state['input_generation'], sequence=state['input_sequence']+1)

    def send(self, game, **event):
        return game.input(self.payload(game, **event), game.revision)

    def viewport(self, game):
        return self.send(game, kind='viewport', **VIEWPORT)

    def pointer(self, game, phase, identity=1, **fields):
        return self.send(game, kind='pointer', phase=phase, id=identity,
                         pointer_type=fields.pop('pointer_type', 'mouse'),
                         x=fields.pop('x', 100), y=fields.pop('y', 200), **fields)

    def test_cursor_requires_real_viewport_and_keeps_contact_state_independent(self):
        game=self.game()
        with self.assertRaises(BridgeError):self.send(game,kind='cursor',phase='move',pointer_type='mouse',x=25,y=30)
        self.viewport(game)
        self.send(game,kind='cursor',phase='move',pointer_type='mouse',x=25,y=30)
        state=self.send(game,kind='cursor',phase='leave',pointer_type='pen',x=25,y=30)
        self.assertEqual([e['phase']for e in state['variables']['events']],['move','leave'])
        self.assertEqual(state['pointer_contact_count'],0)
        self.pointer(game,'down');self.send(game,kind='cursor',phase='move',pointer_type='mouse',x=27,y=31)
        self.assertEqual(game.snapshot()['pointer_contact_count'],1)
        self.pointer(game,'up')

    def test_cursor_rejects_synthetic_touch_hover_stale_owners_and_invalid_coordinates(self):
        game=self.game();self.viewport(game)
        event=dict(kind='cursor',phase='move',pointer_type='mouse',x=10,y=20)
        for extra in [dict(pointer_type='touch'),dict(phase='down'),dict(x=-1),dict(y=float('nan')),dict(extra=True)]:
            with self.subTest(extra=extra),self.assertRaises(BridgeError):self.send(game,**dict(event,**extra))
        payload=self.payload(game,**event);self.send(game,**event)
        with self.assertRaises(BridgeError):game.input(payload,game.revision)
        old=game.snapshot()['input_generation'];game=self.game();self.viewport(game)
        self.assertNotEqual(old,game.snapshot()['input_generation'])
        with self.assertRaises(BridgeError):game.input(payload,game.revision)

    def test_viewport_nil_until_reported_and_lua_copy_cannot_mutate_bus(self):
        game = self.game(RECORDER + '''
assert(pegasusReadPreviewViewport()==nil)
ac.createButton({name='inspect',click_func=function()
 local v=pegasusReadPreviewViewport();v.width=1
 ac.var.width=pegasusReadPreviewViewport().width
end})
''')
        self.assertIsNone(game.snapshot()['viewport'])
        self.viewport(game)
        self.assertEqual(game.snapshot()['viewport'], VIEWPORT)
        game.click('inspect', game.revision)
        self.assertEqual(game.snapshot()['variables']['width'], 960)

    def test_actual_pointer_down_move_up_sequence_and_clock_metadata(self):
        game = self.game();self.viewport(game)
        self.pointer(game, 'down')
        self.clock[0] += .03;game.advance()
        self.pointer(game, 'move', x=150)
        state = self.pointer(game, 'up', x=180)
        events = state['variables']['events']
        self.assertEqual([e['phase'] for e in events], ['down','move','up'])
        self.assertEqual([e['x'] for e in events], [100,150,180])
        self.assertGreater(events[1]['time'], events[0]['time'])
        self.assertGreater(events[1]['frame'], events[0]['frame'])
        self.assertEqual(state['pointer_contact_count'], 0)

    def test_pointer_requires_viewport_down_identity_and_matching_type(self):
        game = self.game()
        with self.assertRaisesRegex(BridgeError, 'viewport'):self.pointer(game,'down')
        self.viewport(game)
        for phase in ('move','up','cancel'):
            with self.assertRaises(BridgeError):self.pointer(game,phase)
        self.pointer(game,'down')
        with self.assertRaises(BridgeError):self.pointer(game,'down')
        with self.assertRaises(BridgeError):self.pointer(game,'move',pointer_type='touch')
        self.pointer(game,'cancel')
        self.assertEqual(game.snapshot()['pointer_contact_count'],0)
        self.assertIsNone(game.fault)

    def test_ten_contacts_are_not_fabricated_into_one_gesture(self):
        game=self.game();self.viewport(game)
        for identity in range(10):self.pointer(game,'down',identity,pointer_type='touch')
        with self.assertRaises(BridgeError):self.pointer(game,'down',10,pointer_type='touch')
        self.assertEqual(game.snapshot()['pointer_contact_count'],10)
        for identity in range(10):self.pointer(game,'cancel',identity,pointer_type='touch')
        self.assertEqual(game.snapshot()['pointer_contact_count'],0)

    def test_stale_revision_generation_and_sequence_are_non_faulting(self):
        game=self.game();self.viewport(game)
        event=self.payload(game,kind='wheel',deltaX=1,deltaY=2,deltaMode=0)
        for bad in [dict(event,generation='old'),dict(event,sequence=True),dict(event,sequence=event['sequence']+1)]:
            with self.assertRaises(BridgeError):game.input(bad,game.revision)
        with self.assertRaises(BridgeError):game.input(event,game.revision-1)
        game.input(event,game.revision)
        with self.assertRaisesRegex(BridgeError,'sequence'):game.input(event,game.revision)
        self.assertIsNone(game.fault)

    def test_strict_schema_rejects_unknown_nonfinite_boolean_and_out_of_bounds(self):
        game=self.game();self.viewport(game)
        original=self.payload(game,kind='pointer',phase='down',id=1,pointer_type='mouse',x=1,y=1)
        invalid=[None,[],dict(original,extra=1),dict(original,id=True),dict(original,x=True),
                 dict(original,x=float('nan')),dict(original,y=float('inf')),dict(original,x=1920.01),
                 dict(original,y=-1),dict(original,phase='click'),dict(original,pointer_type='fake')]
        before=game.snapshot()
        for event in invalid:
            with self.subTest(event=event):
                with self.assertRaises(BridgeError):game.input(event,game.revision)
        after=game.snapshot()
        self.assertEqual(after['revision'],before['revision'])
        self.assertEqual(after['input_sequence'],before['input_sequence'])
        self.assertEqual(after['pointer_contact_count'],0)
        for field,value in [('scale',1),('pixelRatio',False),('width',0),('canvasWidth',99999)]:
            bad=dict(VIEWPORT);bad[field]=value
            with self.assertRaises(BridgeError):self.send(game,kind='viewport',**bad)

    def test_lifo_consumption_and_callback_copy_are_isolated(self):
        game=self.game('''
ac.var.trace={}
pegasusBindPreviewInput(function(e)ac.var.trace[#ac.var.trace+1]='base:'..e.kind end)
pegasusBindPreviewInput(function(e)ac.var.trace[#ac.var.trace+1]='modal:'..e.kind;e.kind='mutated';return true end)
''')
        state=self.send(game,kind='wheel',deltaX=1,deltaY=2,deltaMode=0)
        self.assertEqual(state['variables']['trace'],['modal:wheel'])
        game2=self.game('''
pegasusBindPreviewInput(function(e)ac.var.seen=e.kind end)
pegasusBindPreviewInput(function(e)e.kind='mutated' end)
''')
        self.assertEqual(self.send(game2,kind='wheel',deltaX=0,deltaY=1,deltaMode=0)['variables']['seen'],'wheel')

    def test_binding_limits_cancellation_and_sandbox_are_shared(self):
        game=self.game('''
for _,bind in ipairs({pegasusBindPreviewInput,pegasusBindPreviewFrames})do
 local list={};for i=1,64 do list[i]=bind(function()end)end
 assert(not pcall(bind,function()end))
 for _,cancel in ipairs(list)do assert(cancel());assert(not cancel())end
 local cancel=bind(function()end);assert(cancel())
end
assert(CS==nil and os==nil and python==nil and require==nil)
''')
        self.assertEqual(game.snapshot()['input_binding_count'],0)
        self.assertEqual(game.snapshot()['frame_binding_count'],0)

    def test_input_queue_preserves_order_across_real_scheduler_yields(self):
        game=self.game('''
ac.var.events={}
pegasusBindPreviewInput(function(e)
 if e.kind=='pointer' then
  if e.phase=='down' then ac.waitForSeconds({delay=.1})end
  ac.var.events[#ac.var.events+1]=e.phase
 end
end)
''');self.viewport(game)
        self.pointer(game,'down');self.pointer(game,'move');self.pointer(game,'up')
        self.assertEqual(game.snapshot()['input_pending_count'],3)
        self.clock[0]+=.2;game.advance()
        self.assertEqual(game.snapshot()['variables']['events'],['down','move','up'])
        self.assertEqual(game.snapshot()['input_pending_count'],0)

    def test_old_modal_generation_drops_queued_and_suspended_callbacks(self):
        game=self.game('''
pegasusBindPreviewInput(function(e)if e.kind=='pointer' and e.phase=='down' then
 ac.waitForSeconds({delay=1});ac.var.late=true end end)
ac.createButton({name='modal',click_func=function()pegasusBindPreviewInput(function()return true end)end})
''');self.viewport(game)
        old=self.payload(game,kind='pointer',phase='move',id=1,pointer_type='mouse',x=100,y=100)
        self.pointer(game,'down');self.pointer(game,'move')
        game.click('modal',game.revision)
        self.clock[0]+=2;game.advance()
        self.assertNotIn('late',game.snapshot()['variables'])
        self.assertEqual(game.snapshot()['pointer_contact_count'],0)
        self.assertEqual(game.snapshot()['input_pending_count'],0)
        with self.assertRaisesRegex(BridgeError,'generation'):game.input(old,game.revision)

    def test_hover_matches_visible_current_button_generation_and_leave(self):
        game=self.game(RECORDER+'''
ac.createButton({name='target',click_func=function()end})
ac.createButton({name='replace',click_func=function()
 ac.remove({name='target'});ac.createButton({name='target',click_func=function()end})end})
''')
        generation=game.snapshot()['objects']['target']['preview_generation']
        self.send(game,kind='hover',phase='enter',name='target',button_generation=generation)
        self.assertEqual(game.snapshot()['hover_count'],1)
        self.send(game,kind='hover',phase='leave',name='target',button_generation=generation)
        with self.assertRaises(BridgeError):self.send(game,kind='hover',phase='leave',name='target',button_generation=generation)
        game.click('replace',game.revision)
        self.assertNotEqual(generation,game.snapshot()['objects']['target']['preview_generation'])
        with self.assertRaisesRegex(BridgeError,'同名替换'):
            self.send(game,kind='hover',phase='enter',name='target',button_generation=generation)
        with self.assertRaises(BridgeError):self.send(game,kind='hover',phase='enter',name='absent',button_generation=1)
        self.assertIsNone(game.fault)

    def test_hidden_parent_hover_is_rejected_without_lua_fault(self):
        game=self.game(RECORDER+"ac.createLayer({name='hidden',visible=false});ac.createButton({name='child',inlayer='hidden',click_func=function()end})")
        generation=game.snapshot()['objects']['child']['preview_generation']
        with self.assertRaises(BridgeError):self.send(game,kind='hover',phase='enter',name='child',button_generation=generation)
        self.assertIsNone(game.fault)

    def test_wheel_retains_raw_units_and_is_not_a_pointer(self):
        game=self.game()
        state=self.send(game,kind='wheel',deltaX=-20.5,deltaY=120,deltaMode=1)
        event=state['variables']['events'][0]
        self.assertEqual((event['deltaX'],event['deltaY'],event['deltaMode']),(-20.5,120,1))
        self.assertEqual(state['pointer_contact_count'],0)
        with self.assertRaises(BridgeError):self.send(game,kind='wheel',deltaX=0,deltaY=1,deltaMode=True)

    def test_wheel_measured_coordinates_are_required_and_strict_in_http_and_mcp(self):
        from pegasus_bridge.server import _PreviewWheel
        from pydantic import ValidationError
        game=self.game()
        good=self.payload(game,kind='wheel',x=345.5,y=678,deltaX=0,deltaY=120,deltaMode=0)
        state=game.input(good,game.revision)
        self.assertEqual((state['variables']['events'][0]['x'],state['variables']['events'][0]['y']),(345.5,678))
        self.assertEqual(_PreviewWheel.model_validate(good).x,345.5)
        for change in [dict(x=True),dict(y=float('nan')),dict(x=1921),dict(y=-1),dict(x='12'),dict(extra=1)]:
            event=dict(good,**change);event['sequence']=game.snapshot()['input_sequence']+1
            with self.assertRaises(BridgeError):game.input(event,game.revision)
            with self.assertRaises(ValidationError):_PreviewWheel.model_validate(event)
        for missing in ('x','y'):
            event=dict(good);event.pop(missing);event['sequence']=game.snapshot()['input_sequence']+1
            with self.assertRaises(BridgeError):game.input(event,game.revision)
            with self.assertRaises(ValidationError):_PreviewWheel.model_validate(event)

    def test_frames_use_monotonic_pump_not_inputs_or_catchup(self):
        game=self.game('''ac.var.frames={};pegasusBindPreviewFrames(function(t,n)
 assert(t==pegasusPreviewNow());ac.var.frames[#ac.var.frames+1]={time=t,frame=n} end)''')
        revision=game.revision
        self.clock[0]+=.02;self.assertTrue(game.advance())
        self.clock[0]+=.001;self.assertFalse(game.advance())
        self.clock[0]+=3;self.assertTrue(game.advance())
        events=game.snapshot()['variables']['frames']
        self.assertEqual(len(events),2)
        self.assertEqual(events[1]['frame'],events[0]['frame']+1)
        self.assertGreater(events[1]['time']-events[0]['time'],3)
        self.assertEqual(game.revision,revision)

    def test_cancelled_suspended_frame_cannot_resume_or_leak_timer(self):
        game=self.game('''local cancel
cancel=pegasusBindPreviewFrames(function()ac.var.started=true;ac.waitForSeconds({delay=1});ac.var.late=true end)
ac.createButton({name='stop',click_func=function()assert(cancel());assert(not cancel())end})''')
        self.clock[0]+=.02;game.advance()
        self.assertTrue(game.snapshot()['variables']['started'])
        game.click('stop',game.revision)
        self.assertEqual(game.snapshot()['frame_binding_count'],0)
        self.assertEqual(game.snapshot()['timers'],{})
        self.clock[0]+=2;game.advance()
        self.assertNotIn('late',game.snapshot()['variables'])

    def test_frame_and_input_instruction_budget_stop_uncaught_loops(self):
        game=self.game('pegasusBindPreviewFrames(function()while true do end end)')
        self.clock[0]+=.02;self.assertTrue(game.advance())
        self.assertIn('instruction limit',game.fault)
        game2=self.game('pegasusBindPreviewInput(function()while true do end end)')
        with self.assertRaisesRegex(BridgeError,'instruction limit'):
            self.send(game2,kind='wheel',deltaX=0,deltaY=1,deltaMode=0)

    def test_callbacks_in_one_batch_share_budget(self):
        callback='function()local n=0;for i=1,10000 do n=n+i end end'
        one=self.game('pegasusBindPreviewInput('+callback+')')
        self.send(one,kind='wheel',deltaX=0,deltaY=1,deltaMode=0)
        many=self.game('for i=1,10 do pegasusBindPreviewInput('+callback+')end')
        with self.assertRaisesRegex(BridgeError,'instruction limit'):
            self.send(many,kind='wheel',deltaX=0,deltaY=1,deltaMode=0)
        frames=self.game('for i=1,10 do pegasusBindPreviewFrames('+callback+')end')
        self.clock[0]+=.02;frames.advance()
        self.assertIn('instruction limit',frames.fault)

    def test_input_queue_bound_cancels_without_ghost_waiter(self):
        game=self.game('''local cancel
cancel=pegasusBindPreviewInput(function(e)if e.kind=='pointer' and e.phase=='down' then ac.waitForSeconds({delay=100});ac.var.late=true end end)
ac.createButton({name='stop',click_func=function()cancel()end})''');self.viewport(game);self.pointer(game,'down')
        for _ in range(127):self.send(game,kind='wheel',deltaX=0,deltaY=1,deltaMode=0)
        self.assertEqual(game.snapshot()['input_pending_count'],128)
        with self.assertRaisesRegex(BridgeError,'队列'):self.send(game,kind='wheel',deltaX=0,deltaY=1,deltaMode=0)
        game.click('stop',game.revision)
        self.assertEqual(game.snapshot()['input_pending_count'],0)
        self.assertEqual(game.snapshot()['timers'],{})
        self.clock[0]+=101;game.advance()
        self.assertNotIn('late',game.snapshot()['variables'])

    def test_layer_position_owner_generation_and_frame_fastpath(self):
        game=self.game('''
ac.createLayer({name='world',pos={960,540},size={1920,1080}})
local original=pegasusReadPreviewObjectPosition('world')
ac.var.setter=pegasusSetPreviewObjectPosition
pegasusBindPreviewFrames(function()
 local p=pegasusReadPreviewObjectPosition('world');p.x=100
 local readback=pegasusSetPreviewObjectPosition('world',{x=100,y=200,generation=p.generation})
 readback.x=999;ac.var.x=pegasusReadPreviewObjectPosition('world').x
end)
ac.createButton({name='replace',click_func=function()
 ac.remove({name='world'});ac.createLayer({name='world',pos={0,0}})
 assert(not pcall(pegasusSetPreviewObjectPosition,'world',original))
 assert(pegasusReadPreviewObjectPosition('replace')==nil)
 assert(not pcall(pegasusSetPreviewObjectPosition,'replace',{x=1,y=2,generation=1}))
end})
''')
        before=game.snapshot();self.clock[0]+=.02;game.advance();after=game.snapshot()
        self.assertEqual(after['objects']['world']['pos'],[100,200])
        self.assertEqual(after['variables']['x'],100)
        self.assertEqual(before['input_generation'],after['input_generation'])
        self.assertEqual(before['render_generation'],after['render_generation'])
        self.assertGreater(after['position_generation'],before['position_generation'])
        game.click('replace',game.revision)
        setter=game.runtime.variables['setter'];game.close()
        with self.assertRaisesRegex(Exception,'closed'):setter('world',game.lua.table_from(dict(x=1,y=1,generation=1)))

    def test_layer_position_rejects_nonfinite_unknown_fields_and_missing_generation(self):
        self.game('''ac.createLayer({name='world',pos={1,2}})
local p=pegasusReadPreviewObjectPosition('world')
for _,v in ipairs({{x=0/0,y=1,generation=p.generation},{x=1,y=math.huge,generation=p.generation},
 {x=1,y=2},{x=1,y=2,generation=p.generation,extra=true}})do
 assert(not pcall(pegasusSetPreviewObjectPosition,'world',v))end
assert(pegasusReadPreviewObjectPosition('world').x==1)''')

    def test_video_helpers_read_safe_bus_and_only_pause_existing_video(self):
        game=self.game('''
ac.createButton({name='pause',click_func=function()
 local v=pegasusSetPreviewVideoPaused('clip',true);assert(v.status=='paused')
 v.time=999;assert(pegasusReadPreviewVideo('clip').time==0)
 assert(not pcall(pegasusSetPreviewVideoPaused,'absent',true))
 assert(not pcall(pegasusSetPreviewVideoPaused,'clip',0))
end})
ac.createButton({name='resume',click_func=function()assert(pegasusSetPreviewVideoPaused('clip',false).status=='playing')end})
assert(pegasusReadPreviewVideo('clip')==nil)
ac.showVideo({name='clip',res_id='$clip'})''')
        game.click('pause',game.revision);self.assertEqual(game.snapshot()['videos']['clip']['status'],'paused')
        game.click('resume',game.revision);self.assertEqual(game.snapshot()['videos']['clip']['status'],'playing')
        self.assertIsNone(game.snapshot()['videos']['clip'].get('duration'))

    def test_resize_releases_real_contacts_and_does_not_replay_old_down(self):
        game=self.game();self.viewport(game);self.pointer(game,'down')
        old=self.payload(game,kind='pointer',phase='down',id=2,pointer_type='mouse',x=1,y=1)
        self.send(game,kind='viewport',**dict(VIEWPORT,width=768,height=432,scale=.4))
        state=game.snapshot()
        self.assertEqual(state['pointer_contact_count'],0)
        self.assertEqual(state['variables']['events'][-1]['phase'],'cancel')
        self.assertEqual(state['variables']['events'][-1]['reason'],'viewport_changed')
        with self.assertRaisesRegex(BridgeError,'generation'):game.input(old,game.revision)

    def test_close_disposes_every_input_and_frame_lifetime(self):
        game=self.game(RECORDER+'pegasusBindPreviewFrames(function()end)')
        self.viewport(game);self.pointer(game,'down');game.close()
        state=game.snapshot()
        for key in ('input_binding_count','frame_binding_count','pointer_contact_count','input_pending_count'):
            self.assertEqual(state[key],0)
        self.assertIsNone(state['viewport'])
        with self.assertRaises(BridgeError):game.input({},game.revision)


@unittest.skipUnless(HAS_LUA,'optional preview dependency not installed')
class HTTPInputTests(unittest.TestCase):
    def test_idle_frames_http_validation_reset_nonce_and_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'scene.lua').write_text(RECORDER+"pegasusBindPreviewFrames(function(t,n)ac.var.frame=n;ac.var.time=t end)",encoding='utf-8')
            manifest=root/'preview.json';manifest.write_text(json.dumps(dict(version=1,script='scene.lua')),encoding='utf-8')
            service=PreviewService(root/'sessions');started=service.start(str(manifest));ident=started['session_id']
            try:
                state=service.state(ident);initial=state['preview_frame']
                time.sleep(.08)
                state=service.state(ident)
                self.assertGreater(state['preview_frame'],initial)
                event=dict(kind='viewport',**VIEWPORT,generation=state['input_generation'],sequence=1)
                state=service.input(ident,event,state['revision']);old=dict(event)
                self.assertEqual(state['viewport'],VIEWPORT)
                with httpx.Client(trust_env=False) as client:
                    self.assertEqual(client.post(started['url']+'input',json={'event':event,'revision':state['revision']},headers={'Origin':'https://evil.test'}).status_code,403)
                    self.assertEqual(client.post(started['url']+'input',json={'event':event,'revision':state['revision'],'extra':1}).status_code,400)
                state=service.reset(ident,state['revision'])
                self.assertNotEqual(state['input_generation'],old['generation'])
                self.assertIsNone(state['viewport'])
                self.assertEqual(state['input_sequence'],0)
                old['sequence']=1
                with self.assertRaisesRegex(BridgeError,'generation'):service.input(ident,old,state['revision'])
            finally:
                service.stop(ident)
            for _ in range(100):
                try:service.state(ident)
                except BridgeError:break
                time.sleep(.01)
            else:self.fail('Stopped owned preview remained reachable')


class AssetBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'scene.lua').write_text('ac.var.x=1',encoding='utf-8')
        (self.root/'image.png').write_bytes(b'png-fixture')

    def manifest(self,**fields):
        p=self.root/'preview.json';p.write_text(json.dumps(dict(version=1,script='scene.lua',**fields)),encoding='utf-8');return p

    def test_asset_count_opt_in_keeps_default_and_has_finite_ceiling(self):
        assets={str(i):'image.png' for i in range(1001)}
        with self.assertRaises(BridgeError):load_bundle(self.manifest(assets=assets))
        bundle=load_bundle(self.manifest(assets=assets,asset_count_limit=1001))
        self.assertEqual(len(bundle['media']),1001)
        self.assertEqual(bundle['asset_total_limit_mib'],128)
        assets={str(i).rjust(200,'a'):'image.png' for i in range(8192)}
        self.assertEqual(len(load_bundle(self.manifest(assets=assets,asset_count_limit=8192))['media']),8192)

    def test_asset_byte_budget_is_actual_and_explicit(self):
        (self.root/'big.png').write_bytes(b'x'*(600*1024))
        assets={'a':'big.png','b':'big.png'}
        with self.assertRaises(BridgeError):load_bundle(self.manifest(assets=assets,asset_total_limit_mib=1))
        result=load_bundle(self.manifest(assets=assets,asset_total_limit_mib=2))
        self.assertEqual(sum(len(v[0]) for v in result['media'].values()),1200*1024)

    def test_invalid_limits_and_paths_remain_rejected(self):
        for fields in [dict(asset_count_limit=True),dict(asset_count_limit=0),dict(asset_count_limit=8193),
                       dict(asset_total_limit_mib=513),dict(asset_total_limit_mib='512'),dict(asset_total_limit_mib=float('nan'))]:
            with self.subTest(fields=fields):
                with self.assertRaises(BridgeError):load_bundle(self.manifest(**fields))
        nested=self.root/'nested';nested.mkdir();(nested/'scene.lua').write_text('ac.var.x=1')
        p=nested/'preview.json';p.write_text(json.dumps(dict(version=1,script='scene.lua',assets={'a':'../image.png'},asset_count_limit=8192,asset_total_limit_mib=512)))
        with self.assertRaises(BridgeError):load_bundle(p)


if __name__=='__main__':
    unittest.main()
