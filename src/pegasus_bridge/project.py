"""Explicit-file project manifests; deterministic ZIPs with a content ledger."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

from .core import BridgeError, positive_id, SECRET_KEY
from .ui import bounded_errors
from .validation import validate_source
from .workfiles import read_json, child, new_text


def no_secrets(value):
    if isinstance(value,dict):
        if any(SECRET_KEY.search(str(k)) for k in value):raise BridgeError('工程 JSON 含凭据或会话字段，拒绝打包')
        for v in value.values():no_secrets(v)
    elif isinstance(value,list):
        for v in value:no_secrets(v)


@bounded_errors
def project_check(manifest_path):
    manifest_path=Path(manifest_path).expanduser().resolve();data=read_json(manifest_path)
    if not isinstance(data,dict) or set(data)-{'version','title','game_id','scenes','assets','variables','supporting_files'} or data.get('version')!=1:
        raise BridgeError('工程清单版本或字段无效')
    no_secrets(data);positive_id(data.get('game_id'))
    scenes=data.get('scenes');assets=data.get('assets',{});support=data.get('supporting_files',[])
    if not isinstance(scenes,list) or not 1<=len(scenes)<=100 or not isinstance(assets,dict) or len(assets)>1000 or not isinstance(support,list) or len(support)>200:
        raise BridgeError('工程清单数量无效')
    if not isinstance(data.get('variables',{}),dict):raise BridgeError('variables 须为对象')
    files={};total=0
    allowed={'.lua','.json','.png','.jpg','.jpeg','.webp','.wav','.mp3','.ogg','.md','.txt'}
    def add(name):
        nonlocal total
        file=child(manifest_path.parent,name)
        if file.suffix.lower() not in allowed or any(part.startswith('.') for part in Path(name).parts) or not file.is_file():raise BridgeError('工程文件类型或路径无效')
        normalized=file.relative_to(manifest_path.parent).as_posix()
        if Path(name).as_posix()!=normalized:
            raise BridgeError('工程引用须为规范化相对路径，不接受符号链接别名或父目录跳转')
        if normalized in ('project.json','checksums.json'):raise BridgeError('project.json 与 checksums.json 是打包保留名')
        if normalized in files:return file
        if file.stat().st_size>32*1024*1024:raise BridgeError('单个工程文件超过 32 MiB')
        raw=file.read_bytes();total+=len(raw)
        if total>256*1024*1024:raise BridgeError('工程文件总计超过 256 MiB')
        if file.suffix.lower()=='.json':no_secrets(read_json(file))
        files[normalized]=dict(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        return file
    for key,name in assets.items():
        if not isinstance(key,str) or not re.fullmatch(r'\$[1-9]\d*',key):raise BridgeError('素材映射键必须为 $数字 ID')
        add(name)
    checks=[];ids=set()
    for row in scenes:
        if not isinstance(row,dict) or set(row)!={'scene_id','script'}:raise BridgeError('场景清单字段无效')
        sid=positive_id(row['scene_id'])
        if sid in ids:raise BridgeError('重复场景 ID')
        ids.add(sid);file=add(row['script'])
        if file.suffix.lower()!='.lua':raise BridgeError('场景须为 Lua')
        checks.append(dict(scene_id=sid,**validate_source(file.read_text(encoding='utf-8-sig'),list(assets))))
    for name in support:add(name)
    return dict(valid=all(c['valid'] for c in checks),files=files,total_bytes=total,scenes=checks,manifest=data,native_runtime_verified=False)


@bounded_errors
def project_pack(manifest_path,output_path):
    report=project_check(manifest_path)
    if not report['valid']:raise BridgeError('工程检查失败；请先处理源码或素材缺失')
    root=Path(manifest_path).expanduser().resolve().parent
    output=Path(output_path).expanduser().resolve()
    if output.suffix.lower()!='.zip':raise BridgeError('输出须为新的 .zip 文件')
    output.parent.mkdir(parents=True,exist_ok=True)
    # Read and verify bytes before creating the ZIP, so input races cannot create a false ledger.
    frozen={}
    for name,meta in report['files'].items():
        raw=child(root,name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise BridgeError('工程文件在打包前发生变化')
        frozen[name]=raw
    frozen['project.json']=json.dumps(report['manifest'],ensure_ascii=False,sort_keys=True,indent=2).encode()
    frozen['checksums.json']=json.dumps({k:hashlib.sha256(v).hexdigest() for k,v in sorted(frozen.items())},sort_keys=True,indent=2).encode()
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(frozen.items()):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,raw)
    return dict(path=str(output),files=len(frozen),sha256=hashlib.sha256(output.read_bytes()).hexdigest(),verified=True)
