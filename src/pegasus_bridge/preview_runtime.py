"""Bounded Lua subset for local previews; this is not the native engine."""
import hashlib
import json
import math
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from .core import BridgeError

LIMITATIONS = [
    '同源 Lua 近似预览，非 Unity/天马原生运行',
    '进度仅在本预览会话内；不读写天马玩家存档或商城交易',
    '适配基本控件、音频、HTML5 视频时间/结束回调、showUI 和 ac.var；Unity 输入与未适配命令仍会停止',
    '字体、音频时序及效果不等同原生；showUI 只报告请求，不执行原生界面',
    '音量读回仅确认本预览混音总线；声音需用户点击解锁，媒体元素状态不证明声卡输出',
]

PREVIEW_KEYS = frozenset(('ArrowUp', 'ArrowDown', 'Enter', 'Escape', ' ', '1', '2', '3', '4', '5'))

# Local-only protocol. Coordinates are source logical pixels, top-left origin.
# generation is captured when a DOM event occurs; sequence is allocated by the
# serialized sender from snapshot.input_sequence+1. Never rebase generation.
PREVIEW_INPUT_CONTRACT = {
    'scroll_view': 'ac.createScrollView/create_scroll_view supports bounded fixed-layout vertical content with visualScope/contentArea, layoutMode=none and layerRebuild.autoRebuildLayout=false. Its browser-only return is the exact created generation across yield. pegasusReadPreviewScrollView(name) returns detached config plus actual reportedTop/Left and DOM extents only after a generation-bound scroll-event report. pegasusStylePreviewScrollView validates exact generation, bounded width and normalized RGBA. DOM scrollers retain real wheel/scrollbar input; reports do not dispatch source callbacks. Overlay/reserved layouts are observed, not assumed. Not Unity/native clipping proof.',
    'version': 1, 'common': ['kind', 'generation', 'sequence'],
    'pointer': ['phase=down|move|up|cancel', 'id=0..2147483647', 'pointer_type=mouse|touch|pen', 'x=0..1920', 'y=0..1080'],
    'cursor': ['phase=move|leave', 'pointer_type=mouse|pen', 'x=0..1920', 'y=0..1080; measured viewport required; no synthetic touch hover'],
    'hover': ['phase=enter|leave', 'name=current visible button', 'button_generation=objects[name].preview_generation'],
    'wheel': ['x=0..1920', 'y=0..1080', 'deltaX', 'deltaY', 'deltaMode=0|1|2'],
    'viewport': ['left', 'top', 'width', 'height', 'scale', 'pixelRatio', 'canvasWidth', 'canvasHeight', 'clientWidth', 'clientHeight'],
    'lua': ['pegasusBindPreviewInput(fn(event))->cancel', 'pegasusBindPreviewFrames(fn(seconds,frame))->cancel',
            'pegasusReadPreviewViewport()->copy|nil', 'pegasusPreviewNow()->monotonic seconds'],
    'bounds': '64 live bindings per registry; 128 queued inputs; 10 contacts; budgeted coroutine callbacks',
    'clock': 'Server HTTP pump, at most one frame per 1/60s; no synthetic catch-up frames. Not Unity or requestAnimationFrame.',
    'lifecycle': 'Input/key binding changes and click/key actions invalidate input generation. Reset creates a new nonce. Rebind input on a new modal/controller.',
    'viewport_source': 'Client-reported measured CSS bounds; nil until reported. Not a manifest-derived actual viewport.',
    'video': 'pegasusReadPreviewVideo(name)->nil|{name,time,duration,status,resId,generation,requestSerial,reportedPaused,reportedReadyState,reportedRequestSerial,reportedAt}; status is requested bus state; actual paused/readyState are nil until browser reports. Prepared requires reportedReadyState>=2 and positive duration. Pause/play ACK requires reportedRequestSerial==requestSerial plus reportedPaused matching request. pegasusSetPreviewVideoPaused(name,bool) only changes existing video request bus and increments requestSerial, never fabricates browser state. State reports do not advance source timeline. Generation replacement clears reports; not hardware/audio proof.',
    'layer_position': 'pegasusReadPreviewObjectPosition(name)->nil|{x,y,generation}; only explicit source/ac layer positions. pegasusSetPreviewObjectPosition(name,{x,y,generation})->copy; finite +/-1e6, existing layer only, old generation rejected; no input_generation change.',
    'button_generation': 'pegasusReadPreviewButtonGeneration(name)->integer|nil; read-only current existing button identity, including hidden buttons. Nil for removed/replaced-with-nonbutton/closed objects; does not expose callbacks or accept mutations. Hover input separately enforces visibility and parent lifetime.',
    'object_presence': 'pegasusReadPreviewObjectPresence(name)->boolean for actual current preview object registry; closed/removed is false. This is object existence, not pixel/hardware/animation visibility proof.',
    'video_rate': 'pegasusReadPreviewVideo reports requestedRate separately from actual reportedRate; require reportedRequestSerial==requestSerial for current ACK. pegasusSetPreviewVideoRate(name,.25..4) updates only request state. Actual HTMLVideoElement.playbackRate is reported with media state; old clients without a rate never inherit an old rate ACK. Pause/resume does not reset an independently selected media rate.',
    'asset_opt_in': 'manifest asset_count_limit=1..8192 (default1000), asset_total_limit_mib=1..512 (default128). Video files remain lazy and separately bounded.',
    'lazy_assets': 'lazy_assets=true opts image/audio into immutable local-file references and bounded 256KiB HTTP streaming, with explicit asset_total_limit_mib up to1024. Default eager mode remains at most512MiB; per-file32MiB, root whitelist, count8192, origin/capability and file-identity guards remain. No arbitrary filesystem endpoint is exposed.',
    'text_load_opt_in': 'allow_text_load=true permits text chunks only in the existing sandbox. text_load_limit_mib=1..8 (default2) explicitly bounds one decoded chunk; cumulative8MiB/64 compilations and instruction limits still apply. Raw entry script remains bounded2MiB.',
}


@dataclass(frozen=True)
class PreviewFile:
    path: Path
    size: int
    mtime_ns: int
    inode: int

    def unchanged(self):
        stat=self.path.stat()
        return (stat.st_size,stat.st_mtime_ns,stat.st_ino)==(self.size,self.mtime_ns,self.inode)


@dataclass(frozen=True)
class PreviewVideo(PreviewFile):
    pass


def load_bundle(manifest_path):
    path = Path(manifest_path).expanduser().resolve(strict=True)
    manifest_bytes=path.stat().st_size
    if manifest_bytes > 4 * 1024 * 1024:
        raise BridgeError('预览配置过大')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise BridgeError('预览配置需要 version=1')
    root = path.parent

    def local_file(value, extensions, limit, lazy=False):
        if not isinstance(value, str) or Path(value).is_absolute():
            raise BridgeError('脚本和素材必须使用配置目录内的相对路径')
        target = (root / value).resolve(strict=True)
        if not target.is_relative_to(root) or target.suffix.lower() not in extensions:
            raise BridgeError('脚本或素材路径越界或类型不支持')
        stat=target.stat()
        if not target.is_file() or stat.st_size > limit:
            raise BridgeError('脚本或素材不是文件或超过大小限制')
        if lazy:return PreviewFile(target,stat.st_size,stat.st_mtime_ns,stat.st_ino)
        return target.read_bytes()

    source = local_file(data.get('script'), {'.lua'}, 2 * 1024 * 1024).decode('utf-8-sig')
    variables = data.get('variables', {})
    if not isinstance(variables, dict) or len(variables) > 1000:
        raise BridgeError('variables 必须为不超过 1000 项的对象')
    if any(not isinstance(k, str) or len(k) > 128 or type(v) not in (str, int, float, bool)
           for k, v in variables.items()):
        raise BridgeError('初始变量仅支持字符串、数字和布尔值')
    # Reject non-finite values, also in initial variables.
    try:json.dumps(data, allow_nan=False)
    except (ValueError, TypeError):raise BridgeError('预览配置包含非有限数或无效JSON值') from None
    instruction_limit = data.get('instruction_limit', 5_000_000)
    allow_text_load=data.get('allow_text_load',False)
    if type(allow_text_load) is not bool:
        raise BridgeError('allow_text_load 必须为布尔值')
    text_load_limit_mib=data.get('text_load_limit_mib',2)
    if type(text_load_limit_mib) is not int or not 1<=text_load_limit_mib<=8:
        raise BridgeError('text_load_limit_mib 须为1..8的整数')
    if text_load_limit_mib!=2 and not allow_text_load:
        raise BridgeError('设置文本加载预算需要 allow_text_load=true')
    if type(instruction_limit) is not int or not 100_000 <= instruction_limit <= 200_000_000:
        raise BridgeError('instruction_limit 须为 100000–200000000 的整数')
    width, height = data.get('width', 1920), data.get('height', 1080)
    if any(type(v) is not int or not 100 <= v <= 4096 for v in (width, height)):
        raise BridgeError('预览宽高须为 100–4096 的整数')
    assets = data.get('assets', {})
    asset_count_limit = data.get('asset_count_limit', 1000)
    asset_total_limit_mib = data.get('asset_total_limit_mib', 128)
    lazy_assets=data.get('lazy_assets',False)
    if type(lazy_assets)is not bool:raise BridgeError('lazy_assets 必须为布尔值')
    max_asset_mib=1024 if lazy_assets else 512
    if (type(asset_count_limit) is not int or not 1 <= asset_count_limit <= 8192 or
            type(asset_total_limit_mib) is not int or not 1 <= asset_total_limit_mib <= max_asset_mib):
        raise BridgeError('asset_count_limit 须为1..8192；asset_total_limit_mib 超出显式素材预算（lazy_assets 可达1024MiB，内存模式最多512MiB）')
    if manifest_bytes > 1024 * 1024 and asset_count_limit <= 1000 and asset_total_limit_mib <= 128:
        raise BridgeError('超过1MiB的预览配置需要显式扩大素材预算，最多4MiB')
    if not isinstance(assets, dict) or len(assets) > asset_count_limit:
        raise BridgeError('assets 超过显式素材数量预算')
    media, total = {}, 0
    types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
             '.webp': 'image/webp', '.gif': 'image/gif', '.mp3': 'audio/mpeg',
             '.wav': 'audio/wav', '.ogg': 'audio/ogg'}
    for key, value in assets.items():
        if not isinstance(key, str) or not key or len(key) > 256:
            raise BridgeError('素材 ID 无效')
        content = local_file(value, types, 32 * 1024 * 1024,lazy=lazy_assets)
        total += content.size if isinstance(content,PreviewFile)else len(content)
        if total > asset_total_limit_mib * 1024 * 1024:
            raise BridgeError('预览素材合计超过素材 MiB 预算')
        media[key] = (content, types[Path(value).suffix.lower()])
    video_assets=data.get('video_assets',{})
    if not isinstance(video_assets,dict) or len(video_assets)>1000:
        raise BridgeError('video_assets 必须为不超过1000项的对象')
    for key,value in video_assets.items():
        if not isinstance(key,str) or not key or len(key)>256 or key in media:
            raise BridgeError('视频素材ID无效或重复')
        if not isinstance(value,str) or Path(value).is_absolute():
            raise BridgeError('视频必须使用配置目录内的相对路径')
        video=(root/value).resolve(strict=True)
        if not video.is_relative_to(root) or video.suffix.lower()!='.mp4' or not video.is_file():
            raise BridgeError('视频路径越界或类型不支持')
        stat=video.stat()
        if not 12<=stat.st_size<=512*1024*1024:
            raise BridgeError('视频须为512MiB以内的MP4')
        with video.open('rb') as stream:
            if stream.read(8)[4:8]!=b'ftyp':
                raise BridgeError('视频不是MP4容器')
        media[key]=(PreviewVideo(video,stat.st_size,stat.st_mtime_ns,stat.st_ino),'video/mp4')
    return dict(title=str(data.get('title', path.stem))[:200], source=source, media=media,
                variables=variables, width=width, height=height,
                script_sha256=hashlib.sha256(source.encode()).hexdigest(), instruction_limit=instruction_limit,
                allow_text_load=allow_text_load,text_load_limit_bytes=text_load_limit_mib*1024*1024,asset_count_limit=asset_count_limit,
                asset_total_limit_mib=asset_total_limit_mib,lazy_assets=lazy_assets)


BOOTSTRAP = r'''
local instruction_limit,clock,allow_text_load,text_load_limit=...
instruction_limit=instruction_limit or 5000000
clock=clock or 0
text_load_limit=text_load_limit or 2097152
local sethook=debug.sethook
local load_text=load
local safe={assert=assert,error=error,ipairs=ipairs,pairs=pairs,next=next,
 setmetatable=setmetatable,getmetatable=getmetatable,
 rawset=rawset,rawget=rawget,rawequal=rawequal,
 select=select,tonumber=tonumber,tostring=tostring,type=type,math=math,string=string,
 table=table,utf8=utf8}
-- Python, OS, package and debug stay inaccessible. Optional text loads retain this environment.
if allow_text_load then
 local compiled_count,compiled_bytes=0,0
 safe.load=function(source,name,mode,environment)
  assert(type(source)=='string' and #source<=text_load_limit,'Text load source exceeds configured limit')
  assert(mode==nil or mode=='t','Only text chunks may be loaded')
  assert(environment==nil or environment==safe,'Text load cannot replace the sandbox environment')
  assert(name==nil or type(name)=='string' and #name<=256,'Invalid text chunk name')
  compiled_count=compiled_count+1;compiled_bytes=compiled_bytes+#source
  assert(compiled_count<=64 and compiled_bytes<=8388608,'Text load compilation quota exceeded')
  return load_text(source,name or 'preview-text','t',safe)
 end
end
local exhausted=false
safe.pcall=function(fn,...)
 local values=table.pack(pcall(fn,...))
 assert(not exhausted,'Preview instruction limit exceeded')
 return table.unpack(values,1,values.n)
end
local objects,styles,audio,requests,logs={},{},{},{},{}
local videos,waiting,resumes={},{},{}
local timers,timer_serial,timer_count={},0,0
local key_bindings,active={},true
local input_bindings,frame_bindings,input_queue={},{},{}
local input_epoch,input_busy,frame_number,last_frame_time=1,false,0,clock
local viewport=nil
local render_serial,object_serial,position_serial=0,0,0
local object_generations={}
local object_kinds={}
local scroll_views={}
local cancel_guarded,drain_inputs
local guarded=setmetatable({},{__mode='k'})
local function copy(value)local out={};for k,v in pairs(value)do out[k]=v end;return out end
local function invalidate_input()
 input_epoch=input_epoch+1
 for i=#input_queue,1,-1 do input_queue[i]=nil end
 if cancel_guarded then cancel_guarded()end
end
local function bind(registry,callback,is_input)
 assert(active,'Preview is closed');assert(type(callback)=='function','Binding needs a callback')
 assert(#registry<64,'Too many active preview bindings')
 local binding={callback=callback};registry[#registry+1]=binding
 if is_input then invalidate_input()end
 return function()
  if not binding.callback then return false end
  binding.callback=nil
  for i,item in ipairs(registry)do if item==binding then table.remove(registry,i);break end end
  if is_input then invalidate_input()elseif cancel_guarded then cancel_guarded()end
  return true
 end
end
safe.pegasusBindPreviewInput=function(fn)return bind(input_bindings,fn,true)end
safe.pegasusBindPreviewFrames=function(fn)return bind(frame_bindings,fn,false)end
safe.pegasusReadPreviewViewport=function()return viewport and copy(viewport) or nil end
safe.pegasusPreviewNow=function()return clock end
safe.pegasusReadPreviewButtonGeneration=function(name)
 assert(type(name)=='string' and #name>=1 and #name<=256,'Invalid button name')
 local c=active and objects[name]
 if not c or object_kinds[name]~='button' then return nil end
 return object_generations[name]
end
local function finite(v)return type(v)=='number' and v==v and v>-math.huge and v<math.huge end
safe.pegasusReadPreviewScrollView=function(name)
 assert(type(name)=='string' and #name>=1 and #name<=256,'Invalid scroll view name')
 local s=active and objects[name] and object_kinds[name]=='scrollview' and scroll_views[name]
 if not s or s.generation~=object_generations[name]then return nil end
 return copy(s)
end
local function scroll_event(name,generation,top,left,sh,sw,ch,cw)
 local s=scroll_views[name]
 if not active or not objects[name] or object_kinds[name]~='scrollview' or not s or s.generation~=generation then return false end
 local bar=s.contentHeight>s.height and (s.scrollbarWidth or 6) or 0
 -- Chromium may use an overlay scrollbar, which consumes no client width.
 -- Report the actual width; never manufacture reserved gutter geometry.
 assert(math.abs(ch-s.height)<=1 and (math.abs(cw-(s.width-bar))<=1 or math.abs(cw-s.width)<=1),'Scroll viewport differs from configured geometry')
 assert(math.abs(sh-math.max(ch,s.contentHeight))<=1 and math.abs(sw-math.max(cw,s.contentWidth))<=1,'Scroll content differs from configured geometry')
 assert(top>=0 and left>=0 and top<=math.max(0,sh-ch)+1 and left<=math.max(0,sw-cw)+1,'Scroll offset out of range')
 s.reportedTop=top;s.reportedLeft=left;s.scrollHeight=sh;s.scrollWidth=sw;s.clientHeight=ch;s.clientWidth=cw;s.reportedAt=clock
 return true
end
safe.pegasusStylePreviewScrollView=function(name,style)
 local s=active and scroll_views[name]
 assert(s and objects[name] and object_kinds[name]=='scrollview','Existing scroll view required')
 assert(type(style)=='table' and style.generation==s.generation,'Scroll view was replaced')
 for key in pairs(style)do assert(key=='generation' or key=='width' or key=='track' or key=='thumb' or key=='hoverThumb' or key=='pressedThumb','Unknown scroll style field')end
 assert(finite(style.width) and style.width>=1 and style.width<=32,'Bounded scrollbar width required')
 local value={width=style.width}
 for _,key in ipairs({'track','thumb','hoverThumb','pressedThumb'})do
  local rgba=style[key];assert(type(rgba)=='table' and #rgba==4,'RGBA scrollbar color required')
  value[key]={}
  for i=1,4 do assert(finite(rgba[i]) and rgba[i]>=0 and rgba[i]<=1,'Scrollbar color must be normalized');value[key][i]=rgba[i]end
 end
 objects[name]._previewScrollbar=value;s.scrollbarWidth=style.width
 render_serial=render_serial+1
 return true
end
safe.pegasusReadPreviewObjectPosition=function(name)
 assert(type(name)=='string' and #name>=1 and #name<=256,'Invalid layer name')
 local c=active and objects[name]
 if not c or object_kinds[name]~='layer' or type(c.pos)~='table' or not finite(c.pos[1]) or not finite(c.pos[2])then return nil end
 return {x=c.pos[1],y=c.pos[2],generation=object_generations[name]}
end
safe.pegasusSetPreviewObjectPosition=function(name,value)
 assert(active,'Preview is closed');assert(type(name)=='string' and #name>=1 and #name<=256,'Invalid layer name')
 assert(type(value)=='table','Position requires x/y/generation')
 for key in pairs(value)do assert(key=='x' or key=='y' or key=='generation','Unknown position field')end
 local c=objects[name]
 assert(c and object_kinds[name]=='layer','Only an existing layer can be positioned')
 assert(type(value.generation)=='number' and value.generation==object_generations[name],'Layer was replaced')
 assert(finite(value.x) and finite(value.y) and math.abs(value.x)<=1000000 and math.abs(value.y)<=1000000,'Invalid layer position')
 if not c.pos or c.pos[1]~=value.x or c.pos[2]~=value.y then
  c.pos={value.x,value.y};position_serial=position_serial+1
 end
 return safe.pegasusReadPreviewObjectPosition(name)
end
local mix_settings={isMuted=false,volume=1,videoVolume=1,musicVolume=1,fxVolume=1}
local function read_mix()
 return {isMuted=mix_settings.isMuted,volume=mix_settings.volume,
  videoVolume=mix_settings.videoVolume,musicVolume=mix_settings.musicVolume,fxVolume=mix_settings.fxVolume}
end
safe.pegasusReadPreviewSettings=read_mix
safe.pegasusSetPreviewSettings=function(update)
 assert(active,'Preview is closed')
 assert(type(update)=='table','Preview settings need a table')
 local field,value
 for key,item in pairs(update)do
  assert(field==nil,'Change one preview setting at a time')
  assert(key=='isMuted' or key=='volume','Unknown preview setting')
  if key=='isMuted' then
   assert(type(item)=='boolean','isMuted must be boolean')
  else
   assert(type(item)=='number' and item==item and item>=0 and item<=1,'volume must be finite and within 0..1')
  end
  field,value=key,item
 end
 assert(field~=nil,'Preview settings cannot be empty')
 if field=='isMuted' then
  mix_settings.isMuted=value
  local effective=value and 0 or mix_settings.volume
  mix_settings.videoVolume=effective;mix_settings.musicVolume=effective;mix_settings.fxVolume=effective
 else
  mix_settings.volume=value;mix_settings.fxVolume=value
  -- Original SoundManager quirk: changing volume while muted restores effects only.
  if not mix_settings.isMuted then mix_settings.videoVolume=value;mix_settings.musicVolume=value end
 end
 render_serial=render_serial+1
 return read_mix()
end
safe.pegasusBindPreviewKeys=function(callback)
 assert(active,'Preview is closed')
 assert(type(callback)=='function','Key binding needs a callback')
 assert(#key_bindings<64,'Too many active key bindings')
 local binding={callback=callback}
 key_bindings[#key_bindings+1]=binding
 invalidate_input()
 return function()
  if not binding.callback then return false end
  binding.callback=nil
  for index,item in ipairs(key_bindings)do
   if item==binding then table.remove(key_bindings,index);break end
  end
  invalidate_input()
  return true
 end
end
local function timeout(fn,delay)
 assert(active,'Preview is closed')
 assert(type(fn)=='function','Timer needs a callback')
 assert(type(delay)=='number' and delay==delay and delay>=0 and delay<=86400,'Invalid timer delay')
 assert(timer_count<1000,'Too many active timers')
 timer_serial=timer_serial+1;timer_count=timer_count+1
 timers[timer_serial]={due=clock+delay,callback=fn,id=timer_serial}
 return timer_serial
end
safe.CltTimer={SetTimeout=timeout,ClearTimer=function(id)
 if timers[id]then timers[id]=nil;timer_count=timer_count-1;return true end
 return false
end}
local video_serial=0
local methods={createLayer='layer',createImage='image',createText='text',createButton='button',createScrollView='scrollview'}
local count=0
local function name(c)
 assert(type(c)=='table' and type(c.name)=='string' and #c.name<=256,'Invalid control name')
end
local ac={var={}}
for method,kind in pairs(methods) do
 ac[method]=function(c)
  name(c)
  if kind=='scrollview'then
   c=copy(c)
   for _,key in ipairs({'visualScope','contentArea'})do
    local pair=c[key];assert(type(pair)=='table' and finite(pair[1]) and finite(pair[2]) and pair[1]>=1 and pair[2]>=1 and pair[1]<=32768 and pair[2]<=32768,'Bounded scroll view geometry required')
    c[key]={pair[1],pair[2]}
   end
   assert(c.layoutMode=='none' and type(c.layerRebuild)=='table' and c.layerRebuild.autoRebuildLayout==false,'Preview scroll view requires explicit fixed content layout')
   assert(c.scrollViewHorizontal==false and c.scrollViewVertical==true and c.scrollViewShowBl==true,'Preview supports a visible vertical-only scroll view')
   c.size={c.visualScope[1],c.visualScope[2]}
  end
  if kind=='button' then assert(type(c.click_func)=='function','Button needs click_func') end
  if not objects[c.name] then count=count+1;assert(count<=1000,'Too many controls')end
  object_serial=object_serial+1;object_generations[c.name]=object_serial;object_kinds[c.name]=kind
  scroll_views[c.name]=kind=='scrollview' and {name=c.name,generation=object_serial,width=c.visualScope[1],height=c.visualScope[2],contentWidth=c.contentArea[1],contentHeight=c.contentArea[2]} or nil
  local created=object_serial
  render_serial=render_serial+1;c._kind=kind;objects[c.name]=c;coroutine.yield()
  if kind=='scrollview'then return created end -- Exact created owner across a yielding command.
 end
end
ac.create_scroll_view=ac.createScrollView
ac.createStyle=function(c)name(c);styles[c.name]=c;render_serial=render_serial+1;coroutine.yield()end
ac.createAudio=function(c)
 name(c);if c.source_type==nil then c.source_type=1 end
 audio[c.name]=c;render_serial=render_serial+1;coroutine.yield()
end
ac.stopAudio=function(c)name(c);audio[c.name]=nil;render_serial=render_serial+1;coroutine.yield()end
ac.remove=function(c)
 name(c);if objects[c.name]then count=count-1 end;objects[c.name]=nil;object_generations[c.name]=nil;object_kinds[c.name]=nil;scroll_views[c.name]=nil
 render_serial=render_serial+1
 local changed=true
 while changed do
  changed=false
  for key,item in pairs(objects)do
   if item.inlayer and item.inlayer~='window' and not objects[item.inlayer]then
    objects[key]=nil;object_generations[key]=nil;object_kinds[key]=nil;scroll_views[key]=nil;count=count-1;changed=true
   end
  end
 end
 coroutine.yield()
end
ac.showVideo=function(c)
 name(c)
 c=copy(c) -- Source-held config tables must not forge later browser report fields.
 local resource=c.res_id or c.resId
 assert(type(resource)=='string','Video needs a resource ID')
 assert(c.loop_type==nil or c.loop_type=='next','Unsupported preview video loop_type')
 video_serial=video_serial+1
 c.generation=video_serial;c.resId=resource;c.status='awaiting_browser';c.time=0;c.fired={};c.requestSerial=0
 c.reportedPaused=nil;c.reportedReadyState=nil;c.reportedRequestSerial=nil;c.reportedAt=nil;c.requestedRate=1;c.rateRequestSerial=0;c.reportedRate=nil
 local events=c.events or {}
 assert(#events<=2000,'Too many video events')
 for _,event in ipairs(events)do
  assert(type(event.time)=='number' and event.time>=0 and type(event.func)=='function','Invalid video callback')
  assert(event.action=='next' or event.action=='freeze','Unsupported video event action')
 end
 table.sort(events,function(a,b)return a.time<b.time end);c.events=events
 videos[c.name]=c
 render_serial=render_serial+1
 coroutine.yield('video_wait',c.name,c.generation)
end
ac.closeVideo=function(c)
 name(c);videos[c.name]=nil;render_serial=render_serial+1
 if waiting[c.name]then resumes[#resumes+1]=waiting[c.name].co;waiting[c.name]=nil end
 coroutine.yield()
end
ac.resumeVideo=function(c)name(c);assert(videos[c.name],'Video not active');videos[c.name].status='playing';videos[c.name].requestSerial=videos[c.name].requestSerial+1;render_serial=render_serial+1;coroutine.yield()end
ac.pauseVideo=function(c)name(c);assert(videos[c.name],'Video not active');videos[c.name].status='paused';videos[c.name].requestSerial=videos[c.name].requestSerial+1;render_serial=render_serial+1;coroutine.yield()end
safe.pegasusReadPreviewVideo=function(name)
 assert(type(name)=='string' and #name>=1 and #name<=256,'Invalid video name')
 local v=active and videos[name];if not v then return nil end
 return {name=name,time=v.time,duration=v.duration,status=v.status,resId=v.resId,generation=v.generation,
  requestSerial=v.requestSerial,reportedPaused=v.reportedPaused,reportedReadyState=v.reportedReadyState,
  reportedRequestSerial=v.reportedRequestSerial,reportedAt=v.reportedAt,requestedRate=v.requestedRate,rateRequestSerial=v.rateRequestSerial,reportedRate=v.reportedRate}
end
safe.pegasusSetPreviewVideoPaused=function(name,paused)
 assert(active,'Preview is closed');assert(type(paused)=='boolean','Video paused must be boolean')
 local v=safe.pegasusReadPreviewVideo(name);assert(v,'Only an existing video can be paused')
 videos[name].status=paused and 'paused' or 'playing';videos[name].requestSerial=videos[name].requestSerial+1;render_serial=render_serial+1
 return safe.pegasusReadPreviewVideo(name)
end
safe.pegasusReadPreviewObjectPresence=function(name)
 assert(type(name)=='string'and #name>=1 and #name<=256,'Invalid object name')
 return active and objects[name]~=nil or false
end
safe.pegasusSetPreviewVideoRate=function(name,rate)
 assert(active,'Preview is closed')
 assert(type(rate)=='number'and rate==rate and rate>=.25 and rate<=4,'Video rate must be finite .25..4')
 assert(safe.pegasusReadPreviewVideo(name),'Only an existing video can change rate')
 local v=videos[name];v.requestedRate=rate;v.requestSerial=v.requestSerial+1;v.rateRequestSerial=v.requestSerial;render_serial=render_serial+1
 return safe.pegasusReadPreviewVideo(name)
end
ac.waitForSeconds=function(c)
 assert(type(c)=='table' and type(c.delay)=='number' and c.delay==c.delay and c.delay>=0 and c.delay<=86400,'Invalid wait delay')
 coroutine.yield('timer_wait',c.delay)
end
ac.showUI=function(c)
 assert(type(c)=='table','Invalid showUI');requests[#requests+1]={name=c.name,ui_id=c.ui_id}
 render_serial=render_serial+1
 assert(#requests<=100,'Too many native UI requests');coroutine.yield()
end
setmetatable(ac,{__index=function(_,k)error('Unsupported ac command: '..tostring(k))end})
safe.ac=ac
safe.print=function(...)
 local out={};for i=1,select('#',...)do out[i]=tostring(select(i,...))end
 logs[#logs+1]=string.sub(table.concat(out,' '),1,4096);if #logs>50 then table.remove(logs,1)end
end
safe._G=safe
local function cancel_task(co,task)
 guarded[co]=nil
 if task.timer and timers[task.timer]then timers[task.timer]=nil;timer_count=timer_count-1 end
 for name,item in pairs(waiting)do if item.co==co then waiting[name]=nil end end
 if task.cancel then task.cancel()end
end
cancel_guarded=function()
 local current=coroutine.running()
 for co,task in pairs(guarded)do if co~=current and not task.valid()then cancel_task(co,task)end end
end
local function pump(co)
 local task=guarded[co]
 if task and not task.valid()then cancel_task(co,task);return end
 exhausted=false
 local steps=0
 repeat
  local ok,kind,name,generation=coroutine.resume(co);assert(ok,tostring(kind));steps=steps+1
  assert(steps<=2000,'Too many yielded commands')
  if kind=='video_wait' then
   if task and not task.valid()then cancel_task(co,task);return end
   if videos[name] and videos[name].generation==generation then waiting[name]={co=co,generation=generation} end
   return
  end
  if kind=='timer_wait'then
   if task and not task.valid()then cancel_task(co,task);return end
   local id=timeout(function()if task then task.timer=nil end;pump(co)end,name)
   if task then task.timer=id end
   return
  end
 until coroutine.status(co)=='dead'
 guarded[co]=nil
end
local function run(fn,guard,cancel)
 local co=coroutine.create(fn);local ticks=0
 if guard then guarded[co]={valid=guard,cancel=cancel}end
 sethook(co,function()ticks=ticks+10000;if ticks>=instruction_limit then exhausted=true;error('Preview instruction limit exceeded')end end,'',10000)
 pump(co)
end
local function dispatch_input(event,epoch)
 local batch={};for i=#input_bindings,1,-1 do batch[#batch+1]=input_bindings[i]end
 for _,binding in ipairs(batch)do
  if epoch~=input_epoch then return end
  if binding.callback and binding.callback(copy(event))then return end
 end
end
drain_inputs=function()
 if input_busy or not active then return end
 while #input_queue>0 do
  local item=table.remove(input_queue,1)
  if item.epoch==input_epoch then
   input_busy=true
   run(function()
    dispatch_input(item.event,item.epoch);input_busy=false;drain_inputs()
   end,function()return active and item.epoch==input_epoch end,function()input_busy=false end)
   return
  end
 end
end
local function input_event(event)
 assert(#input_queue<128,'Too many queued preview inputs')
 event.time=clock;event.frame=frame_number
 input_queue[#input_queue+1]={event=copy(event),epoch=input_epoch};drain_inputs()
end
local function cancel_input_event(event)
 event.time=clock;event.frame=frame_number
 local epoch=input_epoch
 run(function()dispatch_input(event,epoch)end,function()return active and epoch==input_epoch end)
end
local function dispatch_frames()
 local batch={}
 for _,binding in ipairs(frame_bindings)do
  if binding.callback and not binding.busy then binding.busy=true;batch[#batch+1]=binding end
 end
 if #batch==0 then return 0 end
 local current=nil;local stamp,number=clock,frame_number
 local function release()for _,binding in ipairs(batch)do binding.busy=false end end
 run(function()
  for _,binding in ipairs(batch)do
   current=binding
   if binding.callback then binding.callback(stamp,number)end
   binding.busy=false
  end
 end,function()return active and (not current or current.callback~=nil)end,release)
 return #batch
end
local function drain_resumes()
 local count=0
 while #resumes>0 do
  count=count+1;assert(count<=2000,'Too many video continuations')
  local co=table.remove(resumes,1)
  if coroutine.status(co)~='dead'then pump(co)end
 end
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
 drain_resumes()
end
local function key_event(key,repeated)
 -- Freeze LIFO order. New bindings wait for the next event; removed ones are skipped.
 local batch={}
 for index=#key_bindings,1,-1 do batch[#batch+1]=key_bindings[index]end
 run(function()
  for _,binding in ipairs(batch)do
   if binding.callback and binding.callback(key,repeated)then return end
  end
 end)
 drain_resumes()
end
local function video_event(name,generation,kind,time,duration,paused,readyState,requestSerial,rate)
 local v=videos[name]
 if not v or v.generation~=generation then return end
 assert(type(time)=='number' and time==time and time>=0 and time<86400,'Invalid video time')
 assert(kind=='playing' or kind=='time' or kind=='ended' or kind=='error' or kind=='state','Invalid video event')
 if paused~=nil then
  assert(type(paused)=='boolean'and type(readyState)=='number'and readyState%1==0 and readyState>=0 and readyState<=4,'Invalid reported media state')
  assert(requestSerial==nil or requestSerial<=v.requestSerial,'Unknown video request serial')
  v.reportedPaused=paused;v.reportedReadyState=readyState;v.reportedRequestSerial=requestSerial;v.reportedAt=clock
  v.reportedRate=rate -- Absent on old clients; never reuse an old report as a new rate ACK.
 end
 if type(duration)=='number' and duration==duration and duration>0 and duration<86400 then v.duration=duration end
 if kind=='state'then return end -- Media-state observations never advance the source timeline.
 if kind=='error' then v.status='error';error('Browser video decoding/playback failed: '..name) end
 if kind=='playing' and v.status=='awaiting_browser' then v.status='playing' end
 if v.status~='playing' then return end
 v.time=time
 for index,event in ipairs(v.events)do
  if not v.fired[index] and event.time<=time+0.000001 then
   v.fired[index]=true
   if event.action=='freeze'then v.status='paused';v.time=event.time;v.requestSerial=v.requestSerial+1 end
   run(event.func)
   if videos[name]~=v or v.status~='playing'then drain_resumes();return end
  end
 end
 if kind=='ended'then
  assert(v.duration and time>=v.duration-0.25,'Video end requires browser duration/time confirmation')
  local task=waiting[name];waiting[name]=nil;videos[name]=nil
  if task and task.generation==generation then pump(task.co) end
 end
 drain_resumes()
end
local function advance_clock(now)
 assert(type(now)=='number' and now==now and now>=clock and now<math.huge,'Invalid preview clock')
 clock=now
 -- Freeze this batch: timers created by callbacks run on a later scheduler tick.
 local due={}
 for _,timer in pairs(timers)do if timer.due<=clock then due[#due+1]=timer end end
 table.sort(due,function(a,b)if a.due==b.due then return a.id<b.id end;return a.due<b.due end)
 local fired=0
 for _,timer in ipairs(due)do
  if timers[timer.id]then
   timers[timer.id]=nil;timer_count=timer_count-1
   run(timer.callback);drain_resumes();fired=fired+1
  end
 end
 drain_inputs()
 local frames=0
 if clock-last_frame_time>=1/60 then
  last_frame_time=clock;frame_number=frame_number+1;frames=dispatch_frames()
 end
 return fired,frames
end
local function dispose()
 active=false
 invalidate_input()
 for _,registry in ipairs({input_bindings,frame_bindings})do
  for i=#registry,1,-1 do registry[i].callback=nil;registry[i]=nil end
 end
 cancel_guarded();viewport=nil;input_busy=false
 for index=#key_bindings,1,-1 do
  key_bindings[index].callback=nil;key_bindings[index]=nil
 end
 for id in pairs(timers)do timers[id]=nil end
 timer_count=0
 for name in pairs(waiting)do waiting[name]=nil end
 for index=#resumes,1,-1 do resumes[index]=nil end
end
return {start=start,click=click,key_event=key_event,dispose=dispose,
 input_event=input_event,cancel_input_event=cancel_input_event,invalidate_input=invalidate_input,
 input_epoch=function()return input_epoch end,input_binding_count=function()return #input_bindings end,
 frame_binding_count=function()return #frame_bindings end,input_pending_count=function()return #input_queue+(input_busy and 1 or 0)end,
 preview_time=function()return clock end,preview_frame=function()return frame_number end,
 sync_clock=function(now)assert(type(now)=='number' and now==now and now>=clock and now<math.huge,'Invalid preview clock');clock=now end,
 render_generation=function()return render_serial end,button_generation=function(name)return object_generations[name]end,
 position_generation=function()return position_serial end,scroll_event=scroll_event,read_scroll=safe.pegasusReadPreviewScrollView,
 set_viewport=function(value)viewport=copy(value)end,read_viewport=function()return viewport and copy(viewport) or nil end,
 key_binding_count=function()return #key_bindings end,objects=objects,styles=styles,audio=audio,
 videos=videos,video_event=video_event,advance_clock=advance_clock,timers=timers,
 requests=requests,logs=logs,variables=ac.var,mix_settings=mix_settings}
'''


class PreviewGame:
    def __init__(self, bundle, clock=None):
        try:
            from lupa.lua54 import LuaRuntime
        except ImportError:
            raise BridgeError('预览需要可选依赖：uv sync --extra preview --inexact') from None
        self.bundle = bundle
        self.clock = clock or time.monotonic
        self.lua = LuaRuntime(unpack_returned_tuples=True, register_eval=False,
                              register_builtins=False, max_memory=64 * 1024 * 1024)
        self.runtime = self.lua.execute(BOOTSTRAP, bundle.get('instruction_limit', 5_000_000),self.clock(),bundle.get('allow_text_load',False),bundle.get('text_load_limit_bytes',2*1024*1024))
        self.revision = 0
        self.fault = None
        self.closed = False
        self._input_nonce = secrets.token_hex(16)
        self.input_sequence = 0
        self._contacts = {}
        self._hovers = {}
        self._contact_epoch = None
        for key, value in bundle['variables'].items():
            self.runtime.variables[key] = value
        self._run(self.runtime.start, bundle['source'])

    def _run(self, fn, *args):
        if self.closed:
            raise BridgeError('预览已停止')
        try:
            fn(*args)
            self.revision += 1
        except Exception as exc:
            self.fault = str(exc)[:1200]
            raise BridgeError('Lua 预览执行失败；当前会话需重置：' + self.fault) from None

    def click(self, name, revision):
        if self.closed:
            raise BridgeError('预览已停止')
        if self.fault:
            raise BridgeError('预览已发生执行错误，请先 reset')
        if type(revision) is not int or revision != self.revision:
            raise BridgeError('预览状态已变化，请重新读取 state 后点击')
        if not isinstance(name, str) or not 1 <= len(name) <= 256:
            raise BridgeError('按钮名称无效')
        c = self.runtime.objects[name]
        if c is None or c['_kind'] != 'button' or c['visible'] is False:
            raise BridgeError('按钮不存在或不可见')
        self._release_contacts('click')
        self.runtime.invalidate_input()
        self._run(self.runtime.click, name)
        return self.snapshot()

    def key(self, key, revision, repeat=False):
        if self.closed:
            raise BridgeError('预览已停止')
        if self.fault:
            raise BridgeError('预览已发生执行错误，请先 reset')
        if type(revision) is not int or revision != self.revision:
            raise BridgeError('预览状态已变化，请重新读取 state 后发送按键')
        if not isinstance(key, str) or key not in PREVIEW_KEYS:
            raise BridgeError('不支持的预览按键')
        if type(repeat) is not bool:
            raise BridgeError('按键 repeat 必须为布尔值')
        self._release_contacts('key')
        self.runtime.invalidate_input()
        self._run(self.runtime.key_event, key, repeat)
        return self.snapshot()

    def close(self):
        if not self.closed:
            self.closed = True
            self.runtime.dispose()
            self._contacts.clear()
            self._hovers.clear()

    def _generation(self):
        return self._input_nonce + ':' + str(self.runtime.input_epoch())

    def _sync_contacts(self):
        epoch = self.runtime.input_epoch()
        if epoch != self._contact_epoch:
            self._contacts.clear()
            self._hovers.clear()
            self._contact_epoch = epoch
        for name, generation in list(self._hovers.items()):
            c = self.runtime.objects[name]
            if c is None or c['_kind'] != 'button' or self.runtime.button_generation(name) != generation:
                del self._hovers[name]

    def _release_contacts(self, reason):
        self._sync_contacts()
        try:
            for contact in list(self._contacts.values()):
                event = dict(contact, kind='pointer', phase='cancel', reason=reason)
                self.runtime.cancel_input_event(self.lua.table_from(event))
        except Exception as exc:
            self.fault = str(exc)[:1200]
            raise BridgeError('Lua 输入取消失败：' + self.fault) from None
        finally:
            self._contacts.clear()

    def _visible_button(self, name):
        c = self.runtime.objects[name]
        if c is None or c['_kind'] != 'button' or c['visible'] is False:
            raise BridgeError('hover 按钮不存在或不可见')
        parent, seen = c['inlayer'], set()
        while parent and parent != 'window':
            if parent in seen:
                raise BridgeError('按钮父图层循环')
            seen.add(parent)
            item = self.runtime.objects[parent]
            if item is None or item['visible'] is False:
                raise BridgeError('按钮父图层隐藏或已移除')
            parent = item['inlayer']
        return c

    def input(self, event, revision):
        """Strict serialized raw input; no click-to-gesture conversion.

        A client must retain generation captured at observation, even while it
        serializes high-frequency events against each acknowledged revision.
        """
        if self.closed or self.fault:
            raise BridgeError('预览已停止或执行错误，请 reset')
        if type(revision) is not int or revision != self.revision:
            raise BridgeError('预览状态已变化，请重新读取 state 后发送输入')
        if not isinstance(event, dict):
            raise BridgeError('输入 event 必须是对象')
        common = {'kind', 'generation', 'sequence'}
        fields = {
            'pointer': {'phase', 'id', 'pointer_type', 'x', 'y'},
            'cursor': {'phase', 'pointer_type', 'x', 'y'},
            'hover': {'phase', 'name', 'button_generation'},
            'wheel': {'x', 'y', 'deltaX', 'deltaY', 'deltaMode'},
            'viewport': {'left', 'top', 'width', 'height', 'scale', 'pixelRatio',
                         'canvasWidth', 'canvasHeight', 'clientWidth', 'clientHeight'},
        }
        kind = event.get('kind')
        if not isinstance(kind, str) or kind not in fields or set(event) != common | fields[kind]:
            raise BridgeError('输入 event 类型、未知字段或必需字段无效')
        self._sync_contacts()
        if type(event['generation']) is not str or event['generation'] != self._generation():
            raise BridgeError('输入 generation 已过期，旧控件或 modal 事件不可重放')
        if type(event['sequence']) is not int or event['sequence'] != self.input_sequence + 1:
            raise BridgeError('输入 sequence 必须严格连续且不得重放')

        def number(key, low, high):
            value = event[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise BridgeError('输入数字字段无效：' + key)
            return value

        if kind == 'viewport':
            for key in ('left', 'top'):
                number(key, -100000, 100000)
            for key in ('width', 'height', 'clientWidth', 'clientHeight'):
                number(key, 1, 32768)
            for key in ('canvasWidth', 'canvasHeight'):
                number(key, 100, 4096)
            number('scale', .0001, 100)
            number('pixelRatio', .1, 16)
            if any(abs(event[size] - event[canvas] * event['scale']) > 1
                   for size, canvas in [('width', 'canvasWidth'), ('height', 'canvasHeight')]):
                raise BridgeError('viewport bounds 与实际 scale 不一致')
        else:
            if not self.runtime.input_binding_count():
                raise BridgeError('没有活动的 preview input 订阅')
            if kind == 'pointer':
                if event['phase'] not in ('down', 'move', 'up', 'cancel') or event['pointer_type'] not in ('mouse', 'touch', 'pen'):
                    raise BridgeError('pointer phase/type 无效')
                if type(event['id']) is not int or not 0 <= event['id'] <= 2147483647:
                    raise BridgeError('pointer id 无效')
                number('x', 0, 1920);number('y', 0, 1080)
                if self.runtime.read_viewport() is None:
                    raise BridgeError('pointer 需要先报告真实 viewport')
                contact = self._contacts.get(event['id'])
                if event['phase'] == 'down':
                    if contact or len(self._contacts) >= 10:
                        raise BridgeError('pointer 重复 down 或 contact 数超过10')
                elif not contact or contact['pointer_type'] != event['pointer_type']:
                    raise BridgeError('pointer 必须属于已观测的 down 序列')
            elif kind == 'cursor':
                if event['phase'] not in ('move', 'leave') or event['pointer_type'] not in ('mouse', 'pen'):
                    raise BridgeError('cursor phase/type 无效；touch 不提供悬停')
                number('x', 0, 1920);number('y', 0, 1080)
                if self.runtime.read_viewport() is None:
                    raise BridgeError('cursor 需要先报告真实 viewport')
            elif kind == 'hover':
                if event['phase'] not in ('enter', 'leave') or type(event['name']) is not str or not 1 <= len(event['name']) <= 256:
                    raise BridgeError('hover name/phase 无效')
                self._visible_button(event['name'])
                if type(event['button_generation']) is not int or self.runtime.button_generation(event['name']) != event['button_generation']:
                    raise BridgeError('hover 按钮已被移除或同名替换')
                if event['phase'] == 'leave' and self._hovers.get(event['name']) != event['button_generation']:
                    raise BridgeError('hover leave 没有对应 enter')
            elif kind == 'wheel':
                number('x', 0, 1920);number('y', 0, 1080)
                number('deltaX', -100000, 100000);number('deltaY', -100000, 100000)
                if type(event['deltaMode']) is not int or event['deltaMode'] not in (0, 1, 2):
                    raise BridgeError('wheel deltaMode 无效')
        if self.runtime.input_pending_count() >= 128:
            raise BridgeError('preview input 队列已满')
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < self.runtime.preview_time():
            raise BridgeError('preview monotonic 时钟倒退或无效')
        self.runtime.sync_clock(now)
        normalized = dict(event)
        if kind == 'viewport':
            viewport = {key: event[key] for key in fields[kind]}
            previous = self.runtime.read_viewport()
            if previous is not None and any(previous[k] != v for k, v in viewport.items()):
                self._release_contacts('viewport_changed')
                self.runtime.invalidate_input()
            self.runtime.set_viewport(self.lua.table_from(viewport))
            normalized['generation'] = self._generation()
        # A measurement with no input subscribers must not stale a keyboard
        # action merely by recording its DOM bounds.
        before_revision = self.revision
        metadata_only = kind == 'viewport' and not self.runtime.input_binding_count()
        self._run(self.runtime.input_event, self.lua.table_from(normalized))
        if metadata_only:self.revision = before_revision
        self.input_sequence = event['sequence']
        self._sync_contacts()
        if event['generation'] == self._generation():
            if kind == 'pointer':
                if event['phase'] in ('up', 'cancel'):
                    self._contacts.pop(event['id'], None)
                else:
                    self._contacts[event['id']] = {k: event[k] for k in ('id', 'pointer_type', 'x', 'y')}
            elif kind == 'hover':
                if event['phase'] == 'enter':self._hovers[event['name']] = event['button_generation']
                else:self._hovers.pop(event['name'], None)
        return self.snapshot()

    def scroll_event(self, payload):
        if self.closed or self.fault:
            raise BridgeError('预览已停止或发生执行错误')
        fields={'name','generation','top','left','scrollHeight','scrollWidth','clientHeight','clientWidth'}
        if not isinstance(payload,dict) or set(payload)!=fields:
            raise BridgeError('滚动报告字段无效')
        name,generation=payload['name'],payload['generation']
        if not isinstance(name,str) or not 1<=len(name)<=256 or type(generation)is not int or generation<1:
            raise BridgeError('滚动报告对象无效')
        values=[payload[k] for k in ('top','left','scrollHeight','scrollWidth','clientHeight','clientWidth')]
        if any(type(v)not in(int,float) or not math.isfinite(v) or not 0<=v<=32769 for v in values):
            raise BridgeError('滚动报告数值无效')
        # Readback metadata never dispatches source input/timeline callbacks.
        try:
            changed=self.runtime.scroll_event(name,generation,*values)
        except Exception as exc:
            raise BridgeError('滚动报告与实际控件配置不符：'+str(exc)[:200]) from None
        if changed:self.revision+=1
        return self.snapshot()

    def video_event(self, payload):
        if self.closed:
            raise BridgeError('预览已停止')
        if self.fault:
            raise BridgeError('预览已发生执行错误，请先 reset')
        if not isinstance(payload, dict):
            raise BridgeError('视频事件必须是对象')
        name, generation = payload.get('name'), payload.get('generation')
        kind, time, duration = payload.get('kind'), payload.get('time'), payload.get('duration')
        paused,ready_state,request_serial,rate=payload.get('paused'),payload.get('readyState'),payload.get('requestSerial'),payload.get('playbackRate')
        reported='paused' in payload or 'readyState' in payload or 'requestSerial' in payload or 'playbackRate'in payload
        if (not isinstance(name, str) or not 1 <= len(name) <= 256 or
                type(generation) is not int or generation < 1 or kind not in ('playing','time','ended','error','state') or
                type(time) not in (int,float) or not 0 <= time < 86400 or
                duration is not None and (type(duration) not in (int,float) or not 0 < duration < 86400) or
                reported and (type(paused) is not bool or type(ready_state) is not int or not 0 <= ready_state <= 4 or
                              request_serial is not None and (type(request_serial) is not int or not 0 <= request_serial <= 9007199254740991) or
                              rate is not None and(type(rate)not in(int,float)or not math.isfinite(rate)or not .25<=rate<=4))):
            raise BridgeError('视频事件字段无效')
        if kind=='state' and not reported:
            raise BridgeError('媒体 state 需要实际 paused/readyState')
        current=self.runtime.videos[name]
        if (reported and request_serial is not None and current is not None and current['generation']==generation and
                request_serial>current['requestSerial']):
            raise BridgeError('未知视频请求版本')
        try:
            if reported:
                now=self.clock()
                if type(now) not in (int,float) or not math.isfinite(now) or now<self.runtime.preview_time():
                    raise BridgeError('preview monotonic 时钟倒退或无效')
                self.runtime.sync_clock(now)
            self.runtime.video_event(name,generation,kind,time,duration,paused,ready_state,request_serial,rate)
            self.revision += 1
        except Exception as exc:
            self.fault = str(exc)[:1200]
            raise BridgeError('Lua 视频回调执行失败：'+self.fault) from None
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
                  ('objects', 'styles', 'audio', 'videos', 'timers', 'requests', 'logs', 'variables', 'mix_settings')}
        self._sync_contacts()
        for name, item in result['objects'].items():
            if item.get('_kind') in ('button','scrollview'):
                item['preview_generation'] = self.runtime.button_generation(name)
        result['scroll_views']={name:plain(self.runtime.read_scroll(name)) for name,item in result['objects'].items() if item.get('_kind')=='scrollview'}
        result.update(mode='lua_approximation', native_runtime=False,
                      key_binding_count=self.runtime.key_binding_count(),
                      input_binding_count=self.runtime.input_binding_count(),frame_binding_count=self.runtime.frame_binding_count(),
                      input_pending_count=self.runtime.input_pending_count(),pointer_contact_count=len(self._contacts),
                      hover_count=len(self._hovers),input_generation=self._generation(),input_sequence=self.input_sequence,
                      viewport=plain(self.runtime.read_viewport()),preview_time=self.runtime.preview_time(),
                      preview_frame=self.runtime.preview_frame(),render_generation=self.runtime.render_generation(),
                      position_generation=self.runtime.position_generation(),
                      revision=self.revision, fault=self.fault, limitations=LIMITATIONS,
                      title=self.bundle['title'], width=self.bundle['width'], height=self.bundle['height'],
                      script_sha256=self.bundle['script_sha256'])
        json.dumps(result, allow_nan=False)
        return result

    def advance(self,now=None):
        if self.closed or self.fault:
            return False
        try:
            fired,frames=self.runtime.advance_clock(self.clock() if now is None else now)
            if fired:
                self.revision+=1
            return bool(fired or frames)
        except Exception as exc:
            self.fault='Lua timer callback failed: '+str(exc)[:1000]
            self.revision+=1
            return True
