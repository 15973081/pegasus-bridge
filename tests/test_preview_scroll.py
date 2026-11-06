"""Bounded scroll reports and real browser clipping/scroll input, not Unity proof."""
import copy,importlib.util,os,threading,time,unittest
from pegasus_bridge.core import BridgeError
from pegasus_bridge.preview_runtime import PreviewGame
from pegasus_bridge.preview import PreviewHTTP

SOURCE='''
local function mount()
 ac.createScrollView({name='paper',pos={960,540},visualScope={300,200},contentArea={294,900},
  layoutMode='none',layerRebuild={autoRebuildLayout=false},scrollViewHorizontal=false,scrollViewVertical=true,scrollViewShowBl=true})
 local s=pegasusReadPreviewScrollView('paper')
 pegasusStylePreviewScrollView('paper',{generation=s.generation,width=6,track={.2,.2,.2,.3},thumb={.8,.8,.8,1},hoverThumb={.9,.9,.9,1},pressedThumb={.7,.7,.7,1}})
 ac.createText({name='body',inlayer='paper',pos={147,450},size={294,900},content='Source full text body'})
end
mount()
pegasusBindPreviewInput(function()ac.var.rawInputs=(ac.var.rawInputs or 0)+1 end)
pegasusBindPreviewKeys(function(key)
 if key=='2'then mount()elseif key=='3'then ac.remove({name='paper'})end
end)
'''
def bundle(source=SOURCE):return dict(source=source,variables={},media={},width=1920,height=1080,title='Scroll fixture',script_sha256='scroll-fixture',instruction_limit=100000)
def report(generation=1,**changes):
    value=dict(name='paper',generation=generation,top=100,left=0,scrollHeight=900,scrollWidth=294,clientHeight=200,clientWidth=294)
    value.update(changes);return value
@unittest.skipUnless(importlib.util.find_spec('lupa'),'preview extra needed')
class ScrollContractTests(unittest.TestCase):
    def test_reports_start_unknown_and_are_detached(self):
        game=PreviewGame(bundle());state=game.runtime.read_scroll('paper')
        self.assertIsNone(state['reportedTop']);state['height']=999
        self.assertEqual(game.runtime.read_scroll('paper')['height'],200)
        game.scroll_event(report());actual=game.runtime.read_scroll('paper')
        self.assertEqual(actual['reportedTop'],100);self.assertEqual(actual['clientWidth'],294)
        self.assertIsNone(game.snapshot()['variables'].get('rawInputs'))
    def test_invalid_reports_do_not_fault_or_replace_previous_actual(self):
        game=PreviewGame(bundle());game.scroll_event(report())
        for change in ({'top':float('nan')},{'top':701.1},{'clientWidth':290},{'scrollHeight':800},{'generation':True},{'extra':1},{'name':3}):
            with self.subTest(change=change),self.assertRaises(BridgeError):game.scroll_event(report(**change))
        self.assertIsNone(game.fault);self.assertEqual(game.runtime.read_scroll('paper')['reportedTop'],100)
    def test_replacement_and_removal_reject_old_report(self):
        game=PreviewGame(bundle());old=game.runtime.read_scroll('paper')['generation']
        game.key('2',game.revision);new=game.runtime.read_scroll('paper')['generation'];self.assertNotEqual(old,new)
        game.scroll_event(report(generation=old));self.assertIsNone(game.runtime.read_scroll('paper')['reportedTop'])
        game.scroll_event(report(generation=new));self.assertEqual(game.runtime.read_scroll('paper')['reportedTop'],100)
        game.key('3',game.revision);self.assertIsNone(game.runtime.read_scroll('paper'))
        game.scroll_event(report(generation=new));self.assertIsNone(game.runtime.read_scroll('paper'))
    def test_bounded_geometry_and_explicit_layout(self):
        for source in (SOURCE.replace('300,200','300,0'),SOURCE.replace('294,900','294,32769'),SOURCE.replace("layoutMode='none'","layoutMode='vertical'"),SOURCE.replace('autoRebuildLayout=false','autoRebuildLayout=true')):
            with self.assertRaises(BridgeError):PreviewGame(bundle(source))
    def test_style_generation_colors_and_fields_are_guarded(self):
        for mutation in ('generation=s.generation+1','width=99','thumb={2,0,0,1}','thumb={0,0,0}','surprise=true,width=6'):
            source=SOURCE.replace('generation=s.generation',mutation) if mutation.startswith('generation=') else SOURCE.replace('width=6',mutation) if 'width='in mutation else SOURCE.replace('thumb={.8,.8,.8,1}',mutation)
            with self.subTest(mutation=mutation),self.assertRaises(BridgeError):PreviewGame(bundle(source))

@unittest.skipUnless(os.environ.get('B13_DOM_SMOKE')=='1' and all(importlib.util.find_spec(x) for x in ('lupa','playwright')),'opt-in actual Chromium scroll smoke')
class ScrollDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright=sync_playwright().start();cls.browser=cls.playwright.chromium.launch(executable_path=os.environ.get('PEGASUS_PREVIEW_BROWSER_EXECUTABLE',r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),headless=True,ignore_default_args=['--hide-scrollbars'])
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.playwright.stop()
    def setUp(self):
        self.server=PreviewHTTP(bundle());self.stopped=threading.Event()
        def serve():
            while not self.stopped.is_set():
                self.server.handle_request()
                with self.server.game_lock:self.server.game.advance()
        self.thread=threading.Thread(target=serve,daemon=True);self.thread.start()
        self.page=self.browser.new_page(viewport={'width':1280,'height':800});self.errors=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}{self.server.prefix}')
        self.wait(lambda:self.actual() is not None and self.actual().get('reportedTop')is not None)
    def tearDown(self):self.page.close();self.stopped.set();self.thread.join(5);self.server.server_close()
    def actual(self):
        with self.server.game_lock:
            value=self.server.game.runtime.read_scroll('paper');return dict(value.items()) if value is not None else None
    def wait(self,fn):
        for _ in range(240):
            if fn():return
            time.sleep(.025)
        self.fail('Actual scroll condition timed out: '+str(self.actual())+' / '+str(self.page.evaluate('()=>({error:document.querySelector("#error").textContent,scroll:(()=>{const e=document.querySelector("#paper");return e&&{sh:e.scrollHeight,sw:e.scrollWidth,ch:e.clientHeight,cw:e.clientWidth}})()})'))+' / '+str(self.errors))
    def test_real_wheel_clips_and_retains_scroller(self):
        box=self.page.locator('#paper').bounding_box();old=self.page.locator('#paper').element_handle()
        self.page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);self.page.mouse.wheel(0,350)
        self.wait(lambda:self.actual()['reportedTop']>200)
        actual=self.actual();dom=self.page.locator('#paper').evaluate('(e)=>({top:e.scrollTop,sw:e.scrollWidth,sh:e.scrollHeight,cw:e.clientWidth,ch:e.clientHeight,overflow:getComputedStyle(e).overflowY,child:e.firstChild.id})')
        self.assertAlmostEqual(actual['reportedTop'],dom['top'],delta=1)
        self.assertEqual([dom['sh'],dom['ch']],[900,200]);self.assertIn(dom['cw'],(294,300));self.assertEqual(dom['overflow'],'auto')
        self.assertTrue(self.page.evaluate('(old)=>old===document.querySelector("#paper")',old));self.assertFalse(self.errors)
        self.assertIsNone(self.page.evaluate('state.fault'))
    def test_real_scrollbar_drag_and_replacement(self):
        box=self.page.locator('#paper').bounding_box();scale=box['height']/200
        self.page.mouse.move(box['x']+box['width']-3*scale,box['y']+10*scale)
        self.page.mouse.down();self.page.mouse.move(box['x']+box['width']-3*scale,box['y']+120*scale,steps=8);self.page.mouse.up()
        self.wait(lambda:self.actual()['reportedTop']>100)
        old=self.actual()['generation'];self.page.locator('#stage').focus();self.page.keyboard.press('2')
        self.wait(lambda:self.actual()['generation']!=old and self.actual().get('reportedTop')==0)
        self.assertIsNone(self.page.evaluate('state.fault'));self.assertFalse(self.errors)
