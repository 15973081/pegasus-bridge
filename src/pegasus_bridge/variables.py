"""Register a normal string checkpoint in the editor's native save schema."""
import re
from .core import BridgeError, positive_id
from .ui import UIService, BASE, bounded_errors, digest
from .mall import backup_file, valid_hash


class VariableService(UIService):
    @staticmethod
    def members(rows):
        if not isinstance(rows, list):
            raise BridgeError('变量目录不是列表')
        result = {}
        for row in rows:
            values = row.get('values', row.get('Values', [])) or []
            if not isinstance(values, list):
                raise BridgeError('变量目录结构异常')
            for item in values:
                ident = item.get('id')
                if ident in result and result[ident] != item:
                    raise BridgeError('重复变量 ID 的定义不一致')
                result[ident] = item
        return list(result.values())

    @bounded_errors
    def ensure_checkpoint(self, game_id, name, expected_sha256, backup_directory):
        positive_id(game_id)
        valid_hash(expected_sha256)
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', name) or name.startswith('system_'):
            raise BridgeError('进度变量名称须为 1–64 位字母数字下划线，以字母开头，不能使用 system_')
        url = f'{BASE}/game/{game_id}/value'
        with self.client() as api:
            before = self.data(api.get(url))
            if digest(before) != expected_sha256:
                raise BridgeError('变量目录已变化，请重新读取')
            members = self.members(before)
            found = [v for v in members if v.get('valueName') == name]
            if found:
                if len(found) != 1 or found[0].get('category') != 2 or found[0].get('valueType') != 2 or found[0].get('isRandom', 0) != 0:
                    raise BridgeError('同名变量已有不兼容定义，不覆盖')
                return dict(game_id=game_id, name=name, variable_id=found[0]['id'], changed=False, verified=True)
            payload = dict(id=0, numValue=0, catalogId=None, category=2, isRandom=0,
                           remark='调查进度快照；由游戏脚本维护，随原生存档保存', valueName=name,
                           valueType=2, randomMin=0, randomMax=0, stringValue='',
                           enableLimit=0, limitMinValue=0, limitMaxValue=0)
            backup = backup_file(backup_directory, f'checkpoint-{game_id}',
                                 dict(game_id=game_id, before=before, requested=payload))
            if digest(self.data(api.get(url))) != expected_sha256:
                raise BridgeError('备份后检测到变量目录变化，未写入')
            self.data(api.post(url, json=payload))
            after = self.members(self.data(api.get(url)))
            created = [v for v in after if v.get('valueName') == name]
            unchanged = all(any(v == old for v in after) for old in members)
            verified = (len(created) == 1 and created[0].get('category') == 2 and
                        created[0].get('valueType') == 2 and created[0].get('stringValue') == '' and unchanged)
            if not verified:
                raise BridgeError(f'创建结果未通过核验，请先读回，不要重复创建；备份：{backup}')
            return dict(game_id=game_id, name=name, variable_id=created[0]['id'],
                        changed=True, verified=True, backup=str(backup), source='native_variable_definition',
                        note='注册存档字段，不写玩家进度；原生存读档需运行验证')
