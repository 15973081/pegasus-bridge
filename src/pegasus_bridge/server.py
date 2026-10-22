from typing import Literal
import logging
from mcp.server.fastmcp import FastMCP
from .core import Pegasus


def create_server(bridge: Pegasus | None = None):
    # FastMCP enables INFO logging; HTTP client request URLs contain csrf_token.
    # Do not let transport diagnostics expose authenticated query strings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app = FastMCP("pegasus-editor", instructions="PegasusEditor 基础适配层。recent_games 是历史日志；在线读取需有效登录。无写入或发布接口。")
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
    def pegasus_read(resource: Literal["game", "scenes", "commands", "variables", "arrays", "ui_catalog"], game_id: int, scene_id: int | None = None) -> dict:
        """从后台读取作品/场景/指令/变量；commands 必须提供 scene_id。"""
        return bridge.read(resource, game_id, scene_id)

    @app.tool(annotations={"destructiveHint": False})
    def pegasus_export(resource: Literal["game", "scenes", "commands", "variables", "arrays", "ui_catalog", "catalog"], game_id: int, output: str, scene_id: int | None = None) -> dict:
        """导出一个资源到本地新 JSON 文件；已有文件不会覆盖。catalog 可离线导出。"""
        return bridge.export(resource, game_id, output, scene_id)

    return app


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
