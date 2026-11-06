"""Real MCP -> independent CLI -> MCP preview lifecycle. No native game writes."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


KEY_SOURCE = '''
ac.var.menu=0;ac.var.activated=0;ac.var.escaped=0;ac.var.modal=false
pegasusBindPreviewKeys(function(key,repeated)
 assert(CS==nil and os==nil and io==nil and python==nil and require==nil)
 ac.var.repeated=repeated
 if repeated then return true end
 if key=='ArrowDown' then ac.var.menu=(ac.var.menu+1)%5;return true end
 if key=='ArrowUp' then ac.var.menu=(ac.var.menu+4)%5;return true end
 if key=='Enter' then ac.var.activated=ac.var.activated+1;return true end
 if key=='Escape' then ac.var.escaped=ac.var.escaped+1;return true end
 if key==' ' then ac.var.continued=true;return true end
 if tonumber(key) then ac.var.selected=tonumber(key);return true end
end)
ac.createButton({name='open_modal',text='Open modal',size={300,80},pos={800,500},click_func=function()
 ac.var.modal=true
 local unsubscribe
 unsubscribe=pegasusBindPreviewKeys(function(key)
  if key=='Escape' then ac.var.modal=false;unsubscribe() end
  return true
 end)
end})
'''


MIX_SOURCE = '''
assert(CS==nil and python==nil and os==nil and io==nil and require==nil)
local changes={{'volume4',{volume=0.4}},{'mute',{isMuted=true}},{'volume6',{volume=0.6}},{'unmute',{isMuted=false}}}
for index,item in ipairs(changes)do
 local update=item[2]
 ac.createButton({name=item[1],text=item[1],size={230,80},pos={index*300,250},click_func=function()
  ac.var.readback=pegasusSetPreviewSettings(update)
 end})
end
ac.createButton({name='delayed',text='delayed',size={230,80},pos={1500,250},click_func=function()
 CltTimer.SetTimeout(function()ac.var.readback=pegasusSetPreviewSettings({volume=0.2})end,0.08)
end})
ac.createAudio({name='bgm',res_id='$mixwave',targetVolume=100,loop=true})
ac.createAudio({name='fx',res_id='$mixwave',source_type=2,targetVolume=100,loop=true})
ac.createAudio({name='half',res_id='$mixwave',source_type=2,targetVolume=50,loop=true})
ac.showVideo({name='mix_video',res_id='$mixvideo',events={{time=0,action='freeze',func=function()ac.var.frozen=true end}}})
'''

INPUT_SOURCE = '''
assert(CS==nil and os==nil and python==nil and require==nil)
ac.var.gestures=0;ac.var.clicks=0;ac.var.wheels=0;ac.var.cancelled=0;ac.var.phases={}
ac.createLayer({name='world',pos={960,540},size={1920,1080}})
ac.createText({name='hint',inlayer='world',content='Drag the real stage',pos={960,540},size={500,60}})
local startX,startY,offset=0,0,0
local cancelInput
pegasusBindPreviewFrames(function(t,n)
 assert(pegasusPreviewNow()==t)
 ac.var.frames=n;ac.var.frameTime=t
 local vp=pegasusReadPreviewViewport();if vp then ac.var.viewportWidth=vp.width end
 local p=pegasusReadPreviewObjectPosition('world')
 if p then pegasusSetPreviewObjectPosition('world',{x=960+offset,y=540,generation=p.generation})end
end)
pegasusBindPreviewKeys(function(key)if key=='Enter' then ac.var.keys=(ac.var.keys or 0)+1;return true end end)
cancelInput=pegasusBindPreviewInput(function(e)
 if e.kind=='pointer' then
  ac.var.phases[#ac.var.phases+1]={phase=e.phase,x=e.x,y=e.y,time=e.time,frame=e.frame}
  if #ac.var.phases>100 then table.remove(ac.var.phases,1)end
  if e.phase=='down' then
   startX,startY=e.x,e.y;ac.var.held=true
   if ac.var.arm then CltTimer.SetTimeout(function()
    ac.var.held=false;cancelInput();ac.var.modal=true
    pegasusBindPreviewInput(function(e)if e.kind=='pointer' then ac.var.modalPointers=(ac.var.modalPointers or 0)+1 end;return true end)
   end,.03)end
  elseif e.phase=='move' then offset=e.x-startX
  elseif e.phase=='up' then
   ac.var.held=false
   if math.sqrt((e.x-startX)^2+(e.y-startY)^2)>10 then ac.var.gestures=ac.var.gestures+1 end
  elseif e.phase=='cancel' then ac.var.held=false;ac.var.cancelled=ac.var.cancelled+1 end
 elseif e.kind=='wheel' then ac.var.wheels=ac.var.wheels+1;ac.var.wheelY=e.deltaY
 elseif e.kind=='hover' then ac.var.hover=e.phase;ac.var.hoverName=e.name end
end)
ac.createButton({name='actual_button',text='Real button',size={220,72},pos={160,920},click_func=function()ac.var.clicks=ac.var.clicks+1 end})
ac.createButton({name='arm_modal',text='Arm modal',size={220,72},pos={430,920},click_func=function()ac.var.arm=true end})
ac.showVideo({name='input_clip',res_id='$inputclip',events={{time=0,action='freeze',func=function()ac.var.frozen=true end}}})
'''


def input_manifest(root):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:raise RuntimeError('--browser-input needs local ffmpeg for its own tiny video')
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=black:s=160x90:r=5',
                    '-t','1','-an','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(root/'input.mp4')],
                   check=True,capture_output=True,timeout=30)
    (root/'input.lua').write_text(INPUT_SOURCE,encoding='utf-8')
    manifest=root/'input.json';manifest.write_text(json.dumps(dict(version=1,script='input.lua',video_assets={'$inputclip':'input.mp4'})),encoding='utf-8')
    return manifest


def mix_manifest(root):
    with wave.open(str(root/'mix.wav'),'wb')as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000)
        stream.writeframes(b'\0'*16000*2*2)  # Valid silence: tests cannot claim speaker output.
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:raise RuntimeError('--browser-mix needs ffmpeg to create its own tiny local fixture')
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=black:s=160x90:r=5',
                    '-t','1','-an','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(root/'mix.mp4')],
                   check=True,capture_output=True,timeout=30)
    (root/'mix.lua').write_text(MIX_SOURCE,encoding='utf-8')
    manifest=root/'mix.json'
    manifest.write_text(json.dumps(dict(version=1,script='mix.lua',assets={'$mixwave':'mix.wav'},
                                        video_assets={'$mixvideo':'mix.mp4'})),encoding='utf-8')
    return manifest


async def wait_for(page, expression):
    # Poll through CDP without Playwright's in-page eval-based waitForFunction;
    # the preview intentionally forbids unsafe-eval in its CSP.
    for _ in range(200):
        if await page.evaluate(expression):return
        await asyncio.sleep(.025)
    raise AssertionError('Browser condition did not become true: '+expression)


async def browser_keys(url):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(
            headless=True, executable_path=os.environ.get('PEGASUS_PREVIEW_BROWSER_EXECUTABLE'))
        try:
            page=await browser.new_page()
            await page.goto(url)
            await wait_for(page,'state && state.key_binding_count===1')
            # A focused HTML button must not also activate its default click on Enter.
            await page.locator('#open_modal').focus()
            await page.keyboard.press('Enter')
            await wait_for(page,'state.variables.activated===1')
            assert not await page.evaluate('state.variables.modal')
            await page.keyboard.press('ArrowDown')
            await wait_for(page,'state.variables.menu===1')
            await page.keyboard.press('ArrowUp')
            await wait_for(page,'state.variables.menu===0')
            await page.locator('#open_modal').click()
            await wait_for(page,'state.key_binding_count===2')
            await page.keyboard.press('ArrowDown')
            await wait_for(page,'!busy')
            assert await page.evaluate('state.variables.menu')==0
            await page.keyboard.press('Escape')
            await wait_for(page,'state.key_binding_count===1 && !state.variables.modal')
            assert await page.evaluate('state.variables.escaped')==0
            await page.keyboard.press('Space')
            await wait_for(page,'state.variables.continued===true')
            await page.keyboard.press('2')
            await wait_for(page,'state.variables.selected===2')
            await page.keyboard.down('ArrowDown')
            await wait_for(page,'state.variables.menu===1')
            await page.keyboard.down('ArrowDown')
            await wait_for(page,'state.variables.repeated===true')
            await page.keyboard.up('ArrowDown')
            assert await page.evaluate('state.variables.menu')==1
            # Native text editing and preview toolbar keys retain browser behavior.
            revision=await page.evaluate('state.revision')
            await page.evaluate("const input=document.createElement('input');input.id='typing';document.body.append(input);input.focus()")
            await page.keyboard.press('1')
            assert await page.locator('#typing').input_value()=='1'
            await page.locator('#reload').focus()
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(150)
            assert await page.evaluate('state.revision')==revision
            await page.evaluate('document.activeElement.blur()')
            await page.keyboard.press('ArrowLeft')
            await page.wait_for_timeout(150)
            assert await page.evaluate('state.revision')==revision
            # Both events observed the same revision; never replay the second on a newer modal/state.
            await page.evaluate("for(let i=0;i<2;i++)document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true}))")
            await wait_for(page,'state.variables.menu===2 && error.style.display===\'block\'')
            assert await page.evaluate('state.revision')==revision+1
            assert '状态已变化' in await page.locator('#error').inner_text()
        finally:
            await browser.close()


async def browser_mix(url):
    from playwright.async_api import async_playwright
    async with async_playwright()as playwright:
        browser=await playwright.chromium.launch(
            headless=True,executable_path=os.environ.get('PEGASUS_PREVIEW_BROWSER_EXECUTABLE'))
        try:
            page=await browser.new_page()
            await page.goto(url)
            await wait_for(page,"state?.variables.frozen && sounds.size===3 && movies.get('mix_video').video.readyState>=2")
            assert await page.evaluate("sounds.get('bgm').audio instanceof HTMLAudioElement && movies.get('mix_video').video instanceof HTMLVideoElement")
            assert await page.evaluate("!soundEnabled && [...sounds.values()].every(item=>item.audio.paused&&item.audio.muted) && movies.get('mix_video').video.muted")
            assert await page.evaluate('state.mix_settings.volume===1 && state.mix_settings.isMuted===false')
            await page.locator('#volume4').click()
            await wait_for(page,"state.mix_settings.volume===0.4 && sounds.get('bgm').audio.volume===0.4 && movies.get('mix_video').video.volume===0.4")
            assert await page.evaluate("!soundEnabled && sounds.get('fx').audio.paused && sounds.get('fx').audio.muted")
            await page.locator('#mute').click()
            await wait_for(page,"state.mix_settings.isMuted && sounds.get('fx').audio.volume===0 && sounds.get('bgm').audio.volume===0")
            await page.locator('#volume6').click()
            await wait_for(page,"state.mix_settings.isMuted && state.mix_settings.volume===0.6 && sounds.get('fx').audio.volume===0.6")
            assert await page.evaluate("sounds.get('fx').audio.muted && sounds.get('fx').audio.paused && sounds.get('bgm').audio.volume===0 && movies.get('mix_video').video.volume===0")
            await page.locator('#sound').click()
            await wait_for(page,"soundEnabled && [...sounds.values()].every(item=>!item.audio.paused&&!item.audio.muted)")
            assert await page.evaluate("state.mix_settings.isMuted && sounds.get('fx').audio.volume===0.6 && sounds.get('half').audio.volume===0.3 && sounds.get('bgm').audio.volume===0 && !movies.get('mix_video').video.muted")
            await page.locator('#unmute').click()
            await wait_for(page,"state.mix_settings.isMuted===false && sounds.get('bgm').audio.volume===0.6 && movies.get('mix_video').video.volume===0.6")
            await page.locator('#delayed').click()
            await wait_for(page,"state.mix_settings.volume===0.2 && sounds.get('bgm').audio.volume===0.2 && sounds.get('fx').audio.volume===0.2 && movies.get('mix_video').video.volume===0.2")
            # Refresh reapplies media properties even with an unchanged server revision.
            revision=await page.evaluate('state.revision')
            await page.evaluate("sounds.get('fx').audio.volume=0.99;sounds.get('fx').audio.muted=true;movies.get('mix_video').video.volume=0.99")
            await page.locator('#reload').click()
            await wait_for(page,"sounds.get('fx').audio.volume===0.2 && !sounds.get('fx').audio.muted && movies.get('mix_video').video.volume===0.2")
            assert await page.evaluate('state.revision')==revision
            async def accept_reset(dialog):await dialog.accept()
            page.once('dialog',accept_reset)
            await page.locator('#reset').click()
            await wait_for(page,"state.mix_settings.volume===1 && state.mix_settings.isMuted===false && sounds.get('bgm').audio.volume===1")
            assert await page.evaluate('soundEnabled')  # User unlock is independent of server-bus reset.
            other=await browser.new_page()
            await other.goto(url)
            await wait_for(other,'state && sounds.size===3')
            assert await other.evaluate("!soundEnabled && [...sounds.values()].every(item=>item.audio.paused&&item.audio.muted)")
            assert await page.evaluate('state.fault')is None
        finally:
            await browser.close()


async def browser_input(url):
    from playwright.async_api import async_playwright
    async with async_playwright()as playwright:
        browser=await playwright.chromium.launch(headless=True,executable_path=os.environ.get('PEGASUS_PREVIEW_BROWSER_EXECUTABLE'))
        try:
            page=await browser.new_page(viewport={'width':1280,'height':720})
            await page.goto(url)
            await wait_for(page,'state?.viewport && state.variables.frozen && state.variables.frames>=2')
            assert await page.evaluate('state.viewport.width===stage.getBoundingClientRect().width && state.viewport.height===stage.getBoundingClientRect().height && state.viewport.pixelRatio===devicePixelRatio && state.viewport.canvasWidth===stage.offsetWidth && state.viewport.scale===stage.getBoundingClientRect().width/stage.offsetWidth')
            assert await page.evaluate('movies.get("input_clip").video.controls===false && movies.get("input_clip").video.style.pointerEvents==="none"')
            frame=await page.evaluate('state.variables.frames')
            await page.wait_for_timeout(100)
            await wait_for(page,f'state.variables.frames>{frame}')
            # Real hover and button activation must never produce a pointer gesture.
            await page.locator('#actual_button').hover()
            await wait_for(page,'state.variables.hover==="enter" && state.variables.hoverName==="actual_button"')
            await page.mouse.move(5,5)
            await wait_for(page,'state.variables.hover==="leave"')
            await page.locator('#actual_button').click()
            await wait_for(page,'state.variables.clicks===1')
            assert await page.evaluate('state.variables.gestures===0 && state.variables.phases.length===undefined')
            await page.locator('#actual_button').focus()
            await page.keyboard.press('Enter')
            await wait_for(page,'state.variables.keys===1')
            assert await page.evaluate('state.variables.clicks===1 && state.variables.gestures===0')

            async def point(x,y):
                return await page.evaluate('([x,y])=>{const r=stage.getBoundingClientRect();return {x:r.left+x*r.width/1920,y:r.top+y*r.height/1080}}',[x,y])
            start=await point(900,400);end=await point(1200,500)
            await page.mouse.move(**start)
            await page.evaluate('window.__world=document.getElementById("world")')
            await page.mouse.down()
            await wait_for(page,'state.variables.held===true')
            await page.mouse.move(**end,steps=10)
            await page.mouse.up()
            await wait_for(page,'state.variables.gestures===1 && state.pointer_contact_count===0')
            await wait_for(page,'parseFloat(document.getElementById("world").style.left)>200')
            assert await page.evaluate('document.getElementById("world")===window.__world')
            assert await page.evaluate('state.variables.phases[0].phase==="down" && state.variables.phases.at(-1).phase==="up" && state.variables.phases.some(e=>e.phase==="move") && state.variables.phases.every((e,i,a)=>!i||e.time>=a[i-1].time)')
            wheels=await page.evaluate('state.variables.wheels')
            await page.mouse.wheel(0,120)
            await wait_for(page,f'state.variables.wheels>{wheels} && state.variables.wheelY===120')
            wheels=await page.evaluate('state.variables.wheels')
            await page.locator('#reload').hover()
            await page.mouse.wheel(0,120)
            await page.wait_for_timeout(100)
            assert await page.evaluate('state.variables.wheels')==wheels

            # A genuine exit/capture loss cancels an observed down, never an up gesture.
            start=await point(900,400);await page.mouse.move(**start);await page.mouse.down()
            await wait_for(page,'state.variables.held===true')
            await page.mouse.move(1,1)
            await wait_for(page,'state.pointer_contact_count===0 && state.variables.held===false && state.variables.cancelled>=1')
            await page.mouse.up()
            assert await page.evaluate('state.variables.gestures')==1

            # Measured resize cancels live contacts and updates actual dimensions.
            start=await point(900,400);await page.mouse.move(**start);await page.mouse.down()
            await wait_for(page,'state.variables.held===true')
            await page.set_viewport_size({'width':1000,'height':700})
            await wait_for(page,'state.viewport.clientWidth===1000 && state.pointer_contact_count===0 && state.variables.held===false')
            await page.mouse.up()
            assert await page.evaluate('state.viewport.width===stage.getBoundingClientRect().width')

            # A timer installs a modal while the physical mouse remains down.
            # Subsequent real movement/release must not reach its new handler.
            await page.locator('#arm_modal').click()
            await wait_for(page,'state.variables.arm===true')
            start=await point(900,400);end=await point(1200,500)
            await page.mouse.move(**start);await page.mouse.down()
            await wait_for(page,'state.variables.modal===true')
            await page.mouse.move(**end,steps=6);await page.mouse.up()
            await page.wait_for_timeout(150)
            assert await page.evaluate('(state.variables.modalPointers||0)===0 && state.pointer_contact_count===0 && state.variables.gestures===1 && !state.fault')
            assert await page.evaluate('state.frame_binding_count===1 && state.input_binding_count===1')
        finally:
            await browser.close()


async def main():
    with tempfile.TemporaryDirectory() as directory:
        env = dict(os.environ, PEGASUS_PREVIEW_DIR=directory)
        params = StdioServerParameters(command=sys.executable,
                                       args=['-m', 'pegasus_bridge.server'], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(name, args):
                    result = await session.call_tool('pegasus_preview_' + name, args)
                    assert not result.isError, result
                    return json.loads(result.content[0].text)

                started = await call('start', {'manifest_path': str(Path('examples/preview/preview.json').resolve())})
                ident = started['session_id']
                try:
                    cli = subprocess.run([sys.executable, '-m', 'pegasus_bridge.cli',
                                          'preview-click', ident, 'take_key', '--expected-revision', '1'],
                                         env=env, capture_output=True, encoding='utf-8', timeout=15, check=True)
                    assert json.loads(cli.stdout)['variables']['has_key'] is True
                    state = await call('state', {'session_id': ident})
                    assert state['revision'] == 2
                    state = await call('click', {'session_id': ident, 'name': 'open_door', 'expected_revision': 2})
                    assert '门打开了' in state['objects']['message']['content']
                    state = await call('reset', {'session_id': ident, 'expected_revision': 3})
                    assert state['variables']['has_key'] is False
                    assert state['revision'] == 4
                finally:
                    await call('stop', {'session_id': ident})
                root=Path(directory)
                (root/'keys.lua').write_text(KEY_SOURCE,encoding='utf-8')
                manifest=root/'keys.json'
                manifest.write_text(json.dumps(dict(version=1,script='keys.lua')),encoding='utf-8')
                started=await call('start',{'manifest_path':str(manifest)})
                ident=started['session_id']
                try:
                    state=await call('key',{'session_id':ident,'key':'ArrowDown','expected_revision':1,'repeat':False})
                    assert state['variables']['menu']==1 and state['key_binding_count']==1
                    for args in [dict(session_id=ident,key='Enter',expected_revision=1),
                                 dict(session_id=ident,key='ArrowLeft',expected_revision=2)]:
                        result=await session.call_tool('pegasus_preview_key',args)
                        assert result.isError
                    state=await call('reset',{'session_id':ident,'expected_revision':2})
                    assert state['key_binding_count']==1 and state['variables']['menu']==0
                    if '--browser-keys' in sys.argv:
                        await browser_keys(started['url'])
                finally:
                    await call('stop',{'session_id':ident})
                if '--browser-mix' in sys.argv:
                    started=await call('start',{'manifest_path':str(mix_manifest(root))})
                    ident=started['session_id']
                    try:
                        assert (await call('state',{'session_id':ident}))['mix_settings']['volume']==1
                        await browser_mix(started['url'])
                    finally:
                        await call('stop',{'session_id':ident})
                if '--browser-input' in sys.argv:
                    started=await call('start',{'manifest_path':str(input_manifest(root))});ident=started['session_id']
                    try:
                        state=await call('state',{'session_id':ident});assert state['viewport'] is None
                        event=dict(kind='wheel',deltaX=0,deltaY=0,deltaMode=0,generation=state['input_generation'],sequence=state['input_sequence']+1)
                        state=await call('input',{'session_id':ident,'event':event,'expected_revision':state['revision']})
                        for bad in [dict(event,extra=True),dict(event,id=True),dict(event,deltaMode=True)]:
                            result=await session.call_tool('pegasus_preview_input',dict(session_id=ident,event=bad,expected_revision=state['revision']))
                            assert result.isError
                        await browser_input(started['url'])
                    finally:
                        await call('stop',{'session_id':ident})
        print(json.dumps({'mcp_start': True, 'cli_click': True, 'mcp_state_click_reset_stop': True,
                          'mcp_keys_stale_unknown_reset':True,'browser_keys':'--browser-keys' in sys.argv,
                          'browser_mix':'--browser-mix' in sys.argv,'mix_evidence':'server_bus_and_media_properties_only',
                          'browser_input':'--browser-input' in sys.argv,'input_evidence':'real_DOM_mouse_drag_wheel_hover_resize_and_modal_lifetime',
                          'native_runtime': False,'windows_input':False}))


asyncio.run(main())
