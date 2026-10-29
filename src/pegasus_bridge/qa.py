"""Recorded click routes and optional browser screenshots for the Lua approximation."""
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import BridgeError
from .preview_runtime import PreviewGame, load_bundle
from .ui import bounded_errors
from .workfiles import read_json, atomic_json


def expectations(state,expected):
    if not isinstance(expected,dict) or set(expected)-{'text','visible','variables','audio'}:raise BridgeError('断言类型只支持 text/visible/variables/audio')
    errors=[]
    for kind,items in expected.items():
        if not isinstance(items,dict):raise BridgeError('断言须为键值对象')
        for key,want in items.items():
            if kind=='text':actual=state['objects'].get(key,{}).get('content')
            elif kind=='visible':
                obj=state['objects'].get(key);actual=obj is not None and obj.get('visible') is not False
                seen=set()
                while actual and obj.get('inlayer') and obj['inlayer']!='window':
                    parent=obj['inlayer']
                    if parent in seen:actual=False;break
                    seen.add(parent);obj=state['objects'].get(parent)
                    actual=obj is not None and obj.get('visible') is not False
            elif kind=='variables':actual=state['variables'].get(key)
            else:actual=key in state['audio']
            if actual!=want:errors.append(dict(kind=kind,key=key,expected=want,actual=actual))
    return errors


@bounded_errors
def test_route(manifest_path,route_path,output_directory,_bundle=None):
    route=read_json(route_path)
    if not isinstance(route,dict) or set(route)-{'version','initial','steps'} or route.get('version')!=1:raise BridgeError('路线格式无效')
    steps=route.get('steps')
    if not isinstance(steps,list) or len(steps)>500:raise BridgeError('路线最多 500 步')
    for step in steps:
        if not isinstance(step,dict) or set(step)-{'click','expect'} or not isinstance(step.get('click'),str):raise BridgeError('每步须含 click 控件名，可选 expect')
    bundle=_bundle if _bundle is not None else load_bundle(manifest_path)
    folder=Path(output_directory).expanduser().resolve();folder.mkdir(parents=True,exist_ok=False)
    outcomes=[];snapshots=[];failure=None
    try:
        game=PreviewGame(bundle);state=game.snapshot();snapshots.append(state)
        errors=expectations(state,route.get('initial',{}));outcomes.append(dict(step=0,passed=not errors,errors=errors))
        if errors:failure='initial_assertion'
        for i,step in enumerate(steps,1):
            if failure:break
            state=game.click(step['click'],state['revision']);snapshots.append(state)
            errors=expectations(state,step.get('expect',{}));outcomes.append(dict(step=i,click=step['click'],passed=not errors,errors=errors))
            if errors:failure='step_assertion'
    except BridgeError as exc:
        failure=str(exc)
    for i,state in enumerate(snapshots):atomic_json(folder/f'state-{i:03d}.json',state)
    result=dict(passed=failure is None,failure=failure,steps=outcomes,snapshots=len(snapshots),script_sha256=bundle['script_sha256'],native_runtime_verified=False)
    atomic_json(folder/'report.json',result)
    return dict(**result,report_path=str(folder/'report.json'))


@bounded_errors
def preview_report(manifest_path,route_path,output_directory):
    """Run the route, then render its snapshots through the bundled preview HTML."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:raise BridgeError('画面报告需要 qa 可选依赖及 Playwright Chromium 或本机 Edge') from None
    bundle=load_bundle(manifest_path)
    result=test_route(manifest_path,route_path,output_directory,_bundle=bundle)
    folder=Path(output_directory).resolve()
    states=[read_json(folder/f'state-{i:03d}.json') for i in range(result['snapshots'])]
    if not states:return result
    html=Path(__file__).with_name('preview.html').read_bytes()
    resources={key:f'asset/{i}' for i,key in enumerate(bundle['media'])}
    media={f'/asset/{i}':item for i,item in enumerate(bundle['media'].values())}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/':data,mime=html,'text/html; charset=utf-8'
            elif self.path=='/resources.json':data,mime=json.dumps(resources).encode(),'application/json'
            elif self.path=='/state':data,mime=json.dumps(states[0]).encode(),'application/json'
            elif self.path in media:data,mime=media[self.path]
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    layouts=[]
    try:
        with sync_playwright() as p:
            try:browser=p.chromium.launch(headless=True)
            except Exception:browser=p.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':bundle['width'],'height':bundle['height']+38})
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.wait_for_function('state!==null',timeout=10000)
                for i,state in enumerate(states):
                    page.evaluate('draw',state)
                    page.wait_for_function('Array.from(document.images).every(i=>i.complete&&i.naturalWidth>0)',timeout=10000)
                    overflow=page.evaluate("""()=>Array.from(document.querySelectorAll('.text')).filter(e=>e.getClientRects().length&&e.innerText.trim()&&(e.firstChild.scrollWidth>e.clientWidth+1||e.firstChild.scrollHeight>e.clientHeight+1)).map(e=>e.id)""")
                    filename=f'screen-{i:03d}.png';page.screenshot(path=str(folder/filename))
                    layouts.append(dict(step=i,screenshot=filename,text_overflow=overflow))
            finally:browser.close()
    except Exception:
        result.update(passed=False,render_error='浏览器渲染失败；检查 qa 依赖、浏览器安装、素材和控件层级')
    finally:
        server.shutdown();server.server_close()
    result['layouts']=layouts
    result['passed']=result['passed'] and len(layouts)==len(states) and not any(x['text_overflow'] for x in layouts)
    atomic_json(folder/'visual-report.json',result)
    return dict(**{k:v for k,v in result.items() if k!='report_path'},report_path=str(folder/'visual-report.json'))
