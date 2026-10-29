# 横竖版工程：0.15.0

已有作品原地切换方向尚无已验证可用接口。0.15.0 支持读取方向、创建独立方向版本，
并保留旧检查点核验功能。创建的是空白模板工程，**不表示素材、剧情或 UI 已迁移**。

## 方向属性

| 属性 | 横版 | 竖版 |
|---|---|---|
| `orientationMode` | 1 | 2 |
| `mode`（编辑器类型） | 8 | 8 |
| 创建所用 `templateId` | 28 | 32 |
| 客户端逻辑画布 | 1920 × 1080 | 1080 × 1920 |

画布尺寸来自客户端配置逻辑，不是上传视频的分辨率，也不同于门户中显示的
1280 × 720 / 720 × 1280 选项文案。不要用 `mode=2` 表示竖屏。

## 读取与创建

1. `pegasus_get_orientation(game_id)` 读取作品和编辑器的方向、并发哈希。
   `profile` 返回逻辑尺寸和该方向创建时的模板；它不是对现有 UI 模板内容的校验。
   两处方向不一致或非天马编辑器时 `profile=null`。
2. `pegasus_create_orientation_variant` 创建独立工程。例如：

```json
{
  "source_game_id": 2445700,
  "name": "午夜禁区：404号病房（新竖版）",
  "orientation": "vertical",
  "expected_sha256": "<get_orientation 返回的 sha256>",
  "checkpoint": "C:/project/skill/天马工坊/pegasus-bridge/exports/new-portrait.json"
}
```

参数 `orientation` 仅接受 `horizontal` / `vertical`。来源须为未发布/被退回的
天马作品，且两处方向一致。新名称须为 1–30 个字符，不能与来源或已有作品重名。
新作继承语言、副标题、原创类型、工作室等基本信息；不继承比赛报名，`matchId=-1`。

返回属性：

- `game_id`：已确认的新作品 ID。无 ID 时不能认为创建失败后可直接重试。
- `verified`：新作品与编辑器方向一致，`mode=8`、`adaptMode=1`，名称、工作室、
  作者与预期一致。仅验证这些后台属性，不代表原生运行、实际模板内容或发布已验证。
- `profile`：请求方向的逻辑尺寸及创建模板，未核验成功时不能当作实际画布。
- `source_modified=false`：工具不写源作品。
- `content_copied=false` / `layout_migrated=false`：内容未复制或适配。
- `migration.status=not_started` 和 `pending`：列出素材、变量、章节、剧情、故事线、
  自定义 UI 和布局验证等后续工作，不把创建成功等同于完整转换。

## 检查点与恢复

同一次操作必须始终复用同一检查点和参数。检查点绑定源哈希、名称和方向。

- 创建请求发出前记录 `create_pending`；响应丢失时不重发创建。
- 创建返回的新 ID 已记录、但尚未发送初始化时，可以继续一次初始化。
- 初始化发出前记录 `init_pending`；响应丢失后只读回，不重复初始化。
- 已完成的检查点重复调用只重新核验；目标被修改后不会自动覆盖。
- 创建响应丢失时可返回同名候选 ID，但不据此自动接管，避免误改其他作品。
- 不确定时不要换检查点重试。需要核对后台作品列表和检查点后处理。
- 检查点锁不能替代后台事务；不同检查点的并发操作不具备服务器原子去重保证。

## 旧接口兼容

`pegasus_set_orientation(game_id, orientation, expected_sha256, checkpoint)` 保留参数。
新请求要求不同方向时返回 `status=unsupported`、`verified=false`、`remote_write=false`，
并建议创建独立方向版本；不再发送方向 PUT。
请求方向已经一致时可核验成功；旧版检查点仍可读回核对，但不会重发修改。

`pegasus_get_orientation.supported` 现为 false，表示原地切换不受支持。
调用方应分别读取 `in_place_update_supported` 和 `create_variant_supported`，
不能把来源类型合规误解为后台允许原地切换。

## 2026-10-29 实测依据

- 最新前端只在无 gameId（新建作品）时显示方向选项。
- 完整作品信息 PUT 返回成功，但作品与编辑器的方向仍为 1。
- 已有配置重新初始化返回 507002；旧 `/edit/mode` 写入出现错误响应后仍改变
  编辑器类型的副作用，不作为方向修改接口使用。原作已通过原生备份恢复为 mode=8。
- 独立测试中跨作品恢复竖版备份返回 509003，来源备份仍存在。
- 原生导入额外传入 `orientationMode=2` 后，新作仍继承来源的横屏。
- 客户端内存 `SetConfig` 会被重新加载的后台作品方向覆盖，不能代替后台转换。
- 新建竖版工程的 mode=8、orientationMode=2 已实测；上述两个临时测试作品已清理。

本地试验记录：`artifacts/orientation-investigation/`。自动化测试覆盖响应丢失、
拒绝请求、错误方向/编辑器类型、源数据并发变化、同名保护和检查点恢复。
原生客户端的完整内容运行和发布效果仍需单独验证。

0.15.0 安装后真实 MCP 测试：新建临时竖版工程 2452106，两处方向均为 2、mode=8，
复用检查点未重复创建；原作前后配置哈希一致。临时工程已删除，测试记录保存在
`artifacts/orientation-investigation/smoke-015-report.json`。
