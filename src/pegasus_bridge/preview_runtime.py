"""Bounded Lua subset for local previews; this is not the native engine."""
import hashlib
import json
from pathlib import Path

from .core import BridgeError

LIMITATIONS = [
    '同源 Lua 近似预览，非 Unity/天马原生运行',
    '进度仅在本预览会话内；不读写天马玩家存档或商城交易',
    '只适配 createLayer/Image/Text/Button/Style/Audio、stopAudio、showUI 和 ac.var',
    '字体、音频时序及效果不等同原生；showUI 只报告请求，不执行原生界面',
]


def load_bundle(manifest_path):
    path = Path(manifest_path).expanduser().resolve(strict=True)
    if path.stat().st_size > 1024 * 1024:
        raise BridgeError('预览配置过大')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise BridgeError('预览配置需要 version=1')
    root = path.parent

    def local_file(value, extensions, limit):
        if not isinstance(value, str) or Path(value).is_absolute():
            raise BridgeError('脚本和素材必须使用配置目录内的相对路径')
        target = (root / value).resolve(strict=True)
        if not target.is_relative_to(root) or target.suffix.lower() not in extensions:
            raise BridgeError('脚本或素材路径越界或类型不支持')
        if not target.is_file() or target.stat().st_size > limit:
            raise BridgeError('脚本或素材不是文件或超过大小限制')
        return target.read_bytes()

    source = local_file(data.get('script'), {'.lua'}, 2 * 1024 * 1024).decode('utf-8-sig')
    variables = data.get('variables', {})
    if not isinstance(variables, dict) or len(variables) > 1000:
        raise BridgeError('variables 必须为不超过 1000 项的对象')
    if any(not isinstance(k, str) or len(k) > 128 or type(v) not in (str, int, float, bool)
           for k, v in variables.items()):
        raise BridgeError('初始变量仅支持字符串、数字和布尔值')
    # Reject non-finite values, also in initial variables.
    json.dumps(data, allow_nan=False)
    width, height = data.get('width', 1920), data.get('height', 1080)
    if any(type(v) is not int or not 100 <= v <= 4096 for v in (width, height)):
        raise BridgeError('预览宽高须为 100–4096 的整数')
    assets = data.get('assets', {})
    if not isinstance(assets, dict) or len(assets) > 1000:
        raise BridgeError('assets 必须为不超过 1000 项的对象')
    media, total = {}, 0
    types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
             '.webp': 'image/webp', '.gif': 'image/gif', '.mp3': 'audio/mpeg',
             '.wav': 'audio/wav', '.ogg': 'audio/ogg'}
    for key, value in assets.items():
        if not isinstance(key, str) or not key or len(key) > 256:
            raise BridgeError('素材 ID 无效')
        content = local_file(value, types, 32 * 1024 * 1024)
        total += len(content)
        if total > 128 * 1024 * 1024:
            raise BridgeError('预览素材合计不得超过 128 MiB')
        media[key] = (content, types[Path(value).suffix.lower()])
    return dict(title=str(data.get('title', path.stem))[:200], source=source, media=media,
                variables=variables, width=width, height=height,
                script_sha256=hashlib.sha256(source.encode()).hexdigest())


BOOTSTRAP = r'''
local sethook=debug.sethook
local safe={assert=assert,error=error,ipairs=ipairs,pairs=pairs,next=next,
 select=select,tonumber=tonumber,tostring=tostring,type=type,math=math,string=string,
 table=table,utf8=utf8}
-- Do not expose Python, OS, package/load, or debug to game code.
local exhausted=false
safe.pcall=function(fn,...)
 local values=table.pack(pcall(fn,...))
 assert(not exhausted,'Preview instruction limit exceeded')
 return table.unpack(values,1,values.n)
end
local objects,styles,audio,requests,logs={},{},{},{},{}
local methods={createLayer='layer',createImage='image',createText='text',createButton='button'}
local count=0
local function name(c)
 assert(type(c)=='table' and type(c.name)=='string' and #c.name<=256,'Invalid control name')
end
local ac={var={}}
for method,kind in pairs(methods) do
 ac[method]=function(c)
  name(c)
  if kind=='button' then assert(type(c.click_func)=='function','Button needs click_func') end
  if not objects[c.name] then count=count+1;assert(count<=1000,'Too many controls')end
  c._kind=kind;objects[c.name]=c;coroutine.yield()
 end
end
ac.createStyle=function(c)name(c);styles[c.name]=c;coroutine.yield()end
ac.createAudio=function(c)name(c);audio[c.name]=c;coroutine.yield()end
ac.stopAudio=function(c)name(c);audio[c.name]=nil;coroutine.yield()end
ac.showUI=function(c)
 assert(type(c)=='table','Invalid showUI');requests[#requests+1]={name=c.name,ui_id=c.ui_id}
 assert(#requests<=100,'Too many native UI requests');coroutine.yield()
end
setmetatable(ac,{__index=function(_,k)error('Unsupported ac command: '..tostring(k))end})
safe.ac=ac
safe.print=function(...)
 local out={};for i=1,select('#',...)do out[i]=tostring(select(i,...))end
 logs[#logs+1]=string.sub(table.concat(out,' '),1,4096);if #logs>50 then table.remove(logs,1)end
end
safe._G=safe
local function run(fn)
 local co=coroutine.create(fn);local ticks=0;exhausted=false
 sethook(co,function()ticks=ticks+1;if ticks>=500 then exhausted=true;error('Preview instruction limit exceeded')end end,'',10000)
 local steps=0
 repeat
  local ok,err=coroutine.resume(co);assert(ok,tostring(err));steps=steps+1
  assert(steps<=2000,'Too many yielded commands')
 until coroutine.status(co)=='dead'
end
local function start(code)
 local fn,err=load(code,'preview-scene','t',safe);assert(fn,err);run(fn)
end
local function click(key)
 local c=objects[key];assert(c and c._kind=='button' and c.visible~=false,'Not an active button')
 local parent=c.inlayer;local seen={}
 while parent and parent~='window' do
  assert(not seen[parent],'Cyclic layer');seen[parent]=true
  local p=objects[parent];assert(p and p.visible~=false,'Hidden or missing parent');parent=p.inlayer
 end
 run(c.click_func)
end
return {start=start,click=click,objects=objects,styles=styles,audio=audio,
 requests=requests,logs=logs,variables=ac.var}
'''


class PreviewGame:
    def __init__(self, bundle):
        try:
            from lupa.lua54 import LuaRuntime
        except ImportError:
            raise BridgeError('预览需要可选依赖：uv sync --extra preview --inexact') from None
        self.bundle = bundle
        self.lua = LuaRuntime(unpack_returned_tuples=True, register_eval=False,
                              register_builtins=False, max_memory=64 * 1024 * 1024)
        self.runtime = self.lua.execute(BOOTSTRAP)
        self.revision = 0
        self.fault = None
        for key, value in bundle['variables'].items():
            self.runtime.variables[key] = value
        self._run(self.runtime.start, bundle['source'])

    def _run(self, fn, arg):
        try:
            fn(arg)
            self.revision += 1
        except Exception as exc:
            self.fault = str(exc)[:1200]
            raise BridgeError('Lua 预览执行失败；当前会话需重置：' + self.fault) from None

    def click(self, name, revision):
        if self.fault:
            raise BridgeError('预览已发生执行错误，请先 reset')
        if type(revision) is not int or revision != self.revision:
            raise BridgeError('预览状态已变化，请重新读取 state 后点击')
        if not isinstance(name, str) or not 1 <= len(name) <= 256:
            raise BridgeError('按钮名称无效')
        c = self.runtime.objects[name]
        if c is None or c['_kind'] != 'button' or c['visible'] is False:
            raise BridgeError('按钮不存在或不可见')
        self._run(self.runtime.click, name)
        return self.snapshot()

    def snapshot(self):
        from lupa.lua54 import lua_type
        budget = [20000]

        def plain(value, depth=0):
            budget[0] -= 1
            if budget[0] < 0 or depth > 24:
                raise BridgeError('预览控件数据过大或嵌套过深')
            kind = lua_type(value)
            if kind == 'function' or kind == 'thread':
                return None
            if kind != 'table':
                if type(value) in (str, int, float, bool, type(None)):
                    return value
                return None
            # Lua table wrappers may differ in identity; depth bounds cycles too.
            items = [(k, v) for k, v in value.items() if lua_type(v) != 'function']
            if items and {k for k, _ in items} == set(range(1, len(items) + 1)):
                return [plain(value[i], depth + 1) for i in range(1, len(items) + 1)]
            return {str(k): plain(v, depth + 1) for k, v in items}

        result = {key: plain(self.runtime[key]) for key in
                  ('objects', 'styles', 'audio', 'requests', 'logs', 'variables')}
        result.update(mode='lua_approximation', native_runtime=False,
                      revision=self.revision, fault=self.fault, limitations=LIMITATIONS,
                      title=self.bundle['title'], width=self.bundle['width'], height=self.bundle['height'],
                      script_sha256=self.bundle['script_sha256'])
        json.dumps(result, allow_nan=False)
        return result
