"""Pegasus inspection and authenticated reads; bounded UI writes live in ui.py."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

import httpx


class BridgeError(Exception):
    pass


SECRET_KEY = re.compile(r"(?i)(token|password|passwd|secret|cookie|authorization|session|ticket|signature)")


def redact(value):
    if isinstance(value, dict):
        return {k: "<redacted>" if SECRET_KEY.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?i)(\b(?:authorization|cookie|set-cookie)\s*[:=]\s*)[^\r\n]+', r'\1<redacted>', value)
        return re.sub(r'''(?ix)([\w-]*(?:token|password|passwd|secret|session|ticket|signature)[\w-]*["']?\s*[:=]\s*["']?)[^\s&,;"'\}\]]+''', r'\1<redacted>', value)
    return value


def positive_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BridgeError("ID 必须为正整数")
    return value


class Pegasus:
    def __init__(self, install: str | Path | None = None, data: str | Path | None = None):
        self.install = Path(install or os.environ.get("PEGASUS_INSTALL_DIR", r"C:\APP\PegasusEditor"))
        self.data = Path(data or os.environ.get("PEGASUS_DATA_DIR", str(Path.home() / "AppData/LocalLow/com_NetEase/Pegasus")))

    @property
    def log(self):
        return self.data / "log.txt"

    def _lines(self):
        if not self.log.is_file():
            return
        with self.log.open(encoding="utf-8", errors="replace") as stream:
            yield from stream

    def status(self) -> dict:
        launcher = self.install / "PegasusEditorLauncher.exe"
        executable = self.install / "Release_Windows_Tianma.exe"
        build = self.install / "Release_Windows_Tianma_Data/StreamingAssets/build_info"
        processes = []
        if os.name == "nt":
            import win32process
            import win32api
            for pid in win32process.EnumProcesses():
                try:
                    handle = win32api.OpenProcess(0x0410, False, pid)
                    try:
                        path = win32process.GetModuleFileNameEx(handle, 0)
                        if Path(path).parent.resolve() == self.install.resolve():
                            processes.append({"pid": pid, "executable": Path(path).name})
                    finally:
                        handle.Close()
                except Exception:
                    continue
        return {"install_dir": str(self.install), "installed": launcher.is_file() and executable.is_file(),
                "build": build.read_text(errors="replace").strip() if build.exists() else None,
                "data_dir": str(self.data), "log_available": self.log.is_file(), "processes": processes,
                "auth_configured": bool(os.environ.get("PEGASUS_TOKEN") or os.environ.get("PEGASUS_COOKIE")),
                "allow_log_auth": os.environ.get("PEGASUS_ALLOW_LOG_AUTH") == "1",
                "capabilities": ["launch", "open_game", "editor_state", "game_state", "player_state", "inventory", "list_archives", "read_archive", "diagnostics", "recent_games", "command_catalog", "read_api", "export", "upload_image", "apply_ui_template", "apply_scene", "ensure_chapter", "apply_story_tree", "apply_mall_ui", "apply_goods"],
                "limitations": ["非官方适配层；支持基本 UI、剧情、章节、故事线与商城编辑，不支持发布", "在线操作需要有效会话；历史日志中的会话可能失效"]}

    def launch(self) -> dict:
        if os.name != "nt":
            raise BridgeError("启动编辑器仅支持 Windows")
        current = [p for p in self.status()["processes"] if p["executable"].lower() in
                   {"pegasuseditorlauncher.exe", "release_windows_tianma.exe"}]
        if current:
            return {"already_running": True, "processes": current}
        launcher = self.install / "PegasusEditorLauncher.exe"
        if not launcher.is_file():
            raise BridgeError("安装目录中不存在 PegasusEditorLauncher.exe")
        # The visible editor is the requested interactive application, not a background helper.
        process = subprocess.Popen([str(launcher)], cwd=self.install, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"started": True, "launcher_pid": process.pid, "note": "已发起启动；此结果不代表登录或加载已完成"}

    def recent_games(self) -> dict:
        games = {}
        for line in self._lines():
            for gid in re.findall(r"/avg-editor-api/(?:command-line/)?game/(\d+)", line):
                game = games.setdefault(gid, {"game_id": int(gid), "scene_ids": set()})
                game["last_seen"] = line.split("]", 1)[0].lstrip("[")
                game["scene_ids"].update(int(x) for x in re.findall(r"/scene/(\d+)", line))
        return {"source": "local_log", "note": "历史访问记录，不是服务器上的完整作品列表", "games": [
            {**v, "scene_ids": sorted(v["scene_ids"])} for v in games.values()]}

    def routes(self) -> dict:
        routes = set()
        for line in self._lines():
            match = re.search(r"\[NetworkManager\]\[Request\] method=(\w+).*?url=(\S+)", line)
            if match:
                url = urlsplit(match[2])
                if url.hostname == "avg.163.com":
                    routes.add((match[1], url.path))
        return {"source": "local_log", "routes": [{"method": method, "path": path} for method, path in sorted(routes)]}

    def logs(self, lines: int = 60) -> dict:
        if not 1 <= lines <= 500:
            raise BridgeError("lines 必须在 1 到 500 之间")
        if not self.log.exists():
            raise BridgeError("本地编辑器日志不存在")
        # Login payloads are not useful diagnostics; suppress whole lines, not only known keys.
        result = []
        for line in deque(self._lines(), maxlen=lines):
            if re.search(r"(?i)(setuserinfo|login|cookie|authorization|headerToken|csrf_token|password|access_token)", line):
                result.append("[包含认证信息的日志行已隐藏]")
            else:
                result.append(redact(line.rstrip()))
        return {"source": str(self.log), "lines": result}

    def command_catalog(self, game_id: int) -> dict:
        game_id = positive_id(game_id)
        matches = sorted(self.data.glob(f"Categories*_{game_id}.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            raise BridgeError("未找到该作品的本地指令分类缓存；先在编辑器中打开作品")
        return {"source": str(matches[0]), "data": redact(json.loads(matches[0].read_text(encoding="utf-8-sig")))}

    def _auth(self):
        token = os.environ.get("PEGASUS_TOKEN", "")
        csrf = os.environ.get("PEGASUS_CSRF_TOKEN", "")
        cookie = os.environ.get("PEGASUS_COOKIE", "")
        if not token and not cookie and os.environ.get("PEGASUS_ALLOW_LOG_AUTH") == "1":
            for line in self._lines():
                match = re.search(r"\[NetworkManager\]\[Request\].*?headerToken=(\S+) url=(\S+)", line)
                if match and urlsplit(match[2]).hostname == "avg.163.com":
                    # The portal JS sends the TOKEN cookie value as csrf_token.
                    # Unity's headerToken is a separate value and does not authenticate this API.
                    observed = parse_qs(urlsplit(match[2]).query).get("csrf_token", [""])[0]
                    if observed:
                        token = observed
                        csrf = observed
        if not token and not cookie:
            raise BridgeError("缺少会话：设置 PEGASUS_TOKEN / PEGASUS_COOKIE；或 PEGASUS_ALLOW_LOG_AUTH=1 尝试当前本地日志会话")
        header = os.environ.get("PEGASUS_TOKEN_HEADER", "")
        if header and (not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", header) or header.lower() in {"host", "content-length", "connection"}):
            raise BridgeError("无效的 PEGASUS_TOKEN_HEADER")
        headers = {"Accept": "application/json", "Referer": "https://avg.163.com/"}
        if token and header:
            headers[header] = token
        elif token and not cookie:
            if re.search(r"[\s;\r\n]", token):
                raise BridgeError("TOKEN 值格式无效")
            cookie = "TOKEN=" + token
            csrf = csrf or token
        if cookie:
            headers["Cookie"] = cookie
        return headers, {"csrf_token": csrf} if csrf else {}

    def read(self, resource: str, game_id: int, scene_id: int | None = None, catalog_id: int | None = None, ui_id: str | None = None):
        game_id = positive_id(game_id)
        if resource == "mall_ui":
            from .mall import MallUIService
            return MallUIService(self).read_ui(game_id, catalog_id, ui_id)
        paths = {"goods": f"/game/{game_id}/goods", "game": f"/game/{game_id}", "scenes": f"/game/{game_id}/chapter/scene",
                 "variables": f"/game/{game_id}/value", "arrays": f"/game/{game_id}/array",
                 "ui_catalog": f"/command-line/game/{game_id}/custom-ui-catalog",
                 "ui_template": f"/game/{game_id}/config/template"}
        if resource == "commands":
            paths["commands"] = f"/command-line/game/{game_id}/scene/{positive_id(scene_id)}/command"
        if resource == "story_tree":
            paths["story_tree"] = f"/game/{game_id}/scene/{positive_id(scene_id)}/story-tree"
        if resource not in paths:
            raise BridgeError("不支持的资源类型")
        headers, params = self._auth()
        try:
            # Fixed origin, GET-only, no redirects: credentials cannot follow a redirect elsewhere.
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.get("https://avg.163.com/avg-editor-api" + paths[resource], headers=headers, params=params)
            if response.status_code != 200:
                raise BridgeError(f"后台返回 HTTP {response.status_code}；请检查登录状态")
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise BridgeError("后台请求失败或返回非 JSON；请检查网络和会话") from None
        if not isinstance(payload, dict):
            raise BridgeError("后台响应结构异常：预期 JSON 对象")
        state = payload.get("state", {})
        if isinstance(state, dict) and state.get("code") not in (None, 0, 200, 200000):
            raise BridgeError(f"后台拒绝读取（state.code={state.get('code')}）；请重新登录并配置有效会话。历史日志凭据不保证可用")
        if "data" not in payload:
            raise BridgeError("后台响应缺少 data；无法确认读取成功")
        return {"source": "live_api", "resource": resource, "game_id": game_id, "response": redact(payload)}

    def export(self, resource: str, game_id: int, output: str, scene_id: int | None = None, catalog_id: int | None = None, ui_id: str | None = None):
        # Fetch first; a failed request must never produce a success-looking snapshot.
        value = self.command_catalog(game_id) if resource == "catalog" else self.read(resource, game_id, scene_id, catalog_id, ui_id)
        path = Path(output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump({"exported_at": datetime.now(timezone.utc).isoformat(), **value}, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        except FileExistsError:
            raise BridgeError("导出文件已存在，请指定新路径") from None
        return {"path": str(path), "bytes": path.stat().st_size, "source": value["source"]}
