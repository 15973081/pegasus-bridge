"""Scene edits using the editor's command protocol, with revision guards."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from .core import BridgeError, positive_id
from .ui import UIService, BASE, bounded_errors


def source_hash(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def editor_session(game_id):
    import winreg
    fallback = None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\com.NetEase\Pegasus') as key:
        for i in range(winreg.QueryInfoKey(key)[1]):
            name, value, _ = winreg.EnumValue(key, i)
            if name.startswith((f'CACHE_GAME_SID_{game_id}_', 'CACHE_GAME_SID_0_')):
                value = value.decode('utf-8').rstrip('\0') if isinstance(value, bytes) else value
                if name.startswith(f'CACHE_GAME_SID_{game_id}_') and value:
                    return value
                fallback = value
    if not fallback:
        raise BridgeError('请先在天马编辑器中打开目标作品，以建立编辑会话')
    return fallback


class SceneService(UIService):
    def __init__(self, bridge, transport=None, session_resolver=editor_session):
        super().__init__(bridge, transport)
        self.session_resolver = session_resolver

    @bounded_errors
    def apply_scene(self, game_id, scene_id, script_path, expected_sha256, backup_directory):
        game_id, scene_id = positive_id(game_id), positive_id(scene_id)
        path = Path(script_path).expanduser().resolve()
        if path.suffix.lower() != '.lua' or not 0 < path.stat().st_size <= 1024 * 1024:
            raise BridgeError('剧情必须为非空、1MB 以内的 UTF-8 Lua 文件')
        code = path.read_text(encoding='utf-8-sig')
        if not code.strip() or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
            raise BridgeError('必须提供非空剧情及读取时的 UTF-8 SHA-256')
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
