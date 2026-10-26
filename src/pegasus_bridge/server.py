from typing import Literal
import logging
from mcp.server.fastmcp import FastMCP
from .core import Pegasus
from .ui import UIService
from .scene import SceneService


def create_server(bridge: Pegasus | None = None):
    # FastMCP enables INFO logging; HTTP client request URLs contain csrf_token.
    # Do not let transport diagnostics expose authenticated query strings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app = FastMCP("pegasus-editor", instructions="PegasusEditor 适配层。支持读取、PNG 素材上传和带备份的基本 UI 与剧情写入；需用户授权及有效登录，不支持发布。recent_games 是历史日志。")
    bridge = bridge or Pegasus()
    readonly = {"readOnlyHint": True, "destructiveHint": False}

    @app.tool(annotations=readonly)
    def pegasus_status() -> dict:
        """检查安装、版本、进程及认证配置状态；不返回凭据。"""
        return bridge.status()

    @app.tool(annotations={"destructiveHint": False})
    def pegasus_launch() -> dict:
        """启动 Windows PegasusEditor；登录仍由用户在编辑器中完成。"""
        return bridge.launch()

    @app.tool(annotations=readonly)
    def pegasus_recent_games() -> dict:
        """列出本地历史日志中出现的作品和场景 ID，非完整作品列表。"""
        return bridge.recent_games()

    @app.tool(annotations=readonly)
    def pegasus_routes() -> dict:
        """列出本地日志实际出现的接口路径，不返回查询参数和凭据。"""
        return bridge.routes()

    @app.tool(annotations=readonly)
    def pegasus_logs(lines: int = 60) -> dict:
        """读取最多 500 行脱敏日志。"""
        return bridge.logs(lines)

    @app.tool(annotations=readonly)
    def pegasus_command_catalog(game_id: int) -> dict:
        """读取本地缓存的指令分类；不等同于作品中的剧情指令。"""
        return bridge.command_catalog(game_id)

    @app.tool(annotations=readonly)
    def pegasus_read(resource: Literal["game", "scenes", "commands", "variables", "arrays", "ui_catalog", "ui_template"], game_id: int, scene_id: int | None = None) -> dict:
        """从后台读取作品/场景/指令/变量；commands 必须提供 scene_id。"""
        return bridge.read(resource, game_id, scene_id)

    @app.tool(annotations={"destructiveHint": False})
    def pegasus_export(resource: Literal["game", "scenes", "commands", "variables", "arrays", "ui_catalog", "ui_template", "catalog"], game_id: int, output: str, scene_id: int | None = None) -> dict:
        """导出一个资源到本地新 JSON 文件；已有文件不会覆盖。catalog 可离线导出。"""
        return bridge.export(resource, game_id, output, scene_id)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def pegasus_upload_image(game_id: int, image_path: str, alias: str, category_id: Literal[1, 2], checkpoint: str) -> dict:
        """经用户授权，将一个本地 PNG 上传到指定作品；1=背景、2=组件。独立检查点可避免重复上传。"""
        return UIService(bridge).upload_image(game_id, image_path, alias, category_id, checkpoint)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    def pegasus_apply_ui_template(game_id: int, section: Literal['cover', 'configuration', 'record', 'playback', 'menu'], template: dict, expected_sha256: str, backup_directory: str) -> dict:
        """经用户授权更新一个基本 UI 分类，自动备份并读回验证。expected_sha256 是旧分类对象排序、紧凑 JSON（ensure_ascii=False）的 UTF-8 SHA256。后台无原子 CAS；编辑器应避免同时保存。可用备份 before[section] 和当前哈希进行显式恢复。"""
        return UIService(bridge).apply_template(game_id, section, template, expected_sha256, backup_directory)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    def pegasus_apply_scene(game_id: int, scene_id: int, script_path: str, expected_sha256: str, backup_directory: str) -> dict:
        """经授权替换一段剧情，自动备份、比对旧源码 UTF-8 SHA256 并读回验证。后台没有原子 CAS，请避免同时保存；本工具不发布作品，不保证编辑器 Lua 兼容性。"""
        return SceneService(bridge).apply_scene(game_id, scene_id, script_path, expected_sha256, backup_directory)

    return app


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
