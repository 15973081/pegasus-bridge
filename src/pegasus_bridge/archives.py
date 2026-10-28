"""Read native v2 Local saves without loading a game or following stored URLs.

Evidence: Client.Core ArchiveManagerV2 (GetLocalSavePath, LocalArchiveStorage,
ProgressData) and AVGVariableManager.CaptureSaveSnapshot. Editor saves are test
snapshots, not the live Lua VM or a production player's inventory.
"""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import stat

from .core import BridgeError, positive_id, redact


SNAPSHOT_FIELDS = ('normal_variables_base', 'normal_variables_increment',
                   'forever_variables', 'normal_arrays', 'forever_arrays')
MAX_METADATA = 256 * 1024
MAX_PROGRESS = 8 * 1024 * 1024


def _integer(value, name, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise BridgeError(f'存档字段 {name} 必须是范围内的整数')
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-finite JSON number')


def _safe_json_value(value, depth=0):
    if depth > 16:
        raise BridgeError('存档变量嵌套过深')
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > 2**63 - 1:
            raise BridgeError('存档变量整数超出范围')
    elif type(value) is float:
        if not math.isfinite(value):
            raise BridgeError('存档变量包含非有限数值')
    elif isinstance(value, str):
        if len(value) > 65536:
            raise BridgeError('存档变量字符串过长')
    elif isinstance(value, (list, dict)):
        if len(value) > 10000:
            raise BridgeError('存档变量集合过大')
        if isinstance(value, dict) and any(not isinstance(k, str) or len(k) > 256 for k in value):
            raise BridgeError('存档变量键格式无效')
        for child in value.values() if isinstance(value, dict) else value:
            _safe_json_value(child, depth + 1)
    else:
        raise BridgeError('存档变量类型不支持')


def _snapshot(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(SNAPSHOT_FIELDS):
        raise BridgeError('不支持的变量快照结构')
    for name in SNAPSHOT_FIELDS:
        if not isinstance(value[name], dict):
            raise BridgeError('变量快照分类必须为对象')
        _safe_json_value(value[name])
    if any(type(v) not in (int, float) for v in value['normal_variables_increment'].values()):
        raise BridgeError('普通变量增量必须为数值')
    return redact(value)


class ArchiveService:
    def __init__(self, bridge):
        self.bridge = bridge

    def _path(self, *parts):
        root = self.bridge.data.expanduser().resolve()
        candidate = root.joinpath(*parts)
        if not candidate.resolve().is_relative_to(root):
            raise BridgeError('存档路径越界')
        current = root
        for part in parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise BridgeError('拒绝读取符号链接或 junction 存档路径')
        return candidate

    def _selection(self, game_id, mode, user_id):
        game_id = positive_id(game_id)
        _integer(game_id, 'game_id', 1, 2**31 - 1)
        if mode not in ('Editor', 'Runtime'):
            raise BridgeError('mode 只支持 Editor 或 Runtime')
        if user_id is not None:
            _integer(user_id, 'user_id', 1, 2**31 - 1)
        # No automatic fallback from one identity to another or to legacy data.
        parts = ['ArchiveFiles', str(game_id)]
        if user_id is not None:
            parts.append(str(user_id))
        parts.extend([mode, 'Local'])
        self._path(*parts)
        return parts

    def _read_json(self, parts, maximum):
        path = self._path(*parts)
        try:
            with path.open('rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
                    raise BridgeError('存档文件为空、过大或不是普通文件')
                if os.name == 'nt':
                    import ctypes
                    from ctypes import wintypes
                    import msvcrt
                    get_path = ctypes.WinDLL('kernel32', use_last_error=True).GetFinalPathNameByHandleW
                    get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
                    get_path.restype = wintypes.DWORD
                    buffer = ctypes.create_unicode_buffer(32768)
                    count = get_path(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
                    if not 0 < count < len(buffer):
                        raise BridgeError('无法验证已打开存档文件的真实路径')
                    actual = buffer.value
                    actual = '\\\\' + actual[8:] if actual.startswith('\\\\?\\UNC\\') else actual.removeprefix('\\\\?\\')
                    if Path(actual) != path:
                        raise BridgeError('已打开存档文件路径与预期不一致')
                raw = stream.read(maximum + 1)
                after = os.fstat(stream.fileno())
            current = self._path(*parts).stat()
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            if len(raw) > maximum or identity(before) != identity(after) or identity(after) != identity(current):
                raise BridgeError('存档读取期间发生变化，请重新读取')
            data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_object, parse_constant=_invalid_constant)
            if not isinstance(data, dict):
                raise BridgeError('存档 JSON 顶层必须为对象')
            return data, dict(size_bytes=before.st_size,
                              modified_at=datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat())
        except FileNotFoundError:
            raise BridgeError('存档文件缺失或尚未保存') from None
        except (UnicodeError, ValueError, RecursionError):
            raise BridgeError('存档 JSON 已损坏或格式不支持') from None
        except OSError:
            raise BridgeError('存档文件无法读取') from None

    def _metadata(self, data, game_id, slot, user_id):
        for name in ('id', 'userId', 'gameId', 'position', 'version'):
            _integer(data.get(name), name, 0, 2**31 - 1)
        for name in ('createTime', 'modifyTime'):
            _integer(data.get(name), name)
        if data['version'] != 2 or data['position'] != slot or data.get('isLocal') is not True:
            raise BridgeError('仅支持与槽位匹配的 v2 Local 存档')
        if data['gameId'] not in (0, game_id):
            raise BridgeError('存档作品 ID 与请求作品不一致')
        if data['userId'] and data['userId'] != user_id:
            raise BridgeError('存档玩家 ID 与显式指定身份不一致')
        _integer(data.get('isAuto'), 'isAuto', 0, 1)
        if not isinstance(data.get('profileName'), str) or len(data['profileName']) > 4096:
            raise BridgeError('存档名称格式无效')
        return redact({key: data[key] for key in ('id', 'userId', 'gameId', 'position', 'version',
                                                 'isAuto', 'isLocal', 'profileName', 'createTime', 'modifyTime')})

    def _context(self, game_id, mode, user_id):
        return dict(game_id=game_id, mode=mode, user_id=user_id, source='native_local_archive',
                    identity_scope='explicit_user_directory' if user_id is not None else 'legacy_unbound',
                    live=False, note='历史存档快照，不是当前内存或线上玩家状态；Editor 是编辑器测试存档。未指定 user_id 只读旧目录，不推断当前玩家。')

    def list_archives(self, game_id, mode='Editor', user_id=None):
        parts = self._selection(game_id, mode, user_id)
        folder = self._path(*parts)
        entries = []
        # Native v2 supports slots 1..50. Do not traverse arbitrary child names.
        for slot in range(1, 51):
            entry_parts = parts + [f'archive_{slot}']
            try:
                entry_path = self._path(*entry_parts)
                if not entry_path.exists():
                    continue
                missing = [name for name in ('main_archive.json', 'progress.json')
                           if not self._path(*entry_parts, name).is_file()]
                if 'main_archive.json' in missing:
                    entries.append(dict(slot=slot, status='incomplete', missing_files=missing))
                    continue
                data, info = self._read_json(entry_parts + ['main_archive.json'], MAX_METADATA)
                metadata = self._metadata(data, game_id, slot, user_id)
                progress_path = self._path(*entry_parts, 'progress.json')
                entries.append(dict(slot=slot, status='present' if progress_path.is_file() else 'incomplete',
                                    metadata=metadata, metadata_file=info,
                                    progress_present=progress_path.is_file(), progress_validated=False))
            except BridgeError as exc:
                entries.append(dict(slot=slot, status='invalid', error=str(exc)))
        return {**self._context(game_id, mode, user_id),
                'status': 'missing' if not folder.exists() else ('empty' if not entries else 'found'),
                'archives': entries,
                'note': self._context(game_id, mode, user_id)['note'] + ' 列表仅验证元数据；完整进度请调用 read_archive。'}

    def read_archive(self, game_id, slot, mode='Editor', user_id=None):
        parts = self._selection(game_id, mode, user_id)
        _integer(slot, 'slot', 1, 50)
        parts += [f'archive_{slot}']
        header, header_info = self._read_json(parts + ['main_archive.json'], MAX_METADATA)
        metadata = self._metadata(header, game_id, slot, user_id)
        progress, progress_info = self._read_json(parts + ['progress.json'], MAX_PROGRESS)
        for name in ('scene_id', 'display_start_scene_id', 'cmd_executes_times'):
            _integer(progress.get(name), name, 0, 2**31 - 1)
        if type(progress.get('is_ui_open')) is not bool:
            raise BridgeError('存档 is_ui_open 必须为布尔值')
        counts = {}
        for name in ('cmd_exe_list', 'event_record_infos', 'video_end_list', 'option_indexs', 'random_values'):
            if not isinstance(progress.get(name), list):
                raise BridgeError('存档命令或事件记录结构不支持')
            counts[name] = len(progress[name])
        if 'save_snapshot' not in progress or 'script_start_snapshot' not in progress:
            raise BridgeError('存档不含受支持的 v2 变量快照字段')
        snapshots = {name: _snapshot(progress[name]) for name in ('script_start_snapshot', 'save_snapshot')}
        saved = snapshots['save_snapshot']
        snapshot_state = 'missing' if saved is None else ('empty' if not any(saved.values()) else 'available')
        # Detect a simultaneous metadata replacement across the two-file read.
        latest_header, latest_info = self._read_json(parts + ['main_archive.json'], MAX_METADATA)
        if latest_header != header or latest_info != header_info:
            raise BridgeError('存档元数据读取期间发生变化，请重新读取')
        return {**self._context(game_id, mode, user_id), 'slot': slot, 'metadata': metadata,
                'files': {'metadata': header_info, 'progress': progress_info},
                'progress': {name: progress[name] for name in ('scene_id', 'display_start_scene_id', 'cmd_executes_times', 'is_ui_open')},
                'record_counts': counts, 'snapshot_state': snapshot_state, 'snapshots': snapshots,
                'limitations': ['只返回原生变量快照；Lua 局部变量和闭包不包含在快照中',
                                'normal_variables_base 与 normal_variables_increment 分开返回；不将存档值宣称为读档后实时值',
                                '不读取 Clouds 缓存、任意 progress 路径、截图 URL 或命令/回调脚本']}
