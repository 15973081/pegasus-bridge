"""Named editor backups, matching ACBackUpView's Save dialog protocol."""
from datetime import datetime, timezone
import json
from pathlib import Path
import unicodedata

from .core import BridgeError, positive_id
from .ui import UIService, BASE, bounded_errors, digest
from .workfiles import atomic_json, file_lock, new_text, read_json


def draft_name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 100
            or value != value.strip()
            or any(unicodedata.category(c).startswith('C') for c in value)):
        raise BridgeError('草稿名称须为 1–100 个字符，不能有首尾空白或控制字符')
    return value


class DraftService(UIService):
    """Create-only backup operations; never restore, rename, delete or publish."""

    def _list(self, api, game_id):
        rows = self.data(api.get(f'{BASE}/game/{game_id}/backup'))
        if not isinstance(rows, list) or len(rows) > 10000:
            raise BridgeError('原生草稿列表格式无效或超过读取上限')
        result, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise BridgeError('原生草稿条目格式无效')
            ident = positive_id(row.get('id'))
            if ident in seen or row.get('gameId') != game_id or isinstance(row.get('gameId'), bool):
                raise BridgeError('原生草稿 ID 重复或作品归属不一致')
            seen.add(ident)
            if not isinstance(row.get('backupName'), str):
                raise BridgeError('原生草稿名称格式无效')
            for key in ('mode', 'isAuto', 'createTime'):
                if type(row.get(key)) is not int or row[key] < 0:
                    raise BridgeError('原生草稿元数据格式无效')
            # Return only fields used by the native list, never unknown backend fields.
            result.append({key: row[key] for key in
                           ('id', 'gameId', 'backupName', 'mode', 'isAuto', 'createTime')})
        return sorted(result, key=lambda row: row['id'])

    @bounded_errors
    def list_drafts(self, game_id):
        game_id = positive_id(game_id)
        with self.client() as api:
            rows = self._list(api, game_id)
        return dict(game_id=game_id, drafts=rows, count=len(rows), sha256=digest(rows),
                    source='native_editor_backup_list',
                    read_at=datetime.now(timezone.utc).isoformat())

    @staticmethod
    def _result(record, checkpoint, reused):
        verified = record['status'] == 'verified'
        return dict(game_id=record['game_id'], name=record['name'], status=record['status'],
                    complete=verified, verified=verified, reused=reused,
                    draft=record.get('draft'), checkpoint=str(checkpoint),
                    verification='native_backup_list' if verified else 'unconfirmed',
                    source='persisted_backend', editor_buffer_flushed=False,
                    native_ui_observed=False, publish_requested=False,
                    message=('已在编辑器使用的备份列表中核对；重新打开原生“打开”窗口可查看。'
                             if verified else '创建结果未确认；保留此检查点，再次调用只读核对，不会重发创建请求。'))

    def _reconcile(self, api, record, checkpoint, reused):
        try:
            rows = self._list(api, record['game_id'])
        except Exception:
            # A read error may include auth-bearing URLs. Preserve uncertainty without it.
            record['status'] = 'uncertain'
            atomic_json(checkpoint, record)
            return self._result(record, checkpoint, reused)
        ident = record.get('response_id') or (record.get('draft') or {}).get('id')
        candidates = [row for row in rows if row['backupName'] == record['name']
                      and row['id'] not in record['before_ids']
                      and (ident is None or row['id'] == ident)]
        if len(candidates) == 1:
            record.update(status='verified', draft=candidates[0],
                          attribution='response_id' if ident else 'new_unique_name')
        else:
            record['status'] = 'uncertain'
            record.pop('draft', None)
        atomic_json(checkpoint, record)
        return self._result(record, checkpoint, reused)

    @bounded_errors
    def save_draft(self, game_id, name, checkpoint):
        game_id, name = positive_id(game_id), draft_name(name)
        cp = Path(checkpoint).expanduser().resolve()
        if cp.suffix.lower() != '.json':
            raise BridgeError('草稿检查点须为 .json 文件')
        with file_lock(cp.with_name(cp.name + '.lock')):
            record = read_json(cp) if cp.exists() else None
            if record is not None:
                if (not isinstance(record, dict) or type(record.get('version')) is not int or record.get('version') != 1
                        or type(record.get('game_id')) is not int or record.get('game_id') != game_id or record.get('name') != name
                        or record.get('status') not in ('requesting', 'uncertain', 'verified')
                        or not isinstance(record.get('before_ids'), list)
                        or any(type(i) is not int or i <= 0 for i in record['before_ids'])
                        or ('response_id' in record and
                            (type(record['response_id']) is not int or record['response_id'] <= 0))
                        or ('draft' in record and not isinstance(record['draft'], dict))):
                    raise BridgeError('草稿检查点无效或与作品 / 名称不符；不会发送创建请求')
            with self.client() as api:
                if record is not None:
                    # Even a successful record must be verified against the current list.
                    return self._reconcile(api, record, cp, reused=True)
                before = self._list(api, game_id)
                if any(row['backupName'] == name for row in before):
                    raise BridgeError('已存在同名原生草稿；请换名，或使用此前创建的检查点核对')
                record = dict(version=1, game_id=game_id, name=name, status='requesting',
                              before_ids=[row['id'] for row in before],
                              requested_at=datetime.now(timezone.utc).isoformat())
                # Persist BEFORE POST. A process crash never leads to a blind re-POST.
                new_text(cp, json.dumps(record, ensure_ascii=False, indent=2))
                try:
                    # Native ACBackUpView serializes a new HttpBackup, setting only its name.
                    response = self.data(api.post(f'{BASE}/game/{game_id}/backup', json={
                        'id': 0, 'gameId': 0, 'mode': 0, 'backupName': name,
                        'isAuto': 0, 'createTime': 0}))
                    if isinstance(response, dict) and type(response.get('id')) is int and response['id'] > 0:
                        record['response_id'] = response['id']
                except Exception:
                    record['status'] = 'uncertain'
                atomic_json(cp, record)
                return self._reconcile(api, record, cp, reused=False)
