"""Opt-in trusted Chromium input against the real PreviewHTTP/Lua frontend."""
import base64
import importlib.util
import os
import threading
import time
import unittest

from pegasus_bridge.preview import PreviewHTTP


SOURCE = '''
ac.var.clicks=0;ac.var.newClicks=0;ac.var.enters=0;ac.var.leaves=0;ac.var.patches=0;ac.var.frames=0
ac.var.cursorMoves=0;ac.var.cursorLeaves=0
local cancelKeys
ac.createButton({name='arm_keys',text='Arm key owner replacement',pos={1600,900},size={250,50},click_func=function()ac.var.swapKeys=true end})
local layer={name='parent',pos={960,540},size={1920,1080}}
local other={name='other',pos={1080,540},size={1920,1080}}
ac.createLayer(layer);ac.createLayer(other)
local marker={name='marker',resId='$red',pos={80,80},size={40,40}}
ac.createImage(marker)
local target={name='target',inlayer='parent',normal_resId='$red',hover_resId='$blue',pos={400,650},size={200,90},
 click_func=function()ac.var.clicks=ac.var.clicks+1 end}
ac.createButton(target)
local cancelInput=pegasusBindPreviewInput(function(event)
 if event.kind=='pointer'and event.phase=='up'and ac.var.swapKeys then
  ac.var.swapKeys=false;cancelKeys()
  cancelKeys=pegasusBindPreviewKeys(function()ac.var.replacementKeys=(ac.var.replacementKeys or 0)+1;return true end)
 end
 if event.kind=='cursor'then
  if event.phase=='move'then ac.var.cursorMoves=ac.var.cursorMoves+1;ac.var.cursorX=event.x;ac.var.cursorY=event.y
  elseif event.phase=='leave'then ac.var.cursorLeaves=ac.var.cursorLeaves+1 end
 end
 if event.kind=='hover' and event.name=='target' then
  if event.phase=='enter' then
   ac.var.enters=ac.var.enters+1;marker.resId='$blue';ac.createImage(marker)
  else ac.var.leaves=ac.var.leaves+1 end
 end
end)
pegasusBindPreviewFrames(function()
 ac.var.frames=ac.var.frames+1
 if ac.var.frames%4==0 and marker then
  ac.var.frameDraws=(ac.var.frameDraws or 0)+1;ac.createImage(marker)
 end
end)
cancelKeys=pegasusBindPreviewKeys(function(key)
 if key=='1' then
  ac.var.patches=ac.var.patches+1;marker.resId=marker.resId=='$red'and'$blue'or'$red';ac.createImage(marker)
  local p=pegasusReadPreviewObjectPosition('parent');p.x=p.x+1;pegasusSetPreviewObjectPosition('parent',p)
 elseif key=='2' then ac.remove({name='parent'})
 elseif key=='3' then target.inlayer='other'
 elseif key=='4' then local p=target.inlayer=='other'and other or layer;p.visible=p.visible==false
 elseif key=='5' then
  target={name='target',inlayer='parent',normal_resId='$red',hover_resId='$blue',pos={400,650},size={200,90},
   click_func=function()ac.var.newClicks=ac.var.newClicks+1 end};ac.createButton(target)
 elseif key=='ArrowDown' then target.normal_resId='$blue';target.hover_resId='$red';target.btn_text='Updated label'
 elseif key=='ArrowUp' then ac.remove({name='marker'});ac.createText({name='marker',content='Source text',pos={80,80},size={100,40}});marker=nil
 elseif key=='Enter' then ac.remove({name='target'});target.name='renamed';ac.createButton(target)
 elseif key=='Escape' then cancelInput()
 elseif key==' 'then
  if target.normal_resId then target.normal_resId=nil;target.hover_resId=nil;target.btn_text='Text button'
  else target.normal_resId='$red';target.hover_resId='$blue'end
 end
 return true
end)
'''

# Two fixed, valid one-pixel PNGs; no image generation or external media dependency.
TEXT_COLOR_SOURCE = '''
ac.createStyle({name='red_text',color='#A30000'})
local directText={name='text_direct',textStyle='red_text',color='#12AbEf',content='Direct color',pos={1700,180},size={180,30}}
ac.createText(directText)
ac.createText({name='text_snake',text_style='red_text',content='Snake style',pos={1700,140},size={180,30}})
ac.createText({name='text_pascal',TextStyle='red_text',content='Pascal style',pos={1700,100},size={180,30}})
ac.createText({name='text_array',textStyle='red_text',color={255,255,255},content='Resolved style',pos={1700,60},size={180,30}})
ac.createButton({name='recolor_text',text='Change text color',pos={1000,900},size={220,40},click_func=function()
 directText.color=directText.color=='#12AbEf'and'#FFFFFF'or'';ac.createText(directText)
end})
'''
TEXT_RICH_SOURCE = '''
ac.createStyle({name='rich_base',bold=true,color='#FFFFFF'})
local rich={name='text_rich',textStyle='rich_base',content='中文 <b>Bold <i>Italic</i></b> <u>Under <s>Strike</s></u>',pos={1300,350},size={900,90}}
ac.createText(rich)
ac.createText({name='text_literal',content='<img src="http://127.0.0.1:1/richtext-must-not-load" onerror="window.richTextExecuted=1"> <script>window.richTextExecuted=1</script> <b onclick="window.richTextExecuted=1">literal</b>',pos={1300,200},size={900,90}})
local changes=0
ac.createButton({name='rewrite_rich_text',text='Change rich text',pos={1000,900},size={220,40},click_func=function()
 changes=changes+1;rich.content=changes==1 and '<i>Changed</i>'or'Plain < 2';ac.createText(rich)
end})
'''
BUTTON_LABEL_SOURCE = '''
ac.createStyle({name='button_base',color='#A30000',font_size=24})
ac.var.buttonLabelClicks=0
local labeled={name='art_label',normal_resId='$red',hover_resId='$blue',btn_text='继续剧情',
 textStyle='button_base',font_size=36,color='#FFFFFF',pos={1500,200},size={300,80},
 click_func=function()ac.var.buttonLabelClicks=ac.var.buttonLabelClicks+1 end}
ac.createButton(labeled)
ac.createButton({name='raster_owned',normal_resId='$red',btn_text='',text='',pos={1500,100},size={300,60},click_func=function()end})
ac.createButton({name='update_art_label',text='Update artwork label',pos={1200,900},size={300,50},click_func=function()
 labeled.btn_text='Changed label';labeled.color='';labeled.font_size=nil;labeled.normal_resId=nil;labeled.hover_resId=nil
end})
'''
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=')
HAS_DEPS = all(importlib.util.find_spec(name) is not None for name in ('lupa', 'playwright'))


@unittest.skipUnless(os.environ.get('B13_DOM_SMOKE') == '1' and HAS_DEPS,
                     'opt-in actual Chromium DOM reconciliation smoke (B13_DOM_SMOKE=1)')
class PreviewDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            executable_path=os.environ.get('PEGASUS_PREVIEW_BROWSER_EXECUTABLE',
                                           r'C:/Users/admin/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe'),
            headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        extra = {'test_native_text_color_override_style_aliases_and_real_click_updates': TEXT_COLOR_SOURCE,
                 'test_basic_tmp_formatting_stays_literal_for_other_markup_and_retains_nodes': TEXT_RICH_SOURCE,
                 'test_native_artwork_button_keeps_label_color_style_and_trusted_click': BUTTON_LABEL_SOURCE}
        source = SOURCE + extra.get(self._testMethodName, '')
        self.server = PreviewHTTP(dict(source=source, variables={}, media={'$red': (PNG, 'image/png'), '$blue': (PNG, 'image/png')},
                                       width=1920, height=1080, title='DOM reconciliation fixture', script_sha256='fixture', instruction_limit=100000))
        self.stopped = threading.Event()
        # Match preview.serve's real-clock scheduler; serve_forever alone never advances Lua frames.
        def run_server():
            while not self.stopped.is_set():
                self.server.handle_request()
                with self.server.game_lock:
                    self.server.game.advance()
        self.thread = threading.Thread(target=run_server, daemon=True)
        self.thread.start()
        self.page = self.browser.new_page(viewport=dict(width=1280, height=800))
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}{self.server.prefix}')
        self.wait("state?.objects.target && state.viewport && !busy && inputQueued===0")

    def tearDown(self):
        self.page.close()
        self.stopped.set()
        self.thread.join(timeout=5)
        self.server.server_close()

    def wait(self, expression):
        # JavaScript only observes real DOM/state; all input below uses Chromium's trusted APIs.
        # Avoid wait_for_function's string eval: PreviewHTTP intentionally forbids unsafe-eval.
        for _ in range(240):
            if self.page.evaluate('()=>Boolean(' + expression + ')'):
                return
            time.sleep(.025)
        self.fail('DOM condition not reached: ' + expression + '; ' + str(self.state()))

    def state(self):
        return self.page.evaluate('state')

    def same(self, handle, selector):
        return self.page.evaluate('(args)=>args.old===document.querySelector(args.selector)', dict(old=handle, selector=selector))

    def center(self, selector='#target'):
        rect = self.page.locator(selector).bounding_box()
        self.assertIsNotNone(rect)
        return rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2

    def hover(self):
        self.page.mouse.move(*self.center())
        self.wait('state.variables.enters>=1 && !busy && inputQueued===0')

    def key(self, key, expression):
        self.page.keyboard.press(key)
        self.wait(expression + ' && !busy && inputQueued===0')

    def assert_clean(self):
        self.assertEqual(self.errors, [])
        self.assertIsNone(self.state()['fault'])
        self.assertEqual(self.page.locator('#ui .node').count(), len(self.state()['objects']))

    def test_native_text_color_override_style_aliases_and_real_click_updates(self):
        self.wait('state.objects.text_array && !busy && inputQueued===0')
        color = lambda selector: self.page.locator(selector).evaluate('(el)=>getComputedStyle(el).color')
        self.assertEqual(color('#text_direct'), 'rgb(18, 171, 239)')
        for selector in ('#text_snake', '#text_pascal', '#text_array'):
            self.assertEqual(color(selector), 'rgb(163, 0, 0)')
        original = self.page.locator('#text_direct').element_handle()
        self.page.locator('#recolor_text').click()
        self.wait("state.objects.text_direct.color==='#FFFFFF' && !busy && inputQueued===0")
        self.assertEqual(color('#text_direct'), 'rgb(255, 255, 255)')
        self.assertTrue(self.same(original, '#text_direct'))
        self.page.locator('#recolor_text').click()
        self.wait("state.objects.text_direct.color==='' && !busy && inputQueued===0")
        self.assertEqual(color('#text_direct'), 'rgb(163, 0, 0)')
        self.assertTrue(self.same(original, '#text_direct'))
        self.assert_clean()

    def test_native_artwork_button_keeps_label_color_style_and_trusted_click(self):
        self.wait('state.objects.art_label && !busy && inputQueued===0')
        button = self.page.locator('#art_label')
        owner = button.element_handle()
        label = button.locator('.button-label').element_handle()
        artwork = button.locator('img').element_handle()
        self.assertEqual(button.inner_text(), '继续剧情')
        self.assertEqual(button.locator('img').count(), 1)
        self.assertEqual(button.evaluate('(el)=>getComputedStyle(el).color'), 'rgb(255, 255, 255)')
        self.assertEqual(button.evaluate('(el)=>getComputedStyle(el).fontSize'), '36px')
        self.assertEqual(self.page.locator('#raster_owned').text_content(), '')
        frame = self.state()['variables']['frames']
        self.wait(f'state.variables.frames>{frame}+4 && !busy && inputQueued===0')
        self.assertTrue(self.same(owner, '#art_label'))
        self.assertTrue(self.same(label, '#art_label .button-label'))
        self.assertTrue(self.same(artwork, '#art_label img'))
        button.click(); self.wait('state.variables.buttonLabelClicks===1 && !busy && inputQueued===0')
        self.page.locator('#update_art_label').click()
        self.wait("state.objects.art_label.btn_text==='Changed label' && !busy && inputQueued===0")
        self.assertTrue(self.same(owner, '#art_label'))
        self.assertTrue(self.same(label, '#art_label .button-label'))
        self.assertEqual(button.locator('img').count(), 0)
        self.assertEqual(button.inner_text(), 'Changed label')
        self.assertEqual(button.evaluate('(el)=>getComputedStyle(el).color'), 'rgb(163, 0, 0)')
        self.assertEqual(button.evaluate('(el)=>getComputedStyle(el).fontSize'), '24px')
        button.click(); self.wait('state.variables.buttonLabelClicks===2 && !busy && inputQueued===0')
        self.assert_clean()

    def test_basic_tmp_formatting_stays_literal_for_other_markup_and_retains_nodes(self):
        self.wait('state.objects.text_literal && !busy && inputQueued===0')
        rich = self.page.locator('#text_rich')
        self.assertEqual(rich.text_content(), '中文 Bold Italic Under Strike')
        self.assertEqual(rich.locator('b').evaluate('(el)=>getComputedStyle(el).fontWeight'), '700')
        self.assertEqual(rich.locator('b i').evaluate('(el)=>getComputedStyle(el).fontStyle'), 'italic')
        self.assertIn('underline', rich.locator('u').evaluate('(el)=>getComputedStyle(el).textDecorationLine'))
        self.assertIn('line-through', rich.locator('u s').evaluate('(el)=>getComputedStyle(el).textDecorationLine'))
        literal = self.page.locator('#text_literal')
        self.assertIn('<img src=', literal.text_content())
        self.assertIn('<script>', literal.text_content())
        self.assertIn('<b onclick=', literal.text_content())
        self.assertEqual(literal.locator('img,script,[onclick]').count(), 0)
        self.assertTrue(self.page.evaluate('typeof window.richTextExecuted === "undefined"'))
        owner = rich.element_handle();bold = rich.locator('b').element_handle()
        before = self.state()['variables']['frames']
        self.wait(f'state.variables.frames>{before}+4 && !busy && inputQueued===0')
        self.assertTrue(self.same(owner, '#text_rich'))
        self.assertTrue(self.same(bold, '#text_rich b'))
        self.page.locator('#rewrite_rich_text').click()
        self.wait("state.objects.text_rich.content==='<i>Changed</i>' && !busy && inputQueued===0")
        self.assertEqual(rich.text_content(), 'Changed')
        self.assertEqual(rich.locator('b,u,s').count(), 0)
        self.assertTrue(self.same(owner, '#text_rich'))
        self.page.locator('#rewrite_rich_text').click()
        self.wait("state.objects.text_rich.content==='Plain < 2' && !busy && inputQueued===0")
        self.assertEqual(rich.text_content(), 'Plain < 2')
        self.assertEqual(rich.locator('b,i,u,s').count(), 0)
        self.assertTrue(self.same(owner, '#text_rich'))
        self.assert_clean()

    def test_hover_frame_image_variable_position_updates_preserve_pressed_button_and_focus(self):
        button = self.page.locator('#target').element_handle()
        artwork = self.page.locator('#target img').element_handle()
        parent = self.page.locator('#parent').element_handle()
        marker = self.page.locator('#marker').element_handle()
        generation = self.state()['objects']['target']['preview_generation']
        old_marker_src = self.page.locator('#marker').get_attribute('src')
        self.hover()
        self.assertTrue(self.same(button, '#target'), 'hover-driven Lua image/var update must retain actual button')
        self.assertTrue(self.same(artwork, '#target img'))
        self.assertTrue(self.same(parent, '#parent'))
        self.assertTrue(self.same(marker, '#marker'))
        self.assertNotEqual(self.page.locator('#marker').get_attribute('src'), old_marker_src)
        self.assertEqual(self.page.locator('#target img').get_attribute('src'), 'media/1')
        self.page.mouse.down()
        self.assertTrue(self.page.locator('#target').evaluate('(el)=>document.activeElement===el'))
        frame = self.state()['variables']['frames']
        self.key('1', 'state.variables.patches===1')
        render_generation = self.state()['render_generation']
        self.wait(f'state.variables.frames>{frame + 2}')
        self.wait(f'state.render_generation>{render_generation}')
        self.assertTrue(self.same(button, '#target'), 'mousedown/frame/position update must retain same live element')
        self.assertTrue(self.same(artwork, '#target img'), 'pressed artwork child must retain identity too')
        self.assertTrue(self.page.locator('#target').evaluate('(el)=>document.activeElement===el'), 'keyboard focus survives unrelated draw')
        self.assertEqual(self.state()['objects']['target']['preview_generation'], generation)
        self.page.mouse.up()
        self.wait('state.variables.clicks===1')
        self.assertEqual(self.state()['variables']['newClicks'], 0)
        self.assertTrue(self.same(button, '#target'))
        self.assertTrue(self.page.locator('#target').evaluate('(el)=>document.activeElement===el'))
        self.assert_clean()

    def test_same_name_new_generation_retires_pressed_dom_and_old_handler(self):
        self.hover()
        old = self.page.locator('#target').element_handle()
        generation = self.state()['objects']['target']['preview_generation']
        self.page.mouse.down()
        self.key('5', f'state.objects.target.preview_generation!=={generation}')
        self.assertFalse(self.same(old, '#target'))
        self.assertFalse(old.evaluate('(el)=>el.isConnected'))
        self.assertTrue(old.evaluate('(el)=>el.onclick===null && el.onmouseenter===null && el.onmouseleave===null'))
        self.page.mouse.up()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.state()['variables']['clicks'], 0)
        self.assertEqual(self.state()['variables']['newClicks'], 0, 'release of retired button must not activate replacement')
        self.page.locator('#target').click()
        self.wait('state.variables.newClicks===1')
        self.assertEqual(self.state()['variables']['clicks'], 0)
        self.assert_clean()

    def test_removed_parent_retires_pressed_descendant_without_ghost_click(self):
        self.hover()
        old = self.page.locator('#target').element_handle()
        parent = self.page.locator('#parent').element_handle()
        self.page.mouse.down()
        self.key('2', '!state.objects.target')
        self.assertFalse(old.evaluate('(el)=>el.isConnected'))
        self.assertFalse(parent.evaluate('(el)=>el.isConnected'))
        self.assertEqual(self.page.locator('#target').count(), 0)
        self.assertEqual(self.page.locator('#parent').count(), 0)
        self.page.mouse.up()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.state()['variables']['clicks'], 0)
        self.assert_clean()

    def test_reparent_moves_same_actual_node_to_real_parent_once(self):
        old = self.page.locator('#target').element_handle()
        first_parent = self.page.locator('#parent').element_handle()
        second_parent = self.page.locator('#other').element_handle()
        self.key('3', "state.objects.target.inlayer==='other'")
        self.assertTrue(self.same(old, '#target'))
        self.assertTrue(self.same(first_parent, '#parent'))
        self.assertTrue(self.same(second_parent, '#other'))
        self.assertEqual(self.page.locator('#target').evaluate('(el)=>el.parentElement.id'), 'other')
        self.assertEqual(self.page.locator('#parent #target').count(), 0)
        self.page.locator('#target').click()
        self.wait('state.variables.clicks===1')
        self.assert_clean()

    def test_hidden_parent_blocks_pressed_click_then_restores_same_nodes(self):
        self.hover()
        old = self.page.locator('#target').element_handle()
        parent = self.page.locator('#parent').element_handle()
        self.page.mouse.down()
        self.key('4', 'state.objects.parent.visible===false')
        self.assertFalse(self.page.locator('#target').is_visible())
        self.page.mouse.up()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.state()['variables']['clicks'], 0)
        self.key('4', 'state.objects.parent.visible===true')
        self.assertTrue(self.same(old, '#target'))
        self.assertTrue(self.same(parent, '#parent'))
        self.page.locator('#target').click()
        self.wait('state.variables.clicks===1')
        self.assert_clean()

    def test_artwork_and_text_updates_have_no_ghost_children_or_stale_properties(self):
        old = self.page.locator('#target').element_handle()
        artwork = self.page.locator('#target img').element_handle()
        self.key('ArrowDown', "state.objects.target.btn_text==='Updated label'")
        self.assertTrue(self.same(old, '#target'))
        self.assertTrue(self.same(artwork, '#target img'))
        self.assertEqual(self.page.locator('#target img').get_attribute('src'), 'media/1')
        self.assertEqual(self.page.locator('#target').get_attribute('aria-label'), 'Updated label')
        self.key(' ', "state.objects.target.btn_text==='Text button' && !state.objects.target.normal_resId")
        self.assertTrue(self.same(old, '#target'))
        self.assertEqual(self.page.locator('#target img').count(), 0)
        self.assertEqual(self.page.locator('#target').inner_text(), 'Text button')
        self.key(' ', "state.objects.target.normal_resId==='$red'")
        self.assertTrue(self.same(old, '#target'))
        self.assertEqual(self.page.locator('#target img').count(), 1)
        self.assertEqual(self.page.locator('#target').inner_text(), 'Text button')
        marker = self.page.locator('#marker').element_handle()
        self.key('ArrowUp', "state.objects.marker._kind==='text'")
        self.assertFalse(self.same(marker, '#marker'))
        self.assertEqual(self.page.locator('#marker .inner').count(), 1)
        self.assertEqual(self.page.locator('#marker').inner_text(), 'Source text')
        self.assert_clean()

    def test_visual_hover_without_raw_binding_survives_frame_draws(self):
        self.key('Escape', 'state.input_binding_count===0')
        old = self.page.locator('#target').element_handle()
        artwork = self.page.locator('#target img').element_handle()
        self.page.mouse.move(*self.center())
        self.wait("document.querySelector('#target img').getAttribute('src')==='media/1'")
        frame = self.state()['variables']['frames']
        self.wait(f'state.variables.frames>{frame + 3}')
        self.assertTrue(self.same(old, '#target'))
        self.assertTrue(self.same(artwork, '#target img'))
        self.assertEqual(self.page.locator('#target img').get_attribute('src'), 'media/1')
        self.page.mouse.move(1200, 100)
        self.wait("document.querySelector('#target img').getAttribute('src')==='media/0'")
        self.assertEqual(self.state()['variables']['enters'], 0)
        self.assert_clean()

    def test_renamed_button_retires_old_identity_without_ghost_descendants(self):
        self.hover()
        old = self.page.locator('#target').element_handle()
        self.page.mouse.down()
        self.key('Enter', "!state.objects.target && !!state.objects.renamed")
        self.assertFalse(old.evaluate('(el)=>el.isConnected'))
        self.assertTrue(old.evaluate('(el)=>el.onclick===null'))
        self.assertEqual(self.page.locator('#target').count(), 0)
        self.assertEqual(self.page.locator('#renamed img').count(), 1)
        self.page.mouse.up()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.state()['variables']['clicks'], 0)
        self.page.locator('#renamed').click()
        self.wait('state.variables.clicks===1')
        self.assert_clean()

    def test_old_trusted_click_queued_behind_delayed_replacement_response_is_discarded(self):
        self.hover()
        old = self.page.locator('#target').element_handle()
        generation = self.state()['objects']['target']['preview_generation']
        held = []
        clicks = []
        self.page.on('request', lambda request: clicks.append(request.url) if request.url.endswith('/click') else None)

        def hold_response(route):
            # Forward to actual PreviewHTTP/Lua now, but delay its real response to the frontend.
            held.append((route, route.fetch()))

        self.page.route('**/key', hold_response)
        self.page.keyboard.press('5')
        for _ in range(80):
            self.page.wait_for_timeout(10)
            if held:
                break
        self.assertEqual(len(held), 1)
        with self.server.game_lock:
            self.assertNotEqual(self.server.game.snapshot()['objects']['target']['preview_generation'], generation)
        self.assertEqual(self.state()['objects']['target']['preview_generation'], generation)
        self.assertTrue(self.same(old, '#target'), 'old DOM remains live while real replacement response is delayed')
        self.page.mouse.click(*self.center())  # Trusted native click: action is queued behind key's pending fetch.
        self.assertEqual(clicks, [])
        route, response = held[0]
        route.fulfill(response=response)
        self.wait(f'state.objects.target.preview_generation!=={generation} && !busy && inputQueued===0')
        self.page.wait_for_timeout(100)
        self.assertEqual(clicks, [], 'retired queued action must be discarded before issuing click RPC')
        self.assertEqual(self.state()['variables']['clicks'], 0)
        self.assertEqual(self.state()['variables']['newClicks'], 0)
        self.page.locator('#target').click()
        self.wait('state.variables.newClicks===1')
        self.assertEqual(len(clicks), 1)
        self.assert_clean()

    def test_trusted_game_click_after_sound_toolbar_restores_keyboard_focus(self):
        self.page.locator('#sound').click()
        self.assertTrue(self.page.locator('#sound').evaluate('(el)=>document.activeElement===el'))
        self.page.locator('#stage').click()
        self.wait('document.activeElement===stage && !busy && inputQueued===0')
        self.key('1', 'state.variables.patches===1')
        self.assert_clean()

    def test_reset_at_same_geometry_reports_actual_viewport_before_new_pointer_input(self):
        old = self.state()['input_generation']
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.locator('#reset').click()
        self.wait(f'state.input_generation!=={old!r} && !!state.viewport && !busy && inputQueued===0')
        with self.server.game_lock:
            self.assertIsNotNone(self.server.game.snapshot()['viewport'])
        self.hover()
        self.page.locator('#target').click()
        self.wait('state.variables.clicks===1 && !busy && inputQueued===0')
        self.assert_clean()

    def test_real_passive_cursor_reports_canvas_coordinates_without_a_pressed_contact(self):
        self.page.mouse.move(1100,300)
        self.wait('state.variables.cursorMoves>=1 && !busy && inputQueued===0')
        state=self.state();rect=self.page.locator('#stage').bounding_box()
        self.assertAlmostEqual(state['variables']['cursorX'],(1100-rect['x'])*1920/rect['width'],delta=.2)
        self.assertAlmostEqual(state['variables']['cursorY'],(300-rect['y'])*1080/rect['height'],delta=.2)
        self.assertEqual(state['pointer_contact_count'],0)
        self.page.mouse.move(1100,790)
        self.wait('state.variables.cursorLeaves>=1 && !busy && inputQueued===0')
        self.assert_clean()

    def queued_key_after_delayed_pointer(self, replace_owner):
        if replace_owner:
            self.page.locator('#arm_keys').click()
            self.wait('state.variables.swapKeys===true && !busy && inputQueued===0')
        held=[]
        def delay_up(route):
            event=route.request.post_data_json['event']
            if event['kind']=='pointer'and event['phase']=='up':held.append((route,route.fetch()))
            else:route.continue_()
        self.page.route('**/input',delay_up)
        self.page.locator('#stage').click()
        for _ in range(80):
            self.page.wait_for_timeout(10)
            if held:break
        self.assertEqual(len(held),1)
        self.page.keyboard.press('1')
        route,response=held[0];route.fulfill(response=response)
        self.wait('!busy && inputQueued===0')
        self.page.wait_for_timeout(120)
        return held

    def test_key_queued_behind_real_pointer_ack_preserves_same_key_owner(self):
        self.queued_key_after_delayed_pointer(False)
        self.wait('state.variables.patches===1 && !busy && inputQueued===0')
        self.assert_clean()

    def test_queued_old_key_is_not_replayed_on_pointer_callback_replacement_owner(self):
        self.queued_key_after_delayed_pointer(True)
        self.assertEqual(self.state()['variables'].get('replacementKeys',0),0)
        self.assertEqual(self.state()['variables']['patches'],0)
        self.page.keyboard.press('1');self.wait('state.variables.replacementKeys===1')
        self.assert_clean()
