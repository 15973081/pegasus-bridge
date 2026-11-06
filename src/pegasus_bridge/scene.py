"""Scene edits using the editor's command protocol, with revision guards."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import os
import secrets
import uuid
import httpx

from .core import BridgeError, positive_id, redact
from .ui import UIService, BASE, bounded_errors


def source_hash(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def _owned_client_session(game_id):
    """SID is a client collaboration nonce, independent of API authentication."""
    from .workfiles import atomic_json, file_lock, read_json
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    path = root / 'PegasusBridge/client-sessions' / f'{positive_id(game_id)}.json'
    with file_lock(path.with_suffix('.lock')):
        if path.exists():
            record = read_json(path, limit=4096)
            sid = record.get('sid') if isinstance(record, dict) else None
            if (not isinstance(record, dict) or record.get('game_id') != game_id
                    or not isinstance(sid, str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{4}_[A-Za-z0-9_-]{13}_[A-Za-z0-9_-]{20}', sid)):
                raise BridgeError('桥接器协作标识缓存无效；请保留文件并检查 client-sessions 记录')
            return sid
        alphabet = '_-0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
        sid = '_'.join(''.join(secrets.choice(alphabet) for _ in range(size)) for size in (4, 13, 20))
        atomic_json(path, {'game_id': game_id, 'sid': sid, 'owner': 'pegasus-bridge'})
        return sid


def editor_session(game_id):
    game_id = positive_id(game_id)
    import winreg
    fallback = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\com.NetEase\Pegasus') as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, value, _ = winreg.EnumValue(key, i)
                exact = f'CACHE_GAME_SID_{game_id}'
                default = 'CACHE_GAME_SID_0'
                is_exact = name == exact or name.startswith(exact + '_')
                is_default = name == default or name.startswith(default + '_')
                if is_exact or is_default:
                    value = value.decode('utf-8').rstrip('\0') if isinstance(value, bytes) else value
                    if is_exact and value:
                        return value
                    if is_default and value:
                        fallback = value
    except FileNotFoundError:
        pass
    return fallback or _owned_client_session(game_id)


class SceneService(UIService):
    def __init__(self, bridge, transport=None, session_resolver=editor_session):
        super().__init__(bridge, transport)
        self.session_resolver = session_resolver

    @bounded_errors
    def native_config_status(self, game_id, scene_id=None, check_material_ids=None):
        game_id=positive_id(game_id)
        checks=check_material_ids or {}
        if not isinstance(checks,dict) or set(checks)-{'image','audio','video'}:
            raise BridgeError('素材核对仅支持 image/audio/video 分类')
        if any(not isinstance(ids,list) or len(ids)>2000 for ids in checks.values()) or sum(len(ids) for ids in checks.values())>4000:
            raise BridgeError('素材核对每类最多2000个ID，总计最多4000个')
        for ids in checks.values():
            if any(type(ident) is not int or ident<=0 for ident in ids) or len(set(ids))!=len(ids):
                raise BridgeError('核对素材ID须为不重复的正整数')
        path=f'{BASE}/game/{game_id}/config'
        if scene_id is not None:path+=f'/scene/{positive_id(scene_id)}'
        headers,_=self.bridge._auth()
        headers={**headers,'User-Agent':'3DAvg/1.0 yidun','Referer':'https://avg.163.com/'}
        with httpx.Client(headers=headers,timeout=180,follow_redirects=False,transport=self.transport) as api:
            response=api.get(path)
        if response.status_code!=200:
            return dict(game_id=game_id,scene_id=scene_id,status='http_error',http_status=response.status_code,valid=False)
        body=response.json()
        if not isinstance(body,dict):raise BridgeError('原生构建响应结构异常')
        state=body.get('state',{})
        data=body.get('data')
        rows=data if isinstance(data,list) else data.get('data',[]) if isinstance(data,dict) else []
        if not isinstance(rows,list):rows=[]
        errors=[{k:(redact(str(r[k]))[:200] if isinstance(r[k],str) else r[k])
                 for k in ('sourceId','sourcePath','source','reviewType','reason','reasonName','command') if k in r}
                for r in rows[:12] if isinstance(r,dict)] if state.get('code')!=200000 else []
        valid=state.get('code')==200000
        resources=data.get('resource') if isinstance(data,dict) else None
        inventory={};material_check={}
        for category in ('image','audio','video'):
            entries=resources.get(category) if isinstance(resources,dict) else None
            available=isinstance(entries,dict)
            inventory[category]={'available':available,'count':len(entries) if available else None}
            if category in checks:
                keys={str(k).removeprefix('$') for k in entries} if available else set()
                missing=[ident for ident in checks[category] if str(ident) not in keys] if available else None
                material_check[category]={'available':available,'requested':len(checks[category]),
                    'found':len(checks[category])-len(missing) if available else None,'missing':missing,
                    'passed':valid and available and not missing}
        def shape(value,depth=0):
            if isinstance(value,dict) and depth<2:
                return {str(k):shape(v,depth+1) for k,v in list(value.items())[:16]}
            if isinstance(value,list):return {'list_length':len(value),'item_shape':shape(value[0],depth+1) if value and depth<2 else None}
            return {'type':type(value).__name__,'length':len(value)} if isinstance(value,str) else {'type':type(value).__name__}
        return dict(game_id=game_id,scene_id=scene_id,status='accepted' if valid else 'rejected',
                    valid=valid,code=state.get('code'),message=redact(str(state.get('message','')))[:200],
                    error_count=len(rows) if not valid else 0,errors=errors,
                    response_sha256=source_hash(json.dumps(body,ensure_ascii=False,sort_keys=True)),
                    config_commands_sha256=source_hash(data['commands']) if isinstance(data,dict) and isinstance(data.get('commands'),str) else None,
                    data_shape=shape(data),
                    resource_inventory=inventory,material_check=material_check,
                    source='native editor config endpoint; GET only',stage='main_config' if scene_id is None else 'scene_config',
                    native_runtime_verified=False,published=False)

    @bounded_errors
    def apply_scene(self, game_id, scene_id, script_path, expected_sha256, backup_directory, allow_empty=False):
        game_id, scene_id = positive_id(game_id), positive_id(scene_id)
        path = Path(script_path).expanduser().resolve()
        if path.suffix.lower() != '.lua' or path.stat().st_size > 1024 * 1024 or (not allow_empty and not path.stat().st_size):
            raise BridgeError('剧情必须为非空、1MB 以内的 UTF-8 Lua 文件')
        code = path.read_text(encoding='utf-8-sig')
        if (not allow_empty and not code.strip()) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
            raise BridgeError('必须提供非空剧情及读取时的 UTF-8 SHA-256')
        if code.strip():
            from .validation import validate_source
            report = validate_source(code)
            if not report['valid']:
                raise BridgeError('保存前 Lua 校验失败：' + json.dumps(report['errors'], ensure_ascii=False))
        url = f'{BASE}/command-line/game/{game_id}/scene/{scene_id}/command'
        with self.client() as api:
            def read():
                return self.data(api.get(url))['command'] or ''
            before = read()
            if source_hash(before) != expected_sha256:
                raise BridgeError('剧情已变化，拒绝覆盖；请重新读取并合并')
            if before == code:
                return dict(game_id=game_id, scene_id=scene_id, changed=False, verified=True, sha256=source_hash(code))
            session = self.session_resolver(game_id)
            folder = Path(backup_directory).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            backup = folder / f'{game_id}-{scene_id}-{stamp}-{uuid.uuid4().hex[:8]}.json'
            with backup.open('x', encoding='utf-8') as stream:
                json.dump(dict(game_id=game_id, scene_id=scene_id, before=before, requested=code,
                               expected_sha256=expected_sha256), stream, ensure_ascii=False, indent=2)
            token = self.data(api.get(url + '/token'))['token']
            if source_hash(read()) != expected_sha256:
                raise BridgeError('备份后检测到并发剧情编辑，未写入')
            commands=[]
            if before:
                commands.append({'index': 0, 'length': len(before.encode('utf-16-le')) // 2, 'type': 'DELETE', 'content': None})
            if code:
                commands.append({'index': 0, 'length': 0, 'type': 'INSERT', 'content': code})
            self.data(api.put(url, json={
                'sId': session, 'token': token, 'serialNum': 1, 'errorStatus': 1,
                'stats': {'source': 3, 'codeNumDelta': 0, 'contentNumDelta': 0},
                'commandList': commands}))
            after = read()
            report = dict(game_id=game_id, scene_id=scene_id, changed=True, verified=after == code,
                          sha256=source_hash(after), backup=str(backup))
            backup.with_suffix('.result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            if not report['verified']:
                raise BridgeError(f'剧情保存后读回不一致；请检查，勿盲目重试。备份：{backup}')
            return report
