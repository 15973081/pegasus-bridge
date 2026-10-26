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
| 基本 UI 模板读取、导出 | `read ui_template` / `export ui_template` | `pegasus_read` / `pegasus_export` | 2026-10-26 在线读回实测 |
| PNG 素材上传 | `upload-image` | `pegasus_upload_image` | 2026-10-26 通过 MCP 上传 13 张适配素材 |
| 基本 UI 分类保存 | `apply-ui` | `pegasus_apply_ui_template` | 2026-10-26 通过 MCP 更新 playback 并读回一致 |

**限制：** 在线操作使用 `TOKEN` Cookie 与 CSRF 查询参数，写入成功业务码必须为 `200000`。会话过期可能返回 `502001`。0.3.0 支持图片上传、基本 UI 保存和带版本校验的剧情替换；不支持发布或 Unity 内部对象控制。`catalog` 是指令分类缓存，不是场景剧情内容。`recent-games` 是历史记录，不是账号全部作品。响应保留后台结构，`scenes` 的 data、UI 分类中的 command 和 `ui_template` 中的 template 可能是 JSON 字符串，需要再次解析。

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

## 在线操作的认证配置

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

读取接口固定为 `https://avg.163.com/avg-editor-api`，使用 GET，不跟随重定向。请求失败不会生成导出文件。返回数据中常见的令牌、Cookie、密码、会话键会被隐藏，诊断日志中的认证行会整行隐藏。不要把原始编辑器日志作为共享附件。

## 上传素材和更新基本 UI（0.2.0）

仅在用户授权编辑目标作品后执行。上传与模板更新是独立步骤：上传成功不表示 UI 已引用新素材。更新过程中避免在编辑器同时保存同一分类。

### 1. 读取并保存当前模板

```powershell
.\pegasus.cmd export ui_template 123456 --output '.\exports\ui-before.json'
```

`response.data.template` 解析后含 `cover`（封面）、`configuration`（环境设置）、`record`（档案管理）、`playback`（章节列表与故事线）、`menu`（菜单）。更新单位是其中一个分类的**完整对象**，不是整个模板，也不是局部补丁。

可用以下 Python 代码提取待编辑分类，并记录原配置的哈希：

```python
import json
from pathlib import Path
from pegasus_bridge.ui import digest

snapshot = json.loads(Path('exports/ui-before.json').read_text(encoding='utf-8'))
template = snapshot['response']['data']['template']
template = json.loads(template) if isinstance(template, str) else template
section = template['playback']
Path('exports/playback.json').write_text(
    json.dumps(section, ensure_ascii=False, indent=2), encoding='utf-8')
Path('exports/playback.sha256').write_text(digest(section), encoding='ascii')
```

哈希算法：对象键排序、`ensure_ascii=False`、`separators=(',', ':')` 的 JSON，UTF-8 编码后计算 SHA-256。哈希必须来自修改前的分类。

### 2. 上传 PNG，取得素材 ID

```powershell
.\pegasus.cmd upload-image 123456 '.\assets\node-frame.png' --alias 'horror-node-frame' --category-id 2 --checkpoint '.\exports\node-frame-upload.json'
```

- `category-id`：1 为背景，2 为图片组件；仅接收 20MB 以内、宽高 1–8192 的 PNG，并检查文件头。
- 上传使用作品默认素材目录；上传检查点绑定作品 ID、文件 SHA-256、别名和分类。
- 成功后返回 `id`、`gameMaterialId`、尺寸和素材 URL；将 `id` 写入待编辑分类的相应资源字段。
- 有完整 `id` 的检查点会直接复用，不会再次联网核验素材是否仍存在；更换作品、文件或分类必须使用新检查点。
- 同一检查点应串行使用。若素材注册已成功但响应丢失，重试可能产生重复素材；上传不是跨服务事务。
- 文件服务器只接收文件上传专用令牌，不接收门户 Cookie 或 CSRF 值；所有请求禁止跟随重定向。

### 3. 保存并读回核对

编辑 `exports/playback.json` 后：

```powershell
$expectedUiHash = (Get-Content '.\exports\playback.sha256' -Raw).Trim()
.\pegasus.cmd apply-ui 123456 playback --template-file '.\exports\playback.json' --expected-sha256 $expectedUiHash --backup-directory '.\exports\ui-backups'
```

工具先读取并比对原配置哈希，保存完整旧模板和本次目标配置到唯一备份文件，再次检查分类哈希后写入，最后读回校验。分类的顶层字段必须全部保留；嵌套配置需要调用方基于原对象编辑。相同内容返回 `changed: false`。读回不一致不会报告成功，也不会盲目回滚。

后台没有原子 compare-and-swap 接口，两次检查能发现已发生的并发修改，但无法完全消除最后一次检查与写入之间的竞争。请求超时或结果不确定时，应先重新读取，不要直接重复保存。

返回的 `backup` 是旧配置备份路径，旁边 `.result.json` 记录校验结果。需要恢复时，取备份里的 `before[section]` 作为目标对象，重新读取当前分类并计算哈希，再通过同一更新工具恢复。备份含作品配置，请留在本地。

### MCP 对应调用

`pegasus_read(resource="ui_template", game_id=...)` 读取模板；两个写工具的参数与 CLI 对应：

```json
{
  "name": "pegasus_upload_image",
  "arguments": {
    "game_id": 123456,
    "image_path": "C:/my-game/assets/node-frame.png",
    "alias": "horror-node-frame",
    "category_id": 2,
    "checkpoint": "C:/my-game/exports/node-frame-upload.json"
  }
}
```

`pegasus_apply_ui_template` 接收 `game_id`、`section`、`template`（完整分类 JSON 对象）、`expected_sha256`、`backup_directory`。该工具标注为写操作；上传工具也不是只读操作。MCP 服务不内置逐次批准界面，授权应由调用它的用户和客户端控制。

## 剧情更新

`pegasus_apply_scene` / `apply-scene` 接收作品 ID、场景 ID、本地 UTF-8 `.lua` 文件、旧源码的 UTF-8 SHA-256，以及备份目录。示例：

```powershell
.\pegasus.cmd apply-scene 123456 987654 '.\scene.lua' --expected-sha256 $expectedSceneHash --backup-directory '.\exports\scene-backups'
```

先用 `read commands` 读取源代码并计算哈希。更新会保留旧代码和目标代码、重新检查版本、按 UTF-16 长度替换指令流，并读回核验；不会发布作品。编辑器须已打开目标作品，以便读取本地编辑会话。备份失败、版本冲突均拒绝写入；读回不一致不自动重试或回滚。与 UI 更新相同，后台没有原子 CAS，请避免同时编辑保存。

**保存成功不代表运行验证成功。** 编辑器可能继续使用内存里的旧剧情，写入后应退出作品并重新打开，再进行运行验证。工具不验证 Lua 语法或天马指令兼容性。

## MCP 接入

复制 `mcp-config.example.json` 为本地 `mcp-config.json`，将示例命令替换为实际 Python 绝对路径，再将 `mcpServers.pegasus-editor` 合并进支持该格式的 MCP 客户端配置。本地配置已加入 Git 忽略列表。其他客户端请填写：

- 命令：本项目 `.venv\Scripts\python.exe` 的绝对路径
- 参数：`-m`、`pegasus_bridge.server`
- 传输：stdio

需要在线操作时，在 MCP 客户端的服务器环境变量里配置上述会话。CLI 的 PowerShell 环境不会自动传播给已经运行的桌面客户端。升级后重启或重连 MCP 服务，工具列表应包含 11 个工具。

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

单元测试还覆盖剧情替换的 UTF-16 索引、备份、并发冲突和读回不一致。单元测试覆盖 Windows 进程枚举、脱敏、认证、读取、导出以及 UI 更新的冲突拒绝、备份失败、写后读回、无修改路径、上传凭据隔离和检查点绑定。stdio 集成测试实际启动独立 MCP 子进程，验证初始化、11 个工具发现、写工具的参数与注解、成功调用和无网络的错误调用。测试不修改云端作品。

2026-10-26 的独立授权实测通过真实 MCP stdio 会话上传 13 张图片并更新 `playback`，读回确认目标分类一致、其他 UI 分类和剧情脚本未变化、作品仍为草稿。此记录验证接口保存，不代表完成 Unity 内的视觉验收。
