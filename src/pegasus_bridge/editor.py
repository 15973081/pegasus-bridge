"""Direct native launch and explicitly scoped, credential-free state observations."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode

from .core import BridgeError, positive_id
from .ui import digest


def now():
    return datetime.now(timezone.utc).isoformat()


def local_sdk_uid():
    """Match SchemeLaunchService's key exactly; sdkUid is a different identity."""
    if os.name != 'nt':
        raise BridgeError('原生作品打开仅支持 Windows')
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\com.NetEase\Pegasus') as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, value, _ = winreg.EnumValue(key, i)
                if name == 'tianmaSdkUid' or name.startswith('tianmaSdkUid_h'):
                    uid = value.decode('utf-8').rstrip('\0') if isinstance(value, bytes) else value
                    if isinstance(uid, str) and re.fullmatch(r'[A-Za-z0-9_.@-]{1,256}', uid) and uid.lower() not in ('null', 'none'):
                        return uid
    except (OSError, UnicodeError):
        pass
    raise BridgeError('未找到天马登录账号标识；请先完成一次编辑器登录')


def pipe_available():
    if os.name != 'nt':
        return False
    import ctypes
    # Bounded 1ms readiness probe; never opens, writes or consumes the pipe.
    if ctypes.windll.kernel32.WaitNamedPipeW(r'\\.\pipe\PegasusEditorScheme', 1):
        return True
    return ctypes.windll.kernel32.GetLastError() in (121, 231)


def cursor_at(stream, offset):
    stat = os.fstat(stream.fileno())
    stream.seek(max(0, offset - 64))
    anchor = stream.read(min(64, offset))
    return dict(device=stat.st_dev, inode=stat.st_ino, offset=offset,
                anchor=hashlib.sha256(anchor).hexdigest())


def log_events(path, since=None, launcher=False):
    """Only fresh, allowlisted events. A baseline never replays historical logs."""
    if since is not None:
        if not isinstance(since, dict) or set(since) != {'device', 'inode', 'offset', 'anchor'}:
            raise BridgeError('无效的日志游标；使用 editor-state 返回的 cursor')
        if any(type(since[k]) is not int or since[k] < 0 for k in ('device', 'inode', 'offset')) or not isinstance(since['anchor'], str) or not re.fullmatch('[a-f0-9]{64}', since['anchor']):
            raise BridgeError('无效的日志游标')
    try:
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            current = cursor_at(stream, size)
            if since is None:
                return dict(cursor=current, events=[], continuity='baseline', more=False)
            if size < since['offset'] or cursor_at(stream, since['offset']) != since:
                return dict(cursor=current, events=[], continuity='reset', more=False)
            stream.seek(since['offset'])
            raw = stream.read(1024 * 1024)
            # Keep incomplete lines for the next poll; never match partial IDs.
            end = raw.rfind(b'\n') + 1
            if not end and len(raw) == 1024 * 1024:
                return dict(cursor=cursor_at(stream, size), events=[], continuity='oversized_line', more=False)
            raw = raw[:end]
            cursor = cursor_at(stream, since['offset'] + end)
    except FileNotFoundError:
        return dict(cursor=None, events=[], continuity='unavailable', more=False)
    events = []
    for line in raw.decode('utf-8', errors='replace').splitlines():
        if launcher:
            if '已转发到运行中的编辑器' in line:
                events.append(dict(event='forwarded_to_editor', game_id=None))
            elif re.search(r'已启动编辑器 pid=\d+', line):
                events.append(dict(event='editor_started', game_id=None))
            continue
        if '[SchemeLaunch]' not in line:
            continue
        match = re.search(r'gameId=(\d+)(?=\D|$)', line)
        gid = int(match[1]) if match else None
        event = None
        if '用户与' in line and '不一致' in line:
            event = 'account_mismatch'
        elif '跳转作品失败' in line:
            event = 'navigation_failed'
        elif gid and 'RequestOpusData 失败回调' in line:
            event = 'metadata_failed'
        elif gid and 'RequestOpusData 成功回调' in line:
            event = 'metadata_loaded' if 'hasData=True' in line else 'metadata_empty'
        elif gid and 'RequestOpusData 开始请求' in line:
            event = 'metadata_requested'
        elif gid and '冷启动目标作品处理完成' in line:
            event = 'navigation_dispatched'
        if event:
            events.append(dict(event=event, game_id=gid))
    return dict(cursor=cursor, events=events[-100:], continuity='continuous',
                more=cursor['offset'] < size, events_truncated=len(events) > 100)


class EditorService:
    def __init__(self, bridge):
        self.bridge = bridge

    @property
    def launcher_log(self):
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'PegasusEditor/Launcher/launcher.log'

    def state(self, since=None):
        status = self.bridge.status()
        return dict(observed_at=now(), source='native_process_and_log',
                    running=any(p['executable'].lower() == 'release_windows_tianma.exe' for p in status['processes']),
                    processes=status['processes'], scheme_pipe_available=pipe_available(),
                    current_game_id=None, editor_ready=None, gameplay_running=None,
                    observation=log_events(self.bridge.log, since),
                    limitations=['原生通信没有已验证的实时状态查询；进程存活不代表作品已打开',
                                 'events 仅是游标之后的日志事件，不代表当前页面或运行变量'])

    def open_game(self, game_id, wait_seconds=3):
        game_id = positive_id(game_id)
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= 15:
            raise BridgeError('wait_seconds 必须是 0 到 15 的整数')
        if os.name != 'nt':
            raise BridgeError('原生作品打开仅支持 Windows')
        launcher = self.bridge.install / 'PegasusEditorLauncher.exe'
        if not launcher.is_file():
            raise BridgeError('安装目录中不存在 PegasusEditorLauncher.exe')
        # Verify access before asking the native application to navigate.
        self.bridge.read('game', game_id)
        uid = local_sdk_uid()
        observation = log_events(self.bridge.log)
        delivery_observation = log_events(self.launcher_log, launcher=True)
        url = 'pegasus-editor://open?' + urlencode({'gameId': game_id, 'uid': uid})
        # Native launcher owns single-instance IPC; no guessed pipe wire format,
        # shell parsing, mouse/keyboard input, process termination or save bypass.
        try:
            process = subprocess.Popen([str(launcher), url], cwd=self.bridge.install,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        except OSError:
            raise BridgeError('无法调用天马启动器') from None
        deadline = time.monotonic() + wait_seconds
        events = []
        delivery = 'unconfirmed'
        while time.monotonic() < deadline:
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
            observation = log_events(self.bridge.log, observation['cursor'])
            delivery_observation = log_events(self.launcher_log, delivery_observation['cursor'], launcher=True)
            if delivery_observation['events']:
                delivery = delivery_observation['events'][-1]['event']
            events.extend(e for e in observation['events'] if e['game_id'] in (None, game_id))
            if any(e['event'] in ('account_mismatch', 'navigation_failed', 'metadata_failed', 'metadata_empty', 'metadata_loaded') for e in events):
                break
        exit_code = process.poll()
        if exit_code is not None and exit_code != 0:
            raise BridgeError(f'原生启动器退出失败（exit_code={exit_code}）；请检查安装与登录状态，不要自动重复打开')
        return dict(game_id=game_id, request_sent=True, launcher_pid=process.pid,
                    launcher_exit_code=exit_code, delivery=delivery,
                    observed_at=now(), progress=events[-1]['event'] if events else 'sent_unconfirmed',
                    events=events, cursor=observation['cursor'], editor_ready=None,
                    note='已通过原生协议发送；加载事件不等于编辑器就绪。已有作品时天马可能要求确认未保存编辑；本工具不代为确认。')

    def game_state(self, game_id, scene_id=None, include_ui=False):
        game_id = positive_id(game_id)
        if scene_id is not None:
            scene_id = positive_id(scene_id)
        if type(include_ui) is not bool:
            raise BridgeError('include_ui 必须为布尔值')
        resources = ['game', 'scenes', 'variables', 'arrays', 'goods']
        if include_ui:
            resources += ['ui_catalog', 'ui_template']
        if scene_id is not None:
            resources += ['commands', 'story_tree']
        started = now()
        values, errors = {}, {}
        for resource in resources:
            try:
                result = self.bridge.read(resource, game_id, scene_id)
                data = result['response']['data']
                values[resource] = dict(data=data, sha256=digest(data), read_at=now())
            except BridgeError as exc:
                errors[resource] = str(exc)
        return dict(game_id=game_id, scene_id=scene_id, source='persisted_backend',
                    started_at=started, completed_at=now(), complete=not errors,
                    resources=values, errors=errors, runtime_state=None,
                    note='逐项读取的已保存作品数据，非原子快照。variables/arrays 是编辑定义，不是玩家运行值；不包含未保存编辑、游玩进度或实时画面。')
