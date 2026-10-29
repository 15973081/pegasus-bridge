"""Create a new orientation-specific project without modifying its source."""
from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata

import httpx

from .core import BridgeError, positive_id
from .orientation import OrientationService, DIRECTIONS, PROFILES
from .ui import BASE, bounded_errors, digest
from .workfiles import atomic_json, file_lock, read_json


class OrientationVariantService(OrientationService):
    def _names(self, api, name):
        matches = []
        for offset in range(0, 10000, 100):
            data = self.data(api.get(f'{BASE}/game', params={
                'sourceType': 5, 'limit': 100, 'offset': offset}))
            rows = data if isinstance(data, list) else data['list']
            if not isinstance(rows, list) or len(rows) > 100:
                raise BridgeError('作品列表格式无效')
            matches.extend(positive_id(g['id']) for g in rows if g.get('gameName') == name)
            if len(rows) < 100:
                return matches
        raise BridgeError('作品列表超过核对上限，未创建')

    @staticmethod
    def _result(record, cp, reused):
        verified = record['status'] == 'verified'
        return dict(source_game_id=record['source_game_id'], game_id=record.get('target_game_id'),
                    game_name=record['name'], status=record['status'], verified=verified,
                    project_created=bool(record.get('target_game_id')), reused=reused,
                    checkpoint=str(cp), profile=dict(PROFILES[DIRECTIONS[record['orientation']]]),
                    after=record.get('after'), candidate_game_ids=record.get('candidate_game_ids', []),
                    source_modified=False, content_copied=False, layout_migrated=False,
                    native_ui_observed=False, publish_requested=False,
                    migration=dict(status='not_started', pending=['materials', 'variables', 'chapters',
                                   'scene_scripts', 'story_trees', 'custom_ui', 'layout_validation']),
                    message=('新方向工程已创建并核对，仍为空白模板；内容迁移及原生运行验证尚未完成。' if verified else
                             '创建或初始化结果未确认。复用此检查点只核对已发送的请求，不重复创建或重新初始化；候选同名作品不会自动接管。'))

    def _verify(self, api, record, cp):
        try:
            after = self._snapshot(api, record['target_game_id'])
            source = record['before']['game']
            g, m = after['game'], after['editor']
            target = DIRECTIONS[record['orientation']]
            ok = (g['id'] != record['source_game_id'] and g.get('gameName') == record['name']
                  and g.get('sourceType') == 5 and g.get('status') == 0
                  and g.get('studioId') == source['studioId']
                  and g.get('authorId') == source.get('authorId')
                  and g['orientationMode'] == m['orientationMode'] == target
                  and m.get('mode') == 8 and m.get('adaptMode') == 1)
            record.update(status='verified' if ok else 'unconfirmed', after=self._view(after))
        except (BridgeError, httpx.HTTPError, ValueError, KeyError, TypeError):
            record.update(status='uncertain')
            record.pop('after', None)
        atomic_json(cp, record)

    @bounded_errors
    def create_variant(self, source_game_id, name, orientation, expected_sha256, checkpoint):
        source_game_id = positive_id(source_game_id)
        if not isinstance(name, str) or not 1 <= len(name) <= 30 or name != name.strip() or any(
                unicodedata.category(c).startswith('C') for c in name):
            raise BridgeError('新作品名称须为无首尾空白和控制字符的 1–30 个字符')
        if orientation not in DIRECTIONS:
            raise BridgeError('orientation 须为 horizontal 或 vertical')
        if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
            raise BridgeError('expected_sha256 须来自 get_orientation')
        cp = Path(checkpoint).expanduser().resolve()
        if cp.suffix.lower() != '.json':
            raise BridgeError('检查点须为 .json 文件')
        binding = dict(source_game_id=source_game_id, name=name, orientation=orientation,
                       expected_sha256=expected_sha256)
        with file_lock(cp.with_name(cp.name + '.lock')):
            reused = cp.exists()
            record = read_json(cp) if reused else None
            if reused:
                if (not isinstance(record, dict) or record.get('kind') != 'orientation_variant'
                        or type(record.get('version')) is not int or record['version'] != 1
                        or any(record.get(k) != v for k, v in binding.items())
                        or not isinstance(record.get('before'), dict)
                        or digest(record['before']) != expected_sha256
                        or record.get('status') not in ('create_pending', 'create_uncertain', 'created',
                                                      'init_pending', 'uncertain', 'unconfirmed', 'verified')):
                    raise BridgeError('检查点与请求不匹配或格式无效；未写入')
                if record.get('target_game_id') is not None:
                    target_id = positive_id(record['target_game_id'])
                    if target_id == source_game_id:
                        raise BridgeError('目标不能为原作品')
            with self.client() as api:
                if reused and record['status'] != 'created':
                    if record.get('target_game_id'):
                        self._verify(api, record, cp)
                    else:
                        # Name matching is evidence, not proof of ownership of this attempt.
                        record['candidate_game_ids'] = self._names(api, name)
                        atomic_json(cp, record)
                    return self._result(record, cp, reused=True)
                if not reused:
                    before = self._snapshot(api, source_game_id)
                    if digest(before) != expected_sha256:
                        raise BridgeError('源作品配置已变化，请重新读取；未创建')
                    if not self._view(before)['create_variant_supported']:
                        raise BridgeError('仅支持未发布/被退回的天马 mode=8 作品作为来源')
                    source = before['game']
                    if name == source['gameName'] or self._names(api, name):
                        raise BridgeError('新作品名称已存在或与原作相同；未创建，也不会接管同名作品')
                    fields = ('subtitle', 'langType', 'creativeType', 'sourceType', 'areaType', 'studioId')
                    if not all(k in source for k in fields):
                        raise BridgeError('源作品基本信息不完整；未创建')
                    payload = {k: source[k] for k in fields}
                    payload.update(gameName=name, orientationMode=DIRECTIONS[orientation], matchId=-1)
                    if self._snapshot(api, source_game_id) != before:
                        raise BridgeError('源作品发生并发修改；未创建')
                    record = dict(version=1, kind='orientation_variant', **binding, before=before,
                                  status='create_pending', request=payload,
                                  created_at=datetime.now(timezone.utc).isoformat())
                    atomic_json(cp, record)
                    try:
                        result = self.data(api.post(f'{BASE}/game', json=payload))
                        target_id = positive_id(result['id'])
                        if target_id == source_game_id:
                            raise BridgeError('创建响应指向原作，停止初始化')
                    except (BridgeError, httpx.HTTPError, ValueError, KeyError, TypeError):
                        record['status'] = 'create_uncertain'
                        atomic_json(cp, record)
                        return self._result(record, cp, reused=False)
                    record.update(target_game_id=target_id, status='created')
                    atomic_json(cp, record)
                # Only a confirmed new ID with no previous initialization attempt reaches here.
                gid = record['target_game_id']
                game = self.data(api.get(f'{BASE}/game/{gid}'))
                source = record['before']['game']
                if (game.get('id') != gid or game.get('gameName') != name or game.get('status') != 0
                        or game.get('sourceType') != 5 or game.get('studioId') != source['studioId']
                        or game.get('authorId') != source.get('authorId')):
                    raise BridgeError('新作品归属或基本信息发生变化，停止初始化')
                record['status'] = 'init_pending'
                atomic_json(cp, record)
                try:
                    self.data(api.post(f'{BASE}/game/{gid}/config', json={
                        'templateId': PROFILES[DIRECTIONS[orientation]]['template_id'], 'mode': 8,
                        'orientationMode': DIRECTIONS[orientation], 'matchId': -1, 'adaptMode': 1}))
                except (BridgeError, httpx.HTTPError, ValueError, KeyError, TypeError):
                    pass  # Read-back resolves response loss; never replay initialization.
                self._verify(api, record, cp)
                return self._result(record, cp, reused)
