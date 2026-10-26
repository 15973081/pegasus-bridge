"""Bounded UI writes: fixed origins, material upload, backup and optimistic checks."""
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
from pathlib import Path
import re
import struct
import uuid

import httpx
from .core import BridgeError, Pegasus, positive_id

BASE = 'https://avg.163.com/avg-editor-api'
SECTIONS = {'cover', 'configuration', 'record', 'playback', 'menu'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def bounded_errors(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BridgeError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError, struct.error):
            # HTTP exceptions may embed credential-bearing query strings.
            raise BridgeError('UI 操作失败；请检查网络、输入和本地备份。写入结果不确定时先读回，勿盲目重试。') from None
    return wrapped


class UIService:
    def __init__(self, bridge: Pegasus, transport=None):
        self.bridge, self.transport = bridge, transport

    def client(self, authenticated=True):
        h, p = self.bridge._auth() if authenticated else ({}, {})
        return httpx.Client(headers=h, params=p, timeout=90, follow_redirects=False,
                            transport=self.transport)

    @staticmethod
    def data(response):
        if response.status_code != 200:
            raise BridgeError(f'UI API HTTP {response.status_code}；未确认成功')
        obj = response.json()
        if obj.get('state', {}).get('code') != 200000:
            raise BridgeError(f"UI API 拒绝操作（code={obj.get('state', {}).get('code')}）")
        return obj.get('data')

    def template(self, client, game_id):
        data = self.data(client.get(f'{BASE}/game/{game_id}/config/template'))
        value = data['template']
        value = json.loads(value) if isinstance(value, str) else value
        if not isinstance(value, dict):
            raise BridgeError('UI template 必须为对象')
        return value

    @bounded_errors
    def upload_image(self, game_id, image_path, alias, category_id, checkpoint):
        game_id = positive_id(game_id)
        if category_id not in (1, 2):
            raise BridgeError('图片 category_id 仅支持 1（背景）和 2（组件）')
        if not alias or len(alias) > 100:
            raise BridgeError('素材名称长度必须为 1–100')
        path = Path(image_path).expanduser().resolve()
        if path.suffix.lower() != '.png' or not 24 <= path.stat().st_size <= 20 * 1024 * 1024:
            raise BridgeError('仅支持 20MB 以内 PNG')
        raw = path.read_bytes()
        if raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
            raise BridgeError('PNG 头无效')
        width, height = struct.unpack('>II', raw[16:24])
        if not 0 < width <= 8192 or not 0 < height <= 8192:
            raise BridgeError('PNG 尺寸超出 1–8192')
        identity = {'game_id': game_id, 'sha256': hashlib.sha256(raw).hexdigest(),
                    'alias': alias, 'category_id': category_id}
        cp = Path(checkpoint).expanduser().resolve()
        record = json.loads(cp.read_text(encoding='utf-8')) if cp.exists() else identity.copy()
        if any(record.get(k) != v for k, v in identity.items()):
            raise BridgeError('上传检查点与作品 / 文件 / 分类不符，请使用新的检查点')
        if record.get('id'):
            return {**record, 'reused': True}
        cp.parent.mkdir(parents=True, exist_ok=True)

        def save():
            temp = cp.with_name(cp.name + '.tmp-' + uuid.uuid4().hex)
            temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(cp)

        with self.client() as api:
            catalogs = self.data(api.get(f'{BASE}/game/{game_id}/material/catalog/3d/v2',
                                        params={'categoryId': category_id, 'materialType': 'imageV2'}))
            defaults = [x['id'] for x in catalogs['ownerList'] if x.get('isDefault') == 1]
            if len(defaults) != 1:
                raise BridgeError('无法唯一确定本作品默认素材分类')
            if not record.get('url'):
                token = self.data(api.get('https://avg.163.com/avg-portal-api/filepicker/token',
                                         params={'type': 'MATERIAL_IMAGE'}))['token']
                # Only filepicker's scoped token goes to the file server. Never portal cookies.
                with self.client(authenticated=False) as uploader:
                    result = uploader.post('https://fp.ps.netease.com/a13/file/new/',
                                           data={'Authorization': token},
                                           files={'fpfile': (path.name, raw, 'image/png')})
                if result.status_code not in (200, 201):
                    raise BridgeError(f'素材上传 HTTP {result.status_code}')
                fid = result.headers.get('response-fid', '')
                if not re.fullmatch(r'[A-Za-z0-9_-]+', fid):
                    raise BridgeError('素材上传响应缺少有效文件 ID')
                record.update(url='https://a13.fp.ps.netease.com/file/' + fid,
                              width=width, height=height, file=str(path))
                save()
            url = record['url']
            if not re.fullmatch(r'https://a13\.fp\.ps\.netease\.com/file/[A-Za-z0-9_-]+', url):
                raise BridgeError('检查点素材 URL 不符合固定文件服务器范围')
            info = {'materialAlias': alias, 'url': url, 'catalogId': defaults[0],
                    'categoryId': category_id, 'fileType': 'png', 'size': len(raw),
                    'width': width, 'height': height}
            material = self.data(api.post(f'{BASE}/game/{game_id}/material', json=info))
            record.update(id=positive_id(material['id']), gameMaterialId=material.get('gameMaterialId'))
            save()
        return {**record, 'reused': False}

    @bounded_errors
    def apply_template(self, game_id, section, template, expected_sha256, backup_directory):
        game_id = positive_id(game_id)
        if section not in SECTIONS or not isinstance(template, dict):
            raise BridgeError('仅支持五个基本 UI 分类，template 必须为对象')
        if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
            raise BridgeError('必须提供读取时的分类配置 SHA-256')
        with self.client() as api:
            before = self.template(api, game_id)
            old = before[section]
            if digest(old) != expected_sha256:
                raise BridgeError('检测到配置已变化，拒绝覆盖；请重新读取并合并')
            if set(old) != set(template):
                raise BridgeError('分类顶层字段必须保留；不允许意外删除或新增配置键')
            if old == template:
                return {'game_id': game_id, 'section': section, 'changed': False, 'verified': True,
                        'sha256': digest(old)}
            folder = Path(backup_directory).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            backup = folder / f'{game_id}-{section}-{stamp}-{uuid.uuid4().hex[:8]}.json'
            with backup.open('x', encoding='utf-8') as stream:
                json.dump({'game_id': game_id, 'section': section, 'before': before,
                           'requested': template, 'expected_sha256': expected_sha256},
                          stream, ensure_ascii=False, indent=2)
            # Backend exposes no CAS token: reduce, but cannot eliminate, the race window.
            latest = self.template(api, game_id)
            if digest(latest[section]) != expected_sha256:
                raise BridgeError('备份后检测到并发编辑，未写入')
            self.data(api.put(f'{BASE}/game/{game_id}/config/template/{section}',
                              json={'template': json.dumps(template, ensure_ascii=False)}))
            after = self.template(api, game_id)
            report = {'game_id': game_id, 'section': section, 'changed': True,
                      'verified': after[section] == template, 'sha256': digest(after[section]),
                      'backup': str(backup),
                      'other_sections_unchanged': all(after.get(k) == v for k, v in latest.items() if k != section)}
            backup.with_suffix('.result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            if not report['verified']:
                raise BridgeError(f'保存后读回不一致；未自动回滚以免覆盖并发修改。备份：{backup}')
            return report
