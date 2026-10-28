"""Opt-in runtime observations, never inferred from source code or purchases."""
from datetime import datetime
import json
import re

from .core import BridgeError, positive_id, SECRET_KEY


MARKER = '[PegasusPlayerStateV1]'
MAX_TAIL = 8 * 1024 * 1024
MAX_EVENT = 131072


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def clean_text(value, limit=256):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('invalid text')
    return value


def validate_snapshot(data):
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('unsupported schema')
    result = {'schema_version': 1}
    for name in ('game_id', 'scene_id', 'sequence', 'chapter', 'highest_chapter'):
        value = data.get(name)
        if type(value) is not int or not 0 < value <= 2**53 - 1:
            raise ValueError('invalid number')
        result[name] = value
    if result['highest_chapter'] < result['chapter']:
        raise ValueError('invalid chapter')
    result['node'] = clean_text(data.get('node'))
    for name in ('flags', 'endings'):
        values = data.get(name)
        if not isinstance(values, dict) or len(values) > 2048:
            raise ValueError('invalid flags')
        result[name] = {}
        for key, value in values.items():
            clean_text(key)
            if type(value) is not bool:
                raise ValueError('invalid flag')
            if not SECRET_KEY.search(key):
                result[name][key] = value
    inventory = data.get('inventory')
    if not isinstance(inventory, list) or len(inventory) > 512:
        raise ValueError('invalid inventory')
    items, seen = [], set()
    for row in inventory:
        if not isinstance(row, dict):
            raise ValueError('invalid item')
        item_id = clean_text(row.get('id'))
        if item_id in seen:
            raise ValueError('duplicate item')
        seen.add(item_id)
        quantity = row.get('quantity')
        if type(quantity) is not int or not 0 <= quantity <= 2**53 - 1:
            raise ValueError('invalid quantity')
        items.append(dict(id=item_id, name=clean_text(row.get('name'), 1024), quantity=quantity,
                          category=clean_text(row.get('category', 'item')),
                          origin=clean_text(row.get('origin', 'unknown'))))
    result['inventory'] = items
    return result


def event_time(line, modified):
    match = re.match(r'^\[(\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
    if not match:
        return None
    # Native logger omits the year. Infer the nearest year to file modification,
    # not wall-clock time, so touching the log cannot make an old line recent.
    candidates = []
    for year in range(modified.year - 1, modified.year + 2):
        try:
            candidates.append(datetime.strptime(f'{year}-{match[1]}', '%Y-%m-%d %H:%M:%S').astimezone())
        except ValueError:
            pass
    return min(candidates, key=lambda value: abs((value - modified).total_seconds())) if candidates else None


class PlayerService:
    def __init__(self, bridge):
        self.bridge = bridge

    def player_state(self, game_id, max_age_seconds=30):
        game_id = positive_id(game_id)
        if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
            raise BridgeError('max_age_seconds 必须是 1 到 3600 的整数')
        result = dict(game_id=game_id, source='instrumented_runtime_log', is_live=False,
                      status='unavailable', fresh=False, snapshot=None, observed_at=None,
                      age_seconds=None, identity='unbound_local_game_instance',
                      note='这是作品主动上报的最近一次状态，不是实时内存查询，也不是线上任意玩家数据。停止试玩后仍可能保留旧快照；没有事件不能视为空背包。')
        try:
            with self.bridge.log.open('rb') as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - MAX_TAIL))
                raw = stream.read(MAX_TAIL)
                stat = self.bridge.log.stat()
        except FileNotFoundError:
            result['reason'] = 'log_missing'
            return result
        if size > MAX_TAIL:
            raw = raw.partition(b'\n')[2]
        # A partial event may be concurrently written: only inspect full lines.
        raw = raw[:raw.rfind(b'\n') + 1]
        result['scan_truncated'] = size > MAX_TAIL
        rejected = 0
        modified = datetime.fromtimestamp(stat.st_mtime).astimezone()
        for line in reversed(raw.decode('utf-8', errors='replace').splitlines()):
            if MARKER not in line:
                continue
            body = line.partition(MARKER)[2]
            if len(body.encode('utf-8')) > MAX_EVENT:
                rejected += 1
                continue
            try:
                data = validate_snapshot(json.loads(body, object_pairs_hook=unique_object))
            except (ValueError, TypeError, RecursionError):
                rejected += 1
                continue
            if data['game_id'] != game_id:
                continue
            timestamp = event_time(line, modified)
            age = (datetime.now().astimezone() - timestamp).total_seconds() if timestamp else None
            fresh = age is not None and -5 <= age <= max_age_seconds
            result.update(status='observed' if fresh else 'stale', fresh=fresh, snapshot=data,
                          observed_at=timestamp.isoformat() if timestamp else None,
                          age_seconds=round(age, 3) if age is not None else None,
                          timestamp_basis='native_log_local_time_year_inferred' if timestamp else 'unknown')
            break
        result['rejected_events'] = rejected
        if result['snapshot'] is None:
            result['reason'] = 'no_valid_state_event'
        return result

    def inventory(self, game_id, max_age_seconds=30):
        result = self.player_state(game_id, max_age_seconds)
        snapshot = result.pop('snapshot')
        items = snapshot['inventory'] if snapshot is not None else None
        result.update(items=items, item_kinds=len([x for x in items if x['quantity'] > 0]) if items is not None else None,
                      total_quantity=sum(x['quantity'] for x in items) if items is not None else None,
                      scene_id=snapshot['scene_id'] if snapshot else None,
                      node=snapshot['node'] if snapshot else None,
                      chapter=snapshot['chapter'] if snapshot else None,
                      sequence=snapshot['sequence'] if snapshot else None)
        return result
