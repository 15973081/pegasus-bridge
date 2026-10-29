# 后台作品列表与定位（0.17）

新增 `pegasus_list_games` 和 `pegasus_resolve_game`，共 60 个 MCP 工具。CLI 与 MCP 共用服务。

作品数据来自已验证的 `GET /avg-editor-api/game?sourceType=5&limit=…&offset=…`，
范围是当前登录账号后台可见的天马作品。无需逐个启动或打开作品，也不调用 `recent_games`。
需要有效登录凭据；若启用 `PEGASUS_ALLOW_LOG_AUTH=1`，仍可从本地日志取得登录会话，
但作品清单不来自历史日志。也可直接配置 `PEGASUS_TOKEN` / `PEGASUS_COOKIE` 等现有认证参数。

## 列表与关键词筛选

```json
{"name":"pegasus_list_games","arguments":{"page_size":100,"max_pages":10}}
```

可选参数：`query` 为名称关键词，`match_mode` 为 `contains`（默认，不区分大小写）或 `exact`（完整名称、区分大小写）。
筛选发生在读取各页之后；不会因为某页没有匹配就停止扫描，也没有假设后台支持未验证的搜索参数。

`page_size` 范围 1–100，`max_pages` 范围 1–100；默认最多读 1000 条，单次最多读 10000 条。
`offset` 默认为 0，可用返回的 `next_offset` 继续读取。分页按原始行数推进，与过滤后的数量无关。

返回字段：

| 字段 | 含义 |
|---|---|
| `games` / `count` | 当前扫描范围内匹配的作品及数量 |
| `scanned_count` / `pages` | 实际读取的原始行数及页数 |
| `total` | 后台 `totalSize`，旧版数组响应为 null |
| `complete` | 本次从 offset=0 开始，扫到末页且没有发现分页异常 |
| `reached_end` | 当前分页范围已到末页；续查到末页也不代表本次读过前面的页 |
| `next_offset` | 达到扫描上限时继续的位置；没有则为 null |
| `restart_required` | 重复页、跨页重复 ID、总数变化或分页矛盾时为 true，应从头重查 |
| `reason` | `end`、`scan_limit`、`partial_range`、`list_changed` 或 `repeated_page` |

作品仅返回 ID、名称及有限的模式、方向、状态和创建时间字段，不返回完整接口数据或作者账号信息。
方向字段 `orientation_mode` 保留后台原值；需核对画布时继续调用 `pegasus_get_orientation`。

## 名称或 ID 核对

```json
{"name":"pegasus_resolve_game","arguments":{"name":"视频上传规格测试_20261029"}}
```

`game_id` 与 `name` 必须且只能提供一个。名称默认精确匹配；`exact=false` 为包含搜索。
自动从头遍历，默认 `page_size=100`、`max_pages=100`，可降低参数控制请求数量。

| `status` | 行为 |
|---|---|
| `resolved` | 完整扫描且唯一匹配，`game` 返回该作品 |
| `ambiguous` | 完整扫描有多个匹配，返回 `candidates`；不自动选第一项 |
| `not_found` | 完整扫描无匹配；仅代表当前账号列表中未找到 |
| `incomplete` | 达到扫描上限或列表变化；即使已有一个匹配，也不会填入 `game` |

`game` 仅在 `resolved` 时非 null。同名时可用候选 ID 再核对。
扫描截断时可以提高 `max_pages` 重新定位，也可通过 `list_games` 分页继续检查；
续查返回的是剩余范围，不能单凭最后一页判断全局唯一性。

## CLI

```powershell
pegasus list-games
pegasus list-games --query "病房" --page-size 2 --max-pages 10
pegasus list-games --offset 1000 --max-pages 10
pegasus resolve-game --name "视频上传规格测试_20261029"
pegasus resolve-game --game-id 2452144
pegasus resolve-game --name "病房" --contains
```

## 错误与边界

- 缺少会话、HTTP 401 返回 `AUTH_REQUIRED`；HTTP 403 返回 `ACCESS_DENIED`。
- 未知业务错误码返回 `API_REJECTED`；保留数值码，不猜测所有业务码的含义。网络与响应格式错误分别返回 `NETWORK_ERROR`、`INVALID_RESPONSE`。
- 错误会使 MCP 调用失败，不会转换成空列表或 `not_found`。HTTP 重定向不跟随，凭据和后台错误文本不回显。
- `not_found` 不能区分无权限、其他账号、已删除或不在此列表范围；不能据此宣称作品不存在。
- 后台 offset 分页不是原子快照。同一总数下的并发变动未必能被检测；大量创作变更时建议重新核对。
- 工具只读取列表，不启动编辑器、不加载场景、不修改或发布作品。后续需要编辑器会话的操作仍按各自要求执行。
- 更新服务后需要 MCP 客户端重新连接，才能发现新增工具；旧的常驻服务不会自动加载新代码。

## 2026-10-29 验证

- 20 项新单元测试覆盖翻页、跨页同名、精确与包含搜索、截断、空列表、重复页、总数变化、会话/网络错误及脱敏；全仓共 248 项，246 项通过、2 项因 Windows 符号链接权限跳过。
- 实际启动新版 stdio MCP 服务，发现 60 个工具。`page_size=2` 连续 3 页返回当前账号 5 个作品。
- 名称 `视频上传规格测试_20261029` 和 ID `2452144` 分别调用 resolve，均返回 `resolved`。
- 包含搜索 `午夜禁区` 返回 2 个候选及 `ambiguous`；限制只读 1 页时，即使找到测试作品，也返回 `incomplete` 且 `game=null`。
- 不存在的测试名称在完整扫描后返回 `not_found`。验证期间没有调用启动、打开作品或云端写入工具。
- 本地完整只读结果位于 `artifacts/game-discovery-20261029/live-mcp.json`（不随仓库提交）。
