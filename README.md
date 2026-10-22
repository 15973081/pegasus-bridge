# PegasusEditor CLI / MCP

Windows 天马编辑器（默认 `C:\APP\PegasusEditor`）的非官方基础适配工具。Python 3.12+，使用 uv 安装锁定依赖。不会修改编辑器安装文件，仓库不包含编辑器二进制、登录凭据或作品数据。

## 当前能力与验证范围

| 能力 | CLI | MCP | 验证情况 |
|---|---|---|---|
| 安装、构建信息、进程诊断 | `status` | `pegasus_status` | 本机实测 |
| 启动编辑器 | `launch` | `pegasus_launch` | 本机实测启动 |
| 历史访问的作品和场景 ID | `recent-games` | `pegasus_recent_games` | 本机日志实测 |
| 已观测的后台路径 | `routes` | `pegasus_routes` | 本机日志实测 |
| 脱敏日志 | `logs` | `pegasus_logs` | 本机读取、脱敏测试 |
| 本地指令分类缓存 | `catalog` | `pegasus_command_catalog` | 本机读取、导出 |
| 作品、场景、指令、变量、数组、UI 分类读取 | `read` | `pegasus_read` | 2026-10-22 已使用当前会话在线验证全部六类资源 |
| JSON 导出 | `export` | `pegasus_export` | 本地缓存及六类在线资源已实测 |

**限制：** 在线读取已打通，使用 `TOKEN` Cookie 与 CSRF 查询参数，成功业务码为 `200000`。会话过期后仍会返回 `502001`。未实现剧情修改、素材上传、保存、发布或 Unity 内部对象控制。`catalog` 是指令分类缓存，不是场景剧情内容。`recent-games` 是历史记录，不是账号全部作品。响应保留后台结构，`scenes` 的 data 以及 UI 分类中的 command 可能是 JSON 字符串，需要调用方再次解析。

## 使用

在 PowerShell 中：

```powershell
git clone https://github.com/15973081/pegasus-bridge.git
cd pegasus-bridge
uv sync --locked
.\pegasus.cmd status
.\pegasus.cmd launch
.\pegasus.cmd recent-games
.\pegasus.cmd routes
.\pegasus.cmd logs --lines 30
.\pegasus.cmd catalog 123456
.\pegasus.cmd export catalog 123456 --output '.\exports\catalog.json'
```

所有成功结果输出 JSON 到 stdout，错误输出 JSON 到 stderr 并返回退出码 1。导出文件不会覆盖；请使用新文件名。

需要先安装 Python 3.12+ 和 uv。示例作品 ID `123456`、场景 ID `987654` 请替换为自己的实际 ID，可通过 `recent-games` 查询历史访问记录。也可使用 `uv run pegasus ...`；全局安装可用 `uv tool install .`。无须管理员权限。

## 在线读取的认证配置

凭据仅从运行环境读取，不写入配置文件或导出文件。应使用你自己的有效会话。

- `PEGASUS_TOKEN`：门户的有效 TOKEN Cookie 值；默认发送为 `Cookie: TOKEN=...` 并用作 CSRF 值。
- `PEGASUS_TOKEN_HEADER`：可选的自定义令牌请求头名称；默认不设置。仅适用于明确需要自定义头的环境。
- `PEGASUS_COOKIE`：可选的完整 Cookie 请求头，可单独提供。
- `PEGASUS_CSRF_TOKEN`：可选的 CSRF 值。
- `PEGASUS_ALLOW_LOG_AUTH=1`：没有显式 token/cookie 时，从本地请求日志中最新的 `csrf_token` 还原 TOKEN Cookie；不使用 Unity 的 `headerToken`。库和公开示例默认关闭，需要使用自己的本机会话时再启用，日志会话仍可能过期。
- `PEGASUS_INSTALL_DIR` / `PEGASUS_DATA_DIR`：覆盖安装目录与用户数据目录。

配置有效会话后：

```powershell
$env:PEGASUS_ALLOW_LOG_AUTH = '1' # 使用当前本机编辑器会话
.\pegasus.cmd read game 123456
.\pegasus.cmd read scenes 123456
.\pegasus.cmd read commands 123456 --scene-id 987654
.\pegasus.cmd read variables 123456
.\pegasus.cmd read arrays 123456
.\pegasus.cmd read ui_catalog 123456
.\pegasus.cmd export commands 123456 --scene-id 987654 --output '.\exports\scene.json'
```

接口固定为 `https://avg.163.com/avg-editor-api`，仅 GET，不跟随重定向。请求失败不会生成导出文件。返回数据中常见的令牌、Cookie、密码、会话键会被隐藏，诊断日志中的认证行会整行隐藏。不要把原始编辑器日志作为共享附件。

## MCP 接入

复制 `mcp-config.example.json` 为本地 `mcp-config.json`，将示例命令替换为实际 Python 绝对路径，再将 `mcpServers.pegasus-editor` 合并进支持该格式的 MCP 客户端配置。本地配置已加入 Git 忽略列表。其他客户端请填写：

- 命令：本项目 `.venv\Scripts\python.exe` 的绝对路径
- 参数：`-m`、`pegasus_bridge.server`
- 传输：stdio

需要在线读取时，在 MCP 客户端的服务器环境变量里配置上述会话。CLI 的 PowerShell 环境不会自动传播给已经运行的桌面客户端。

直接启动服务可用 `.\pegasus-mcp.cmd` 或 `uv run pegasus-mcp`；服务等待 MCP 消息，不提供交互式提示符。服务采用 [官方 MCP Python SDK](https://py.sdk.modelcontextprotocol.io/v1/) 的 FastMCP 与 stdio 传输，依赖锁定在 1.x。

## 检查结果

检查日期：2026-10-22。

- 安装为 Unity / IL2CPP 发布包，含 `GameAssembly.dll`，不是源代码仓库。
- `launcher.json` 指向 `Release_Windows_Tianma.exe`。
- 内置构建信息为 2026-09-29；热更新缓存另有 2026-10-19 版本，因此此构建日期不代表全部热更新内容。
- 注册表存在 `pegasus-editor` 协议，指向 `PegasusEditorLauncher.exe`；本适配层仅调用普通启动，不构造未经验证的作品深链参数。
- 数据目录为 `%USERPROFILE%\AppData\LocalLow\com_NetEase\Pegasus`。
- 历史日志中观测到 `/game/{id}`、`/game/{id}/chapter/scene`、`/command-line/game/{id}/scene/{scene_id}/command` 等路径。它们是非公开、可能变化的接口。
- 未发现已验证的原生 CLI 或 MCP 服务入口；该结论不是对所有二进制内部能力的穷尽证明。

## 测试

```powershell
uv run python -m unittest discover -s tests -v
uv run python tests/smoke_mcp.py
```

10 项单元测试覆盖实际 Windows 进程枚举、脱敏、ID 校验、缺少会话、显式日志会话、TOKEN Cookie 构造、显式 Cookie 优先级、真实成功码 200000、后台拒绝、失败不落盘、避免覆盖。stdio 集成测试实际启动独立 MCP 子进程，验证初始化、8 个工具发现、成功调用和错误调用。另已通过 MCP 实际读取当前作品。测试不修改云端作品。
