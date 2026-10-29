# 操作参考

[返回项目首页](../README.md) · [新增创作工作流](workflows.md) · [安装与接入](install.md)

本文保留读取、认证、UI、剧情、章节、故事线、商城和玩家状态的详细用法。角色、音频、批量任务、恢复、测试报告与打包见创作工作流。所有相对命令路径以仓库根目录为起点，示例 ID 请替换为自己的作品 ID。

## 当前能力与验证范围

| 能力 | CLI | MCP | 验证情况 |
|---|---|---|---|
| 安装、构建信息、进程诊断 | `status` | `pegasus_status` | 本机实测 |
| 启动编辑器 | `launch` | `pegasus_launch` | 本机实测启动 |
| 无鼠标键盘打开作品 | `open-game` | `pegasus_open_game` | 原生启动器实测转发；作品加载就绪尚未确认 |
| 编辑器进程与增量加载事件 | `editor-state` | `pegasus_editor_state` | 本机进程、管道探测；游标隔离与脱敏测试 |
| 已保存作品状态汇总 | `game-state` | `pegasus_game_state` | 2026-10-28 真实 MCP 读回 9 类资源 |
| 最近上报的玩家进度 | `player-state` | `pegasus_player_state` | Lua 采集与解析测试；原生试玩上报尚待验证 |
| 背包物品及数量 | `inventory` | `pegasus_inventory` | 实际六章 Lua 测试，18 份证据；区分前情与本轮收集 |
| 本机原生存档目录 | `list-archives` | `pegasus_list_archives` | 真实 MCP 列出 4 个本地测试档 |
| 原生存档变量快照 | `read-archive` | `pegasus_read_archive` | 真实 MCP 读取 4 档；快照均为空，不等于空背包 |
| 注册原生存档字段 | `ensure-checkpoint-variable` | `pegasus_ensure_checkpoint_variable` | 普通字符串变量实际创建、读回验证；不写玩家进度 |
| 同源 Lua 浏览器预览 | `preview-start/state/click/reset/stop` | `pegasus_preview_start/state/click/reset/stop` | 实际 MCP/CLI 跨进程会话、浏览器点击及第一章调查流程通过；非 Unity 原生试玩 |
| 历史访问的作品和场景 ID | `recent-games` | `pegasus_recent_games` | 本机日志实测 |
| 已观测的后台路径 | `routes` | `pegasus_routes` | 本机日志实测 |
| 脱敏日志 | `logs` | `pegasus_logs` | 本机读取、脱敏测试 |
| 本地指令分类缓存 | `catalog` | `pegasus_command_catalog` | 本机读取、导出 |
| 作品、场景、指令、变量、数组、UI 分类读取 | `read` | `pegasus_read` | 2026-10-22 已使用当前会话在线验证全部六类资源 |
| JSON 导出 | `export` | `pegasus_export` | 本地缓存及六类在线资源已实测 |
| 基本 UI 模板读取、导出 | `read ui_template` / `export ui_template` | `pegasus_read` / `pegasus_export` | 2026-10-26 在线读回实测 |
| PNG 素材上传 | `upload-image` | `pegasus_upload_image` | 2026-10-26 通过 MCP 上传 13 张适配素材 |
| 基本 UI 分类保存 | `apply-ui` | `pegasus_apply_ui_template` | 2026-10-26 通过 MCP 更新 playback 并读回一致 |
| 剧情源码替换 | `apply-scene` | `pegasus_apply_scene` | 空场景首次写入与六章读回实测 |
| 创建或复用章节与场景 | `ensure-chapter` | `pegasus_ensure_chapter` | 2026-10-27 六章实测 |
| 原生故事线读取、导出 | `read story_tree` / `export story_tree` | `pegasus_read` / `pegasus_export` | 六个场景在线读回 |
| 原生故事线节点与连线保存 | `apply-story-tree` | `pegasus_apply_story_tree` | 2026-10-27 六章 45 节点读回，第三章实机显示确认 |
| 商城 UI 读取、导出 | `read mall_ui` / `export mall_ui` | `pegasus_read` / `pegasus_export` | 首页、三个详情页、剧情锁在线读取 |
| 商城 UI 源码编辑 | `apply-mall-ui` | `pegasus_apply_mall_ui` | 五页真实 MCP 保存并精确读回；原生显示待验收 |
| 商城配置与模板检查 | `mall-overview` | `pegasus_mall_overview` | 真实 MCP 读取五页与商品数 |
| 生成统一商城设计 | `build-mall-ui` | `pegasus_build_mall_ui` | CLI 生成五页 Lua，分类、分页、详情、已拥有及剧情锁逻辑测试 |
| 商品列表读取、导出 | `read goods` / `export goods` | `pegasus_read` / `pegasus_export` | 在线读取实测 |
| 已有商品数据编辑 | `apply-goods` | `pegasus_apply_goods` | 模拟接口测试；尚未实际修改线上商品 |

**限制：** 在线操作使用 `TOKEN` Cookie 与 CSRF 查询参数，写入成功业务码必须为 `200000`。会话过期可能返回 `502001`。0.12.0 支持作品状态、主动上报的玩家/背包观察、本地原生存档，以及图片、UI、剧情、章节、故事线、商城编辑与设计生成，并提供独立 Lua 近似预览；不支持发布、原生试玩控制或 Unity 内部对象读写。`catalog` 是指令分类缓存，不是场景剧情内容。`recent-games` 是历史记录，不是账号全部作品。响应保留后台结构，`scenes` 的 data、UI 分类中的 command 和 `ui_template` 中的 template 可能是 JSON 字符串，需要再次解析。


## 使用

在 PowerShell 中：

```powershell
git clone https://github.com/15973081/pegasus-bridge.git
cd pegasus-bridge
uv sync --locked --extra preview --extra qa
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

## 同源 Lua 浏览器预览

```powershell
uv sync --locked --extra preview --inexact
.\pegasus.cmd preview-start examples/preview/preview.json
```

打开返回的 `url`，或使用 `preview-state/click/reset/stop` 操作返回的 `session_id`。MCP 同时提供五个 `pegasus_preview_*` 工具；点击和重置需要最新 `expected_revision`。此功能不需要天马登录，不改作品或玩家存档。已有 MCP 客户端可能需要重新连接以刷新工具列表。

通过配置文件提供脚本、素材与初始变量，启动时冻结为独立会话；只监听本机、闲置两小时退出。支持的 Lua/控件范围、配置样例及限制详见 [预览文档](preview.md)。原生存档、交易、动画及 Unity 渲染不由此预览代替。

## 在线操作的认证配置

凭据仅从运行环境读取，不写入配置文件或导出文件。应使用你自己的有效会话。

- `PEGASUS_TOKEN`：门户的有效 TOKEN Cookie 值；默认发送为 `Cookie: TOKEN=...` 并用作 CSRF 值。
- `PEGASUS_TOKEN_HEADER`：可选的自定义令牌请求头名称；默认不设置。仅适用于明确需要自定义头的环境。
- `PEGASUS_COOKIE`：可选的完整 Cookie 请求头，可单独提供。
- `PEGASUS_CSRF_TOKEN`：可选的 CSRF 值。
- `PEGASUS_ALLOW_LOG_AUTH=1`：没有显式 token/cookie 时，从本地请求日志中最新的 `csrf_token` 还原 TOKEN Cookie；不使用 Unity 的 `headerToken`。库和公开示例默认关闭，需要使用自己的本机会话时再启用，日志会话仍可能过期。
- `PEGASUS_INSTALL_DIR` / `PEGASUS_DATA_DIR`：覆盖安装目录与用户数据目录。

## 不操作桌面的作品工作流

配置上述认证后，CLI / MCP 直接操作接口，不需要鼠标、键盘、截图或坐标：

```powershell
# 发送原生作品打开请求（会启动或唤起天马窗口）
.\pegasus.cmd open-game 123456 --wait-seconds 5
# 建立原生日志观察起点，之后只读取新增事件
.\pegasus.cmd editor-state > editor-state.json
.\pegasus.cmd editor-state --since-file editor-state.json
# 读取已保存数据；指定场景可同时读取剧情与故事线
.\pegasus.cmd game-state 123456 --scene-id 987654 --include-ui
```

对应 MCP：`pegasus_open_game(game_id, wait_seconds)`、`pegasus_editor_state(since)`、`pegasus_game_state(game_id, scene_id, include_ui)`。`since` 传入上次 `editor-state` 的 `observation.cursor`，或 `open-game` 的 `cursor`。CLI 的 `--since-file` 接受这两类完整 JSON 输出。下一次轮询应使用最新游标；日志被替换/截断时返回 `continuity=reset`，不回放旧成功事件。

`open-game` 先验证后台读取权限，再从本机 PlayerPrefs 读取原生服务使用的 `tianmaSdkUid`，构造固定的 `pegasus-editor://open?gameId=...&uid=...` 参数，交给已安装启动器处理单实例转发。UID 不出现在返回值中；不将另一种 `sdkUid` 或作者 ID 当作替代。首次使用仍需完成原生登录。它打开的是**编辑中的作品，不是运行试玩**。

返回值分层解释：

- `request_sent=true`：启动器已被调用；非零退出码会报错。
- `delivery=forwarded_to_editor` / `editor_started`：本次观察到启动器转发/启动日志；尚不是加载完成。没有日志时为 `unconfirmed`。
- `progress=metadata_loaded`：新日志表明作品元数据加载成功；仍不代表资源、编辑窗口和 Lua 已就绪。无新事件时为 `sent_unconfirmed`。
- `editor_ready`、`current_game_id`、`gameplay_running`：没有可靠实时查询协议，保持 `null`。不从旧日志或缓存猜测当前状态。

天马原生代码在已有作品时可能显示“当前可能有未完成编辑，是否跳转”。工具不绕过这个确认、不结束进程、不丢弃编辑；因此目前不能保证所有情况下全程无人值守切换作品。并行原生打开请求可能共用日志，请串行调用；日志事件是观察证据，没有原生请求 ID，也不是严格的请求回执。

`game-state` 返回 `source=persisted_backend`，每个资源包含 `data`、`sha256`、`read_at`，并单独列出 `errors`；任一读取失败时 `complete=false`。逐项读取非原子快照。变量和数组是**编辑定义**，不包含玩家实时值、存档进度或未保存编辑。整体资源哈希用于比对快照；现有写接口要求的单个 UI 分类、商品对象或 Lua 源码哈希仍按对应文档计算。

编辑继续使用 `apply-scene`、`ensure-chapter`、`apply-story-tree`、`apply-ui`、`apply-mall-ui`、`apply-goods`，这些功能本来就通过接口保存并读回验证。保存后台数据不保证正在打开的编辑器自动刷新，应避免两端同时保存。

## 玩家数据与背包

三种来源不能混用：

| 来源 | 可以读取 | 不能据此认定 |
|---|---|---|
| `game-state` | 编辑器后台的已保存配置和变量定义 | 玩家当前变量或持有数量 |
| `list-archives` / `read-archive` | 本机指定槽位保存时的场景、五类变量/数组快照 | 当前内存、线上玩家或脚本局部变量 |
| `player-state` / `inventory` | 已接入观察代码的作品最近一次上报 | 持续实时连接、已绑定玩家账号、任意作品自动支持 |

### 查看原生本地存档

```powershell
.\pegasus.cmd list-archives 123456
.\pegasus.cmd read-archive 123456 1
# 明确选择运行端和身份目录，不自动猜测当前用户
.\pegasus.cmd list-archives 123456 --mode Runtime --user-id 98765
```

MCP：`pegasus_list_archives(game_id, mode="Editor", user_id=None)`、`pegasus_read_archive(game_id, slot, mode="Editor", user_id=None)`。`mode` 为 `Editor` 或 `Runtime`；支持本机 v2 Local 槽位 1–50。省略 `user_id` 只读旧目录，标记 `identity_scope=legacy_unbound`；不会从多个用户目录中自动选一个。

存档读取仅返回白名单元数据、场景与记录数量、`script_start_snapshot` / `save_snapshot`。五类快照为普通变量基础值、普通变量增量、永久变量、普通数组、永久数组。`snapshot_state=missing/empty/available` 区分无快照、空快照、有内容。普通变量基础值与购买增量分开返回，不擅自相加后声称是当前值。不会执行存档脚本、跟随其中的路径/URL 或将 Clouds 缓存冒充当前云档。

### 注册调查进度字段（0.9.0）

Lua 的局部变量不会自动进入原生变量快照。需要持久化自定义调查状态时，可注册一个普通字符串变量，然后由作品脚本将有版本号的进度序列化到 `ac.var.<变量名>`。普通变量的后台 `category=2`，字符串的 `valueType=2`；不是永久变量，不产生商城权益。

```powershell
.\pegasus.cmd read variables 123456
# expectedVariablesHash = response.data 的 pegasus_bridge.ui.digest
.\pegasus.cmd ensure-checkpoint-variable 123456 investigation_checkpoint --expected-sha256 $expectedVariablesHash --backup-directory '.\exports\variable-backups'
```

MCP：`pegasus_ensure_checkpoint_variable(game_id, name, expected_sha256, backup_directory)`。名称为 1–64 位字母数字下划线，以字母开头，禁止 `system_` 前缀。先核对整个变量目录哈希、备份并复查，再创建空字符串定义；同名兼容定义仅复用，不改默认值或任何玩家值。同名不兼容定义拒绝覆盖。后台没有原子 CAS；请求结果不确定时先读回，不重复创建。

注册字段不是存档完成。作品应校验版本、节点、道具数量和选择的一致性，将状态写入变量后调用原生存档 UI，并由玩家选定槽位。当前打开的编辑器可能尚未加载新定义，须重新打开作品。原生客户端在回放末尾应用保存快照，因此脚本还需在读档关闭后重新取值；仅在脚本开头读取可能早于最终恢复。`ac.showUI` 支持具名 `close_cb` 回调，游戏也应处理由系统菜单发起的读档。旧存档没有新增字段时，不能将其当成当前调查进度。

这项工具只注册字段，没有通用游戏状态编解码器，也不保证任意 Lua 局部状态能自动恢复。真实作品中的进度编解码和交互必须另行测试；原生跨运行存读档验收仍待完成。

### 查看上报的玩家进度与物品

```powershell
.\pegasus.cmd player-state 123456
.\pegasus.cmd inventory 123456 --max-age-seconds 30
```

对应 `pegasus_player_state(game_id, max_age_seconds=30)` 和 `pegasus_inventory(game_id, max_age_seconds=30)`。返回节点、章节、进度标记、结局，或物品 `id/name/quantity/category/origin`。背包另有 `item_kinds` 和 `total_quantity`。

- `status=unavailable`、`items=null`：未收到有效上报，**不能判定背包为空**。
- `items=[]`：有效上报明确给出了空背包。
- `status=stale`：上次上报超过时限，或无法确定事件时间；保留旧值但不作为当前状态。
- `fresh=true` 只表示日志事件距今在时限内，`is_live` 始终为 `false`。没有心跳或结束事件，停止试玩后不能立即判断会话已结束。`sequence` 每次脚本初始化重置，不是跨运行的全局版本号。

观察器读取本机 `log.txt` 末尾最多 8MB，匹配固定标记 `[PegasusPlayerStateV1]`，严格校验 JSON 与物品数量，只返回白名单字段。原生日志时间没有年份，年份依据文件修改时间推断，不能用于跨年历史审计。接口不接受任意玩家 ID，不查询他人数据；本地事件的玩家身份未绑定。

其他作品需先将包内 [runtime_observer.lua](../src/pegasus_bridge/runtime_observer.lua) 嵌入场景，显式构造状态，再在状态改变后调用：

```lua
local state = {
 game_id=123456, scene_id=987654, node="ward", chapter=1, highest_chapter=1,
 flags={door_open=true}, endings={},
 inventory={{id="key",name="病房钥匙",quantity=1,category="item",origin="collected_this_run"}}
}
pcall(pegasusEmitPlayerState, state)
```

观察代码只输出传入的游戏字段，不读取登录、文件或网络，不向游戏状态写值。它不是自动扫描所有 Lua 局部变量的工具。不要传入凭据、账号资料或不需要的玩家个人信息；序列化失败不应阻断玩法。

《午夜禁区》六章已加入该观察代码，并通过 MCP 保存与读回验证。它把本轮已取得的证据映射为数量 1 的物品；`origin=chapter_recap` 表示独立进入后续章时补入的前情，`collected_this_run` 表示本轮收集。需要重新载入最新作品并试玩才可能产生新的原生日志。本次已验证 Lua 逻辑与保存读回，**尚未收到并验证原生试玩上报**。不承诺旧存档能还原脚本里未保存的 `local flags`。

调查还发现商城 `goods/paid/v1` 返回已购校验记录，`goods/increment/paid` 返回数值增量；二者都不是背包余量。本版不以商品目录、购买次数或增量冒充持有数量，也未接入线上任意玩家数据。具体调查边界见 [玩家数据调查记录](player-data-evidence.md)。

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

先用 `read commands` 读取源代码并计算哈希。更新会保留旧代码和目标代码、重新检查版本、按 UTF-16 长度替换指令流，并读回核验；不会发布作品。本机需保留此前打开作品建立的编辑会话，不要求编辑器窗口持续运行。备份失败、版本冲突均拒绝写入；读回不一致不自动重试或回滚。与 UI 更新相同，后台没有原子 CAS，请避免同时编辑保存。

**保存成功不代表运行验证成功。** 编辑器可能继续使用内存里的旧剧情，写入后应退出作品并重新打开，再进行运行验证。工具会检查 Lua 语法及已知不兼容模式，但不能保证所有天马指令的原生运行行为。

## 章节与原生故事线（0.4.0）

```powershell
.\pegasus.cmd ensure-chapter 123456 '第一章_病房' '病房苏醒' --backup-directory '.\exports\chapter-backups'
.\pegasus.cmd read story_tree 123456 --scene-id 987654
.\pegasus.cmd export story_tree 123456 --scene-id 987654 --output '.\exports\story-tree-before.json'
```

`ensure-chapter` 按名称复用章节和场景；显式传入 `--chapter-id` 与 `--scene-id` 可以重命名已存在的目标。指定场景时必须指定其所属章节。不会删除剧情；写入前备份章节结构、检查并发，写入后读回确认。名称使用中文、字母、数字或下划线，不以数字开头。创建步骤不是事务，网络失败后先读回结构再决定是否重试。

故事线是独立于 Lua 源码和 `playback` 样式的场景数据。仅更新剧情或背景，不会自动生成节点。以下是待提交文件的结构示例，素材 ID 需要替换为作品中真实素材：

```json
{
  "gameId": 123456,
  "sceneId": 987654,
  "nodes": [
    {"name": "start", "video_id": "start", "title_text": "病房苏醒", "story_res_id": "$123456789", "next_video": "clue", "title_style": "", "font_name": "", "hide": false},
    {"name": "clue", "video_id": "clue", "title_text": "检查病历", "story_res_id": "$123456790", "next_video": "", "title_style": "", "font_name": "", "hide": false}
  ]
}
```

从读取结果的 `response.data` 计算 `digest(before)`，方法与 UI 分类哈希一致；不要对修改后的对象或响应外层计算旧哈希。保存目标对象为 `story-tree.json` 后：

```powershell
.\pegasus.cmd apply-story-tree 123456 987654 --tree-file '.\story-tree.json' --expected-sha256 $expectedTreeHash --backup-directory '.\exports\story-tree-backups'
```

MCP 对应 `pegasus_ensure_chapter` 和 `pegasus_apply_story_tree`；后者直接接收 `tree` 对象，其他参数与 CLI 对应。故事线限制为 1–200 个节点，校验目标身份、节点名称与 ID 唯一性、素材 ID、连线目标及无环结构。备份包含 `before` 与 `after`，写入前再次检查并发，写入后精确读回；不支持清空故事线。后台没有原子 CAS。

原生故事线的点击、解锁和存档行为由编辑器运行时决定；保存节点不等于将自定义 Lua 分支接入精确跳转或自动同步进度。编辑模式可能只选择节点，不执行剧情跳转。第三章的显示已实测，其他章节已读回核验；不宣称完成所有节点的原生跳转验收。

## 商城 UI 与商品数据（0.5.0）

商城 UI 在 `ui_catalog` 中，是 `type=12`、`mode=8` 的分类。其 `command` 是 JSON 字符串数组，包含商城首页、商品详情、剧情锁的名称和 UUID。它们使用独立 Lua 源码，不是 `apply-ui` 的基本模板分类。

```powershell
.\pegasus.cmd read ui_catalog 123456
.\pegasus.cmd export mall_ui 123456 --catalog-id 765432 --ui-id '11111111-1111-1111-1111-111111111111' --output '.\exports\mall-before.json'
.\pegasus.cmd read goods 123456
.\pegasus.cmd export goods 123456 --output '.\exports\goods-before.json'
```

上面的分类 ID、UI UUID 和商品 ID 都需要从本作品的读取结果中取得。`mall_ui` 返回 `response.data.command` 和源码 `sha256`，可将源码保存为 UTF-8 `.lua` 文件，修改布局、素材引用或商品说明后提交：

```powershell
.\pegasus.cmd apply-mall-ui 123456 765432 '11111111-1111-1111-1111-111111111111' '.\mall.lua' --expected-sha256 $expectedMallHash --backup-directory '.\exports\mall-backups'
```

MCP 对应 `pegasus_apply_mall_ui(game_id, catalog_id, ui_id, script_path, expected_sha256, backup_directory)`。仅编辑已存在的商城 UI，校验它属于目标作品的商城分类；非空 Lua 文件不超过 1MB。先备份并重新校验归属和源码版本，再按 UTF-16 索引替换指令流，最后读回校验。空的原始 UI 只发送插入指令；同内容跳过写入。编辑器须先打开目标作品建立编辑会话。工具不验证 Lua 语法、运行兼容性或视觉效果，保存后须重新打开作品检查。

商品修改采用局部字段对象，保留类型、商品绑定、权益与子商品配置。支持：

- `name`：1–100 字符的非空名称。
- `price`：正整数，沿用后台价格单位，不自动换算人民币。
- `payType`：1 为仅购买一次，2 为可重复购买。后台仍可能基于商品类型拒绝修改。

先对 `goods` 列表中待修改的**完整单个商品对象**计算 `pegasus_bridge.ui.digest(product)`，用作旧哈希；不要对整个列表或修改后的对象计算。将修改字段保存为 `goods-changes.json`，例如 `{"name":"调查道具","price":12}`：

```powershell
.\pegasus.cmd apply-goods 123456 246810 --changes-file '.\goods-changes.json' --expected-sha256 $expectedGoodsHash --backup-directory '.\exports\goods-backups'
```

MCP 对应 `pegasus_apply_goods(game_id, goods_id, changes, expected_sha256, backup_directory)`。修改前从作品商品列表确认归属、匹配哈希并备份，再次读取检测并发，保存后核对业务字段。修改协议字段不完整或类型为 null 的商品会被拒绝，不猜测类型。没有独立商品说明字段；说明、详情排版与图片通过对应的商城 UI Lua 编辑。当前不支持创建/删除商品、修改权益/子商品、支付下单或发布作品。

两种写入都没有原子 CAS；避免编辑器同时保存。结果不确定时先读回，不自动重试或回滚。备份里的 `before` 保留原内容，可结合当前哈希显式恢复。商品编辑会改变后台数据，调用前应取得目标作品的编辑授权。

### 统一商城设计工具（0.8.0）

`mall-overview` 读取本作品所有商城页面的名称、UUID、源码哈希及后台商品数。`native_product_status` 和 `static_product_list_hint` 是源码文本线索，不能代替运行验证；商品数也不是玩家背包数量。

```powershell
.\pegasus.cmd mall-overview 123456
.\pegasus.cmd build-mall-ui 123456 765432 --bindings-file '.\bindings.json' --theme-file '.\theme.json' --output-directory '.\mall-design-v1'
```

MCP 对应 `pegasus_mall_overview(game_id)` 和 `pegasus_build_mall_ui(game_id, catalog_id, bindings, theme, output_directory)`。生成器核对五个 UI 的作品归属、旧源码哈希和商品说明的归属，只写**全新本地目录**，不保存后台、不创建商品、不发起支付。

`bindings.json` 必须包含五个不同的实际 UI UUID：

```json
{
  "home": "11111111-1111-1111-1111-111111111111",
  "detail": "22222222-2222-2222-2222-222222222222",
  "detail_repeat": "33333333-3333-3333-3333-333333333333",
  "detail_permanent": "44444444-4444-4444-4444-444444444444",
  "lock": "55555555-5555-5555-5555-555555555555"
}
```

`theme.json` 支持标题、颜色、素材和按商品 ID 配置的说明。下列素材 ID 仅为格式示例，必须替换为作品已上传的真实引用：

```json
{
  "title": "物资室",
  "subtitle": "医院物资档案",
  "currency": "金马尼",
  "font": "NotoSansSC-Regular SDF",
  "assets": {
    "background": "$1001", "vignette": "$1002",
    "paper": "$1003", "card": "$1004", "seal": "$1005",
    "button": "$1006", "hover": "$1007", "accent": "$1008"
  },
  "colors": {"title": "#E8E0D2", "muted": "#ABA5A0", "red": "#C96970", "ink": "#302A26", "inkRed": "#7B3534"},
  "descriptions": {}
}
```

布局为 1920×1080：`background/vignette/paper` 为全屏素材，`paper` 中央须留出约 x=440–1465、y=180–910 的阅读区；`card` 为列表卡片，`seal` 为小图标，`button/hover/accent` 为普通、悬停和选中按钮素材。素材格式会校验，但不会下载核验图像、字体或自动避开素材内嵌文字；须检查预览效果。生成器使用固定医院恐怖主题布局，通用商城可在生成后进一步编辑 Lua。

标题最多 24 字，其余单行文字最多 80 字；颜色为 `#RRGGBB`。说明格式为 `"实际商品ID":{"description":"140 字以内的用途说明"}`，支持换行，最多 500 项。说明显示在 UI 中，不会改写商品后台字段；价格、名称和领取方式来自原生商品数据。

生成目录包含五个 `.lua`、主题配置及 `manifest.json`。按清单中的 `ui_id`、`script_path`、`expected_sha256` 调用 `apply-mall-ui` 保存；每页独立备份和读回，**不是五页原子事务**。失败后先检查已保存页与剩余页，不盲目重试。`applied=false` 是生成时状态，最终保存结果以写工具回执为准。

首页用 `ac.getMallProductStatus()` 读取真实商品，支持分类、六项分页、刷新、空库和读取失败提示；详情以真实 goods ID 调用原生购买按钮，单次已拥有商品不再显示购买入口，可重复商品保留入口。找不到记录时不使用模板商品兜底。剧情锁沿用原生锁定、保存和返回流程。编辑器预览中的 `isAlreadyBuy` 可能恒为 false，购买后的实际解锁仍须在原生运行端确认。本版没有创建测试商品或测试支付。

## MCP 接入

复制 `mcp-config.example.json` 为本地 `mcp-config.json`，将示例命令替换为实际 Python 绝对路径，再将 `mcpServers.pegasus-editor` 合并进支持该格式的 MCP 客户端配置。本地配置已加入 Git 忽略列表。其他客户端请填写：

- 命令：本项目 `.venv\Scripts\python.exe` 的绝对路径
- 参数：`-m`、`pegasus_bridge.server`
- 传输：stdio

需要在线操作时，在 MCP 客户端的服务器环境变量里配置上述会话。CLI 的 PowerShell 环境不会自动传播给已经运行的桌面客户端。升级后重启或重连 MCP 服务，工具列表应包含 48 个工具。

直接启动服务可用 `.\pegasus-mcp.cmd` 或 `uv run pegasus-mcp`；服务等待 MCP 消息，不提供交互式提示符。服务采用 [官方 MCP Python SDK](https://py.sdk.modelcontextprotocol.io/v1/) 的 FastMCP 与 stdio 传输，依赖锁定在 1.x。
