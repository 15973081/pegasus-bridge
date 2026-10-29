"""Persisted game orientation; never reinitialize a game's template or layout."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import httpx

from .core import BridgeError, positive_id, redact
from .ui import BASE, UIService, bounded_errors, digest
from .workfiles import atomic_json, file_lock, new_text, read_json


DIRECTIONS = {'horizontal': 1, 'vertical': 2}
PROFILES = {
    1: dict(orientation='horizontal', orientationMode=1, mode=8, template_id=28,
            canvas_width=1920, canvas_height=1080),
    2: dict(orientation='vertical', orientationMode=2, mode=8, template_id=32,
            canvas_width=1080, canvas_height=1920),
}
# Fields submitted by Tianma's game-detail form. Do not send arbitrary API fields.
FORM_FIELDS = ('gameName', 'subtitle', 'langType', 'creativeType', 'sourceType',
               'areaType', 'matchId', 'studioId')


class OrientationService(UIService):
    def _snapshot(self, api, game_id):
        game = self.data(api.get(f'{BASE}/game/{game_id}'))
        mode = self.data(api.get(f'{BASE}/game/{game_id}/edit/mode'))
        if (not isinstance(game, dict) or type(game.get('id')) is not int
                or game['id'] != game_id or not isinstance(mode, dict)):
            raise BridgeError('作品或编辑器配置格式无效 / 归属不符')
        for row in (game, mode):
            if type(row.get('orientationMode')) is not int or row['orientationMode'] not in (1, 2):
                raise BridgeError('后台未返回已知的横竖版方向，未写入')
        return redact({'game': game, 'editor': mode})

    @staticmethod
    def _view(snapshot):
        game, mode = snapshot['game'], snapshot['editor']
        supported = (type(game.get('status')) is int and game['status'] in (-1, 0)
                     and game.get('sourceType') == 5 and mode.get('mode') == 8
                     and game['orientationMode'] == mode['orientationMode'])
        return dict(game_id=game['id'], game_name=game.get('gameName'),
                    game_orientation=game['orientationMode'],
                    editor_orientation=mode['orientationMode'],
                    consistent=game['orientationMode'] == mode['orientationMode'],
                    sha256=digest(snapshot), supported=False,
                    in_place_update_supported=False, create_variant_supported=supported,
                    profile=dict(PROFILES[game['orientationMode']]) if mode.get('mode') == 8
                    and game['orientationMode'] == mode['orientationMode'] else None,
                    source='live_api', native_ui_observed=False,
                    note='1=横版，2=竖版；mode=8 是编辑器类型。已有作品原地切换未获后台支持；可创建独立方向版本，再迁移内容。画布尺寸来自客户端逻辑，不是素材分辨率。')

    @bounded_errors
    def get_orientation(self, game_id):
        game_id = positive_id(game_id)
        with self.client() as api:
            return self._view(self._snapshot(api, game_id))

    def _reconcile(self, api, record, cp, reused):
        try:
            after = self._snapshot(api, record['game_id'])
            target = DIRECTIONS[record['orientation']]
            both = all(after[key]['orientationMode'] == target for key in ('game', 'editor'))
            before = record['before']
            unchanged = (all(after['game'].get(k) == before['game'].get(k) for k in FORM_FIELDS)
                         and {k: v for k, v in after['editor'].items() if k != 'orientationMode'}
                         == {k: v for k, v in before['editor'].items() if k != 'orientationMode'})
            status = 'verified' if both and unchanged else (
                'unsupported' if record['status'] == 'unsupported' else 'unconfirmed')
            record.update(after=after, status=status)
        except (BridgeError, httpx.HTTPError, ValueError, KeyError, TypeError):
            record.pop('after', None)
            record['status'] = 'uncertain'
        atomic_json(cp, record)
        verified = record['status'] == 'verified'
        return dict(game_id=record['game_id'], orientation=record['orientation'],
                    status=record['status'], verified=verified, reused=reused,
                    checkpoint=str(cp), after=self._view(record['after']) if 'after' in record else None,
                    native_ui_observed=False, layout_migrated=False, publish_requested=False, remote_write=False,
                    message=('后台未支持原地切换，未发送修改；请创建独立方向版本。' if record['status'] == 'unsupported' else
                             '两处后台方向均已核对。重新打开作品查看效果；现有 UI 需另行适配。' if verified else
                             '切换尚未确认。保留此检查点再次调用只读核对，不会重复写入或重置模板。'))

    @bounded_errors
    def set_orientation(self, game_id, orientation, expected_sha256, checkpoint):
        game_id = positive_id(game_id)
        if orientation not in DIRECTIONS:
            raise BridgeError('orientation 须为 horizontal 或 vertical')
        if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
            raise BridgeError('expected_sha256 须来自 get_orientation')
        cp = Path(checkpoint).expanduser().resolve()
        if cp.suffix.lower() != '.json':
            raise BridgeError('检查点须为 .json 文件')
        with file_lock(cp.with_name(cp.name + '.lock')):
            record = read_json(cp) if cp.exists() else None
            if record is not None:
                if (not isinstance(record, dict) or record.get('kind') != 'orientation'
                        or type(record.get('version')) is not int or record['version'] != 1
                        or type(record.get('game_id')) is not int or record['game_id'] != game_id
                        or record.get('orientation') != orientation
                        or record.get('expected_sha256') != expected_sha256
                        or record.get('status') not in ('requesting', 'uncertain', 'unconfirmed', 'verified', 'conflict', 'unsupported')
                        or not isinstance(record.get('before'), dict)
                        or digest(record['before']) != expected_sha256
                        or not all(isinstance(record['before'].get(k), dict) for k in ('game', 'editor'))):
                    raise BridgeError('检查点无效或与本次请求不符；未写入')
            with self.client() as api:
                if record is not None:
                    return self._reconcile(api, record, cp, reused=True)
                before = self._snapshot(api, game_id)
                if digest(before) != expected_sha256:
                    raise BridgeError('配置已变化，请重新读取方向；未写入')
                if not self._view(before)['create_variant_supported']:
                    raise BridgeError('仅支持天马 mode=8 的未发布/被退回作品；不会提交审核或重初始化')
                game = before['game']
                if not all(k in game for k in ('gameName', 'subtitle', 'langType', 'creativeType',
                                              'sourceType', 'areaType', 'studioId')):
                    raise BridgeError('作品基本信息不完整，无法保留表单字段；未写入')
                record = dict(version=1, kind='orientation', game_id=game_id,
                              orientation=orientation, expected_sha256=expected_sha256,
                              status='requesting', before=before,
                              requested_at=datetime.now(timezone.utc).isoformat())
                new_text(cp, json.dumps(record, ensure_ascii=False, indent=2))
                if self._snapshot(api, game_id) != before:
                    record['status'] = 'conflict'
                    atomic_json(cp, record)
                    raise BridgeError('配置发生并发变化，未写入；需重新读取并使用新检查点')
                target = DIRECTIONS[orientation]
                if not all(before[k]['orientationMode'] == target for k in ('game', 'editor')):
                    record['status'] = 'unsupported'
                    atomic_json(cp, record)
                    return dict(game_id=game_id, orientation=orientation, status='unsupported',
                                verified=False, checkpoint=str(cp), reused=False,
                                remote_write=False, native_ui_observed=False, layout_migrated=False,
                                publish_requested=False, alternative='pegasus_create_orientation_variant',
                                message='后台未支持已有作品原地切换，未发送修改。请创建独立方向版本；素材、剧情及 UI 需另行迁移。')
                return self._reconcile(api, record, cp, reused=False)
