"""Character and audio manifests compiled at an explicit Lua node hook."""
import json
import math
from pathlib import Path
import re
import uuid

from .core import BridgeError, positive_id
from .ui import UIService, bounded_errors
from .validation import validate_source
from .workfiles import read_json, digest, atomic_json, file_lock, new_text


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',value):
        raise BridgeError('标识须为小写字母开头的 1–40 位字母数字下划线')
    return value


def label(value):
    if not isinstance(value,str) or not 1<=len(value)<=160 or any(ord(c)<32 for c in value):
        raise BridgeError('名称或节点须为不含控制字符的 1–160 位文本')
    return value


def lua(value):
    if value is None:return 'nil'
    if isinstance(value,bool):return 'true' if value else 'false'
    if isinstance(value,(int,float)):return str(value)
    if isinstance(value,str):return json.dumps(value,ensure_ascii=False)
    if isinstance(value,list):return '{'+','.join(lua(v) for v in value)+'}'
    if isinstance(value,dict):return '{'+','.join('['+lua(k)+']='+lua(v) for k,v in value.items())+'}'
    raise BridgeError('无法生成 Lua 值')


def validate_manifest(value):
    if not isinstance(value,dict) or set(value)!={'version','game_id','characters','placements','audio'} or value['version']!=1:
        raise BridgeError('角色配置版本或字段无效')
    positive_id(value['game_id'])
    chars,places,audio=value['characters'],value['placements'],value['audio']
    if not all(isinstance(x,dict) for x in (chars,places,audio)) or len(chars)>100 or len(places)>1000 or len(audio)>1000:
        raise BridgeError('角色或节点配置数量超出范围')
    for key,char in chars.items():
        identifier(key)
        if not isinstance(char,dict) or set(char)!={'name','expressions'}:raise BridgeError('角色字段无效')
        label(char['name'])
        if not isinstance(char['expressions'],dict) or not 1<=len(char['expressions'])<=12:raise BridgeError('每个角色须有 1–12 个表情')
        for expr,res in char['expressions'].items():
            identifier(expr)
            if not isinstance(res,str) or not re.fullmatch(r'\$[1-9]\d*',res):raise BridgeError('角色素材须为 $数字 ID')
    for node,rows in places.items():
        label(node)
        if not isinstance(rows,list) or len(rows)>4:raise BridgeError('每节点最多 4 位角色')
        seen=set()
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'character_id','expression','x','y','width','height','index','inlayer','visible'}:
                raise BridgeError('立绘布局字段无效')
            key=row['character_id']
            if key not in chars or key in seen or row['expression'] not in chars[key]['expressions']:raise BridgeError('角色或表情不存在/重复')
            seen.add(key);label(row['inlayer'])
            if type(row['visible']) is not bool:raise BridgeError('visible 须为布尔值')
            if type(row['index']) is not int:raise BridgeError('层级 index 须为整数')
            for field in ['x','y','width','height','index']:
                v=row[field]
                if type(v) not in (int,float) or not math.isfinite(v) or abs(v)>16384:raise BridgeError('布局数值无效')
            if min(row['width'],row['height'])<=0:raise BridgeError('立绘尺寸须大于 0')
    for node,rows in audio.items():
        label(node)
        if not isinstance(rows,list) or len(rows)>8:raise BridgeError('每节点最多 8 个音频')
        seen=set()
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'name','res_id','loop','volume'}:raise BridgeError('音频字段无效')
            identifier(row['name'])
            if row['name'] in seen:raise BridgeError('节点音频名称重复')
            seen.add(row['name'])
            if not isinstance(row['res_id'],str) or not re.fullmatch(r'\$[1-9]\d*',row['res_id']):raise BridgeError('音频素材 ID 无效')
            if type(row['loop']) is not bool or type(row['volume']) not in (int,float) or not 0<=row['volume']<=100:raise BridgeError('音量须为 0–100，loop 须为布尔值')
    return value


class AuthoringService:
    def __init__(self,bridge,uploader=None):
        self.uploader=uploader or UIService(bridge)

    @bounded_errors
    def init(self,manifest_path,game_id):
        value=dict(version=1,game_id=positive_id(game_id),characters={},placements={},audio={})
        path=new_text(manifest_path,json.dumps(value,ensure_ascii=False,indent=2))
        return dict(manifest_path=str(path),sha256=digest(value))

    @bounded_errors
    def read(self,manifest_path):
        value=validate_manifest(read_json(manifest_path))
        return dict(manifest=value,sha256=digest(value))

    def load(self,path,expected,game_id=None):
        if path.exists():
            value=validate_manifest(read_json(path))
            if expected!=digest(value):raise BridgeError('角色配置已变化；需要当前 expected_sha256')
            if game_id is not None and value['game_id']!=game_id:raise BridgeError('角色配置属于另一作品')
            return value
        if expected is not None:raise BridgeError('角色配置不存在，不能匹配旧哈希')
        if game_id is None:raise BridgeError('请先使用 character-add 创建角色配置')
        return dict(version=1,game_id=positive_id(game_id),characters={},placements={},audio={})

    def save(self,path,value,expected):
        validate_manifest(value)
        if path.exists():
            if expected is None or digest(read_json(path))!=expected:
                raise BridgeError('保存前发现角色配置被其他程序修改，未覆盖')
            new_text(path.with_name(path.name+'.backup-'+uuid.uuid4().hex+'.json'),path.read_text(encoding='utf-8'))
        elif expected is not None:
            raise BridgeError('保存前角色配置被移除，未重新创建')
        atomic_json(path,value)
        return dict(manifest_path=str(path),sha256=digest(value),characters=len(value['characters']),nodes=len(value['placements']))

    @bounded_errors
    def character_add(self,manifest_path,game_id,character_id,name,expressions,expected_sha256=None):
        game_id=positive_id(game_id);identifier(character_id);label(name)
        if not isinstance(expressions,dict) or not 1<=len(expressions)<=12:raise BridgeError('expressions 须为表情名到本地 PNG 路径的映射，最多 12 项')
        # Validate every file before the first upload.
        from PIL import Image
        for key,file in expressions.items():
            identifier(key);p=Path(file).expanduser().resolve()
            if p.suffix.lower()!='.png' or not p.is_file() or p.stat().st_size>20*1024*1024:raise BridgeError('立绘须为 20 MiB 以内 PNG')
            try:
                with Image.open(p) as image:
                    if image.format!='PNG' or max(image.size)>8192:raise BridgeError('立绘 PNG 尺寸无效')
                    image.verify()
            except (OSError,ValueError):raise BridgeError('立绘 PNG 无法解析') from None
        path=Path(manifest_path).expanduser().resolve()
        with file_lock(path.with_name(path.name+'.lock')):
            value=self.load(path,expected_sha256,game_id)
            current=value['characters'].get(character_id,dict(name=name,expressions={}))
            if len(set(current['expressions'])|set(expressions))>12:raise BridgeError('合并后表情超过 12 项')
            if character_id not in value['characters'] and len(value['characters'])>=100:raise BridgeError('角色数量达到上限')
            from hashlib import sha256
            for key,file in expressions.items():
                p=Path(file).expanduser().resolve();fingerprint=sha256(p.read_bytes()).hexdigest()
                alias=f'{character_id}-{key}'
                cp=path.parent/(path.stem+'-uploads')/f'{game_id}-{character_id}-{key}-{fingerprint}.json'
                row=self.uploader.upload_image(game_id,str(p),alias,2,str(cp))
                current['expressions'][key]='$'+str(row['id'])
            current['name']=name;value['characters'][character_id]=current
            return self.save(path,value,expected_sha256)

    @bounded_errors
    def character_place(self,manifest_path,node_id,character_id,expression,layout,expected_sha256):
        label(node_id);identifier(character_id);identifier(expression)
        if not isinstance(layout,dict) or set(layout)-{'x','y','width','height','index','inlayer','visible'}:raise BridgeError('布局包含未知字段')
        path=Path(manifest_path).expanduser().resolve()
        with file_lock(path.with_name(path.name+'.lock')):
            value=self.load(path,expected_sha256)
            row=dict(character_id=character_id,expression=expression,x=960,y=540,width=600,height=900,index=3,inlayer='window',visible=True)
            row.update(layout)
            rows=[v for v in value['placements'].get(node_id,[]) if v['character_id']!=character_id]
            rows.append(row);value['placements'][node_id]=rows
            return self.save(path,value,expected_sha256)

    @bounded_errors
    def audio_bind(self,manifest_path,node_id,name,res_id,loop,volume,expected_sha256):
        label(node_id);identifier(name)
        path=Path(manifest_path).expanduser().resolve()
        with file_lock(path.with_name(path.name+'.lock')):
            value=self.load(path,expected_sha256)
            rows=[v for v in value['audio'].get(node_id,[]) if v['name']!=name]
            rows.append(dict(name=name,res_id=res_id,loop=loop,volume=volume));value['audio'][node_id]=rows
            return self.save(path,value,expected_sha256)


def build_scene(script_path,manifest_path,output_path,node_variable='key',hook_anchor='-- PEGASUS-NODE-HOOK'):
    """The anchor is supplied by the author; never guess arbitrary Lua semantics."""
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',node_variable):raise BridgeError('node_variable 须为 Lua 变量名')
    path=Path(script_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size>1024*1024:raise BridgeError('Lua 源文件不存在或过大')
    source=path.read_text(encoding='utf-8-sig')
    if not isinstance(hook_anchor,str) or not hook_anchor.strip() or source.count(hook_anchor)!=1:
        raise BridgeError('节点插入锚点必须在源文件中恰好出现一次')
    if 'pegasusPresent' in source or 'pegasusCast' in source:raise BridgeError('源文件已含角色模块；请从未编译的源文件重新构建')
    config=validate_manifest(read_json(manifest_path))
    module='local pegasusCast='+lua(config)+'\n'+RUNTIME
    # Append a newline so a comment anchor cannot swallow the function call.
    code=module+'\n'+source.replace(hook_anchor,hook_anchor+'\npegasusPresent('+node_variable+')',1)
    report=validate_source(code)
    if not report['valid']:raise BridgeError('生成脚本未通过校验：'+json.dumps(report['errors'],ensure_ascii=False))
    output=new_text(output_path,code)
    return dict(script_path=str(output),sha256=report['sha256'],manifest_sha256=digest(config),validation=report,
                note='仅生成本地 Lua；用 apply-scene 或批量计划保存。每次节点钩子会隐藏旧立绘并停止本模块音频。')


RUNTIME='''
-- PEGASUS-AUTHORING-V1: reserved pgas_actor_ / pgas_audio_ control names.
local pegasusActorSlots={}
local pegasusAudioNames={}
local function pegasusPresent(nodeId)
 for name,row in pairs(pegasusActorSlots)do
  ac.createImage({name=name,inlayer=row.inlayer,resId=row.resId,pos={row.x,row.y},size={row.width,row.height},anchor={50,50},index=row.index,visible=false,raycast=false,opacity=100})
 end
 for name,active in pairs(pegasusAudioNames)do
  if active then ac.stopAudio({name=name});pegasusAudioNames[name]=false end
 end
 for _,row in ipairs(pegasusCast.placements[nodeId] or {})do
  local name="pgas_actor_"..row.character_id
  local resId=pegasusCast.characters[row.character_id].expressions[row.expression]
  pegasusActorSlots[name]={inlayer=row.inlayer,resId=resId,x=row.x,y=row.y,width=row.width,height=row.height,index=row.index}
  ac.createImage({name=name,inlayer=row.inlayer,resId=resId,pos={row.x,row.y},size={row.width,row.height},anchor={50,50},index=row.index,visible=row.visible,raycast=false,opacity=100})
 end
 for _,row in ipairs(pegasusCast.audio[nodeId] or {})do
  local name="pgas_audio_"..row.name
  ac.createAudio({name=name,resId=row.res_id,loop=row.loop,targetVolume=row.volume,source_type=2})
  pegasusAudioNames[name]=true
 end
end
'''
