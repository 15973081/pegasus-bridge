"""Discover authenticated account works through the backend, never log history."""
from functools import wraps
from typing import Literal

import httpx

from .core import BridgeError, positive_id
from .ui import BASE, UIService


def discovery_errors(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BridgeError:
            raise
        except httpx.HTTPError:
            raise BridgeError('NETWORK_ERROR：作品查询失败，请重试；不能据此判断作品不存在。') from None
        except (ValueError, TypeError, KeyError, OSError):
            raise BridgeError('INVALID_RESPONSE：作品列表响应异常，未完成核对。') from None
    return wrapped


class GameDiscoveryService(UIService):
    @staticmethod
    def _integer(value, name, low, high):
        if type(value) is not int or not low <= value <= high:
            raise BridgeError(f'{name} 必须为 {low}–{high} 的整数')

    @staticmethod
    def _query(value):
        if value is not None and (not isinstance(value, str) or not value.strip()
                                  or len(value) > 200 or any(ord(c) < 32 for c in value)):
            raise BridgeError('名称/关键词必须为 1–200 字符，不能全为空白或含控制字符')

    @staticmethod
    def _page(response, page_size):
        if response.status_code == 401:
            raise BridgeError('AUTH_REQUIRED：后台会话无效，请重新登录天马编辑器；无需逐个打开作品。')
        if response.status_code == 403:
            raise BridgeError('ACCESS_DENIED：后台拒绝列表访问，请检查当前账号权限及登录状态；不能判定作品不存在。')
        if response.status_code != 200:
            raise BridgeError(f'API_HTTP_ERROR：作品列表 HTTP {response.status_code}，未完成核对。')
        obj = response.json()
        if not isinstance(obj, dict) or not isinstance(obj.get('state'), dict):
            raise BridgeError('INVALID_RESPONSE：作品列表缺少有效状态。')
        code = obj['state'].get('code')
        if type(code) is not int or code != 200000:
            # Do not echo server messages or infer undocumented auth codes.
            safe_code = code if type(code) is int else 'unknown'
            raise BridgeError(f'API_REJECTED：后台拒绝作品查询（code={safe_code}），请检查登录状态和账号权限。')
        data = obj.get('data')
        rows = data.get('list') if isinstance(data, dict) else data
        total = data.get('totalSize') if isinstance(data, dict) else None
        if not isinstance(rows, list) or len(rows) > page_size:
            raise BridgeError('INVALID_RESPONSE：作品列表格式或分页数量异常。')
        if total is not None and (type(total) is not int or total < 0):
            raise BridgeError('INVALID_RESPONSE：作品总数无效。')
        games = []
        for row in rows:
            if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0:
                raise BridgeError('INVALID_RESPONSE：作品 ID 无效。')
            if not isinstance(row.get('gameName'), str) or not row['gameName'].strip():
                raise BridgeError('INVALID_RESPONSE：作品名称无效。')
            game = {'game_id': row['id'], 'name': row['gameName']}
            # Whitelist scalar metadata; no author identifiers or raw API payloads.
            for source, target in [('mode', 'mode'), ('orientationMode', 'orientation_mode'),
                                   ('status', 'status'), ('statusName', 'status_name'),
                                   ('createTime', 'create_time')]:
                if source in row and (row[source] is None or type(row[source]) in (int, str, bool)):
                    game[target] = row[source]
            games.append(game)
        return games, total

    @discovery_errors
    def list_games(self, query: str | None = None, offset: int = 0, page_size: int = 100,
                   max_pages: int = 10, match_mode: Literal['contains', 'exact'] = 'contains') -> dict:
        self._query(query)
        self._integer(offset, 'offset', 0, 1_000_000)
        self._integer(page_size, 'page_size', 1, 100)
        self._integer(max_pages, 'max_pages', 1, 100)
        if match_mode not in ('contains', 'exact'):
            raise BridgeError('match_mode 必须为 contains 或 exact')
        try:
            client = self.client()
        except BridgeError:
            raise BridgeError('AUTH_REQUIRED：缺少有效会话；配置 PEGASUS_TOKEN / PEGASUS_COOKIE，'
                              '或授权 PEGASUS_ALLOW_LOG_AUTH=1 使用当前编辑器登录会话。无需逐个打开作品。') from None
        games, seen, page_keys = [], set(), set()
        current, scanned, pages = offset, 0, 0
        total, changed, reached_end, reason = None, False, False, 'scan_limit'
        with client as api:
            for _ in range(max_pages):
                rows, page_total = self._page(api.get(f'{BASE}/game', params={
                    'sourceType': 5, 'limit': page_size, 'offset': current}), page_size)
                pages += 1
                if pages > 1 and total != page_total:
                    changed = True
                total = page_total
                ids = tuple(row['game_id'] for row in rows)
                if ids and ids in page_keys:
                    reason = 'repeated_page'
                    changed = True
                    break
                page_keys.add(ids)
                for row in rows:
                    gid = row['game_id']
                    if gid in seen:
                        changed = True
                        continue
                    seen.add(gid)
                    if query is None or (row['name'] == query if match_mode == 'exact'
                                         else query.casefold() in row['name'].casefold()):
                        games.append(row)
                scanned += len(rows)
                current += len(rows)
                if total is not None:
                    if rows and current > total:
                        changed = True
                    reached_end = current >= total
                    if not rows and not reached_end:
                        changed = True
                        reason = 'list_changed'
                        break
                else:
                    reached_end = len(rows) < page_size
                if reached_end:
                    reason = 'end'
                    break
        if changed and reason != 'repeated_page':
            reason = 'list_changed'
        elif reached_end and offset > 0:
            reason = 'partial_range'
        complete = reached_end and offset == 0 and not changed
        return {'source': 'backend', 'scope': 'current_account_source_type_5',
                'query': query, 'match_mode': match_mode, 'games': games, 'count': len(games),
                'scanned_count': scanned, 'unique_scanned_count': len(seen), 'pages': pages,
                'offset': offset, 'page_size': page_size, 'total': total,
                'complete': complete, 'reached_end': reached_end, 'reason': reason,
                'next_offset': current if not reached_end and not changed else None,
                'restart_required': changed,
                'note': '仅当前账号后台可见的天马作品；关键词在读取各页后匹配。'
                        '分页不是原子快照；无匹配不证明作品被删除或无权限。'}

    @discovery_errors
    def resolve_game(self, game_id: int | None = None, name: str | None = None,
                     exact: bool = True, page_size: int = 100, max_pages: int = 100) -> dict:
        if (game_id is None) == (name is None):
            raise BridgeError('game_id 与 name 必须且只能提供一个')
        if game_id is not None:
            positive_id(game_id)
        if type(exact) is not bool:
            raise BridgeError('exact 必须为布尔值')
        result = self.list_games(name, 0, page_size, max_pages, 'exact' if exact else 'contains')
        candidates = [g for g in result['games'] if game_id is None or g['game_id'] == game_id]
        status = ('incomplete' if not result['complete'] else 'not_found' if not candidates
                  else 'resolved' if len(candidates) == 1 else 'ambiguous')
        result.pop('games')
        return {**result, 'count': len(candidates), 'requested_game_id': game_id,
                'status': status, 'candidates': candidates,
                'game': candidates[0] if status == 'resolved' else None}
