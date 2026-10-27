"""Mall UI scripts and existing goods edits using observed editor protocols."""
import json
import re
import uuid
from pathlib import Path

from .core import BridgeError, positive_id, redact
from .scene import SceneService, source_hash
from .ui import UIService, BASE, bounded_errors, digest


def valid_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise BridgeError('必须提供读取时的 SHA-256')


def backup_file(directory, prefix, data):
    folder = Path(directory).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{prefix}-{uuid.uuid4().hex}.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    return path


class MallUIService(SceneService):
    @staticmethod
    def target(game_id, catalog_id, ui_id):
        positive_id(game_id); positive_id(catalog_id)
        if not isinstance(ui_id, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', ui_id):
            raise BridgeError('ui_id 必须为目录中实际存在的 UUID')
        return f'{BASE}/command-line/game/{game_id}/custom-ui-catalog/{catalog_id}/ui/{ui_id}/command'

    def member(self, api, game_id, catalog_id, ui_id):
        rows = self.data(api.get(f'{BASE}/command-line/game/{game_id}/custom-ui-catalog'))
        catalogs = [row for row in rows if row['id'] == catalog_id and row.get('type') == 12 and row.get('mode') == 8]
        if len(catalogs) != 1:
            raise BridgeError('目标不是本作品唯一匹配的商城 UI 分类')
        items = catalogs[0]['command']
        if isinstance(items, str): items = json.loads(items)
        matches = [item for item in items if item['id'] == ui_id]
        if len(matches) != 1: raise BridgeError('UI 不属于指定商城分类或无法唯一匹配')
        return matches[0]

    @bounded_errors
    def read_ui(self, game_id, catalog_id, ui_id):
        url = self.target(game_id, catalog_id, ui_id)
        with self.client() as api:
            member = self.member(api, game_id, catalog_id, ui_id)
            data = self.data(api.get(url))
            code = data.get('command') or ''
            return {'source': 'live_api', 'resource': 'mall_ui', 'game_id': game_id,
                    'catalog_id': catalog_id, 'ui_id': ui_id, 'name': member['name'],
                    'sha256': source_hash(code), 'response': {'data': redact(data)}}

    @bounded_errors
    def apply_ui(self, game_id, catalog_id, ui_id, script_path, expected_sha256, backup_directory):
        url = self.target(game_id, catalog_id, ui_id)
        valid_hash(expected_sha256)
        path = Path(script_path).expanduser().resolve()
        if path.suffix.lower() != '.lua' or not 0 < path.stat().st_size <= 1024 * 1024:
            raise BridgeError('商城 UI 必须为非空、1MB 以内的 UTF-8 Lua 文件')
        code = path.read_text(encoding='utf-8-sig')
        if not code.strip(): raise BridgeError('商城 UI 源码不能为空')
        with self.client() as api:
            member = self.member(api, game_id, catalog_id, ui_id)
            def read(): return self.data(api.get(url))['command'] or ''
            before = read()
            if source_hash(before) != expected_sha256: raise BridgeError('商城 UI 已变化，拒绝覆盖')
            report = dict(game_id=game_id, catalog_id=catalog_id, ui_id=ui_id)
            if before == code:
                return dict(report, changed=False, verified=True, sha256=source_hash(code))
            session = self.session_resolver(game_id)
            backup = backup_file(backup_directory, f'mall-ui-{game_id}-{ui_id}',
                                 dict(report, before=before, requested=code, expected_sha256=expected_sha256))
            token = self.data(api.get(url + '/token'))['token']
            if self.member(api, game_id, catalog_id, ui_id) != member or source_hash(read()) != expected_sha256:
                raise BridgeError('备份后检测到商城 UI 并发编辑，未写入')
            commands = []
            if before:
                commands.append({'index': 0, 'length': len(before.encode('utf-16-le')) // 2, 'type': 'DELETE', 'content': None})
            commands.append({'index': 0, 'length': 0, 'type': 'INSERT', 'content': code})
            self.data(api.put(url, json={'sId': session, 'token': token, 'serialNum': 1, 'errorStatus': 1,
                                        'stats': {'source': 3, 'codeNumDelta': 0, 'contentNumDelta': 0},
                                        'commandList': commands}))
            after = read()
            report.update(changed=True, verified=after == code, sha256=source_hash(after), backup=str(backup))
            backup.with_suffix('.result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            if not report['verified']: raise BridgeError(f'商城 UI 读回不一致；请检查备份，勿盲目重试：{backup}')
            return report


class GoodsService(UIService):
    def goods(self, api, game_id, goods_id):
        rows = self.data(api.get(f'{BASE}/game/{game_id}/goods'))
        found = [item for item in rows if item['id'] == goods_id]
        if len(found) != 1: raise BridgeError('商品不属于指定作品或无法唯一匹配')
        return found[0]

    @bounded_errors
    def apply(self, game_id, goods_id, changes, expected_sha256, backup_directory):
        positive_id(game_id); positive_id(goods_id); valid_hash(expected_sha256)
        if not isinstance(changes, dict) or not changes or set(changes) - {'name', 'price', 'payType'}:
            raise BridgeError('商品修改仅支持 name、price、payType；说明在商品详情 UI 中编辑')
        if 'name' in changes and (not isinstance(changes['name'], str) or not changes['name'].strip() or len(changes['name']) > 100):
            raise BridgeError('商品名称须为 1–100 字符')
        if 'price' in changes and (type(changes['price']) is not int or not 1 <= changes['price'] <= 2147483647):
            raise BridgeError('商品价格须为正整数，单位沿用后台原值，不换算人民币')
        if 'payType' in changes and (type(changes['payType']) is not int or changes['payType'] not in (1, 2)):
            raise BridgeError('payType 仅支持客户端的 1、2 类型')
        with self.client() as api:
            before = self.goods(api, game_id, goods_id)
            if digest(before) != expected_sha256: raise BridgeError('商品已变化，拒绝覆盖')
            target = {**before, **changes}
            report = dict(game_id=game_id, goods_id=goods_id)
            if target == before: return dict(report, changed=False, verified=True, sha256=digest(before))
            # Keep product binding, type, entitlements and child goods intact.
            fields = ('id', 'name', 'payType', 'price', 'productId', 'type')
            if any(k not in target for k in fields) or target['type'] is None:
                raise BridgeError('商品修改协议字段不完整；不猜测商品类型')
            backup = backup_file(backup_directory, f'goods-{game_id}-{goods_id}', dict(report, before=before, requested=target))
            if self.goods(api, game_id, goods_id) != before: raise BridgeError('备份后检测到商品并发编辑，未写入')
            self.data(api.put(f'{BASE}/game/{game_id}/goods/{goods_id}', json={k: target[k] for k in fields}))
            after = self.goods(api, game_id, goods_id)
            # Ignore server-managed timestamps while checking every previous business field.
            business = set(before) - {'modifyTime', 'updateTime'}
            verified = all(after.get(k) == target[k] for k in business)
            report.update(changed=True, verified=verified, sha256=digest(after), backup=str(backup))
            backup.with_suffix('.result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            if not verified: raise BridgeError(f'商品读回不一致；请检查备份，勿盲目重试：{backup}')
            return report
