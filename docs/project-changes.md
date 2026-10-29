# 0.16 项目修改与原生验收

新增 5 个工具，共 58 个。CLI 与 MCP 共用服务。

## 项目修改计划

准备输入清单，所有 path 相对清单目录，禁止越界。scene 为 Lua，ui 和 story_tree 为 JSON：

```json
{
  "game_id": 123456,
  "items": [
    {"kind": "scene", "scene_id": 1001, "path": "chapter1.lua"},
    {"kind": "ui", "section": "cover", "path": "cover.json"},
    {"kind": "story_tree", "scene_id": 1001, "path": "tree.json"}
  ]
}
```

```powershell
pegasus change-plan changes.json exports/change-001
# 审阅 plan.json 与 changes.diff
pegasus change-apply exports/change-001/plan.json
# 失败或回包丢失：同一命令先核对实际内容再续跑
pegasus change-apply exports/change-001/plan.json
# 生成可审阅的恢复计划；这一步不写云端
pegasus change-reverse-plan exports/change-001/plan.json exports/restore-001
pegasus change-apply exports/restore-001/plan.json
```

对应 MCP：`pegasus_change_plan`、`pegasus_change_apply`、`pegasus_change_reverse_plan`。

每个计划最多 100 项，目标文件每项最多 1 MiB。冻结前后内容及哈希，后续修改源文件不改变计划。执行前检查全部远端目标；任一目标已被第三方修改，整轮不写入。逐项写入仍复用既有服务的备份、并发检查与读回。progress.json 记录 verified/pending/conflict/uncertain。

执行不是跨资源原子事务；执行中仍可能出现部分成功。遇到 uncertain 停止，续跑先读回；已达到目标则跳过。反向计划按相反顺序执行，跳过尚未修改的资源，拒绝覆盖第三方新版本。异常终止残留 apply.lock 时，需要先确认旧任务已经结束，再处理锁文件，不自动抢锁。

支持剧情、cover/configuration/record/playback/menu 五类基础 UI、故事线。不包括变量定义、素材上传、角色配置文件、商城自定义 Lua 或工程创建。UI 顶层键必须保留。故事线为空等不能由现有接口恢复的原始数据，会在生成反向计划时拒绝；不要据此认为所有修改均可恢复。

## 原生人工验收

```powershell
pegasus native-qa-begin 123456 exports/native-001
pegasus open-game 123456
# 在原生编辑器中试玩，填写 observations.json，附录屏/截图/观察记录等证据
pegasus native-qa-report exports/native-001/session.json exports/native-001/observations.json exports/native-001/report.json
```

对应 MCP：`pegasus_native_qa_begin`、`pegasus_native_qa_report`。begin 不自动打开或启动试玩；已有 open-game 工具负责发送打开请求。

观察表填写 reviewer，四项 checks 为 portrait/audio/chapter_navigation/save_load。每项包含 status（passed/failed/not_tested）、note、evidence（观察表目录内相对路径列表）。已验收项必须有说明与证据；单文件最多 32 MiB，每项最多 20 个。报告保存文件绝对路径和 SHA256，交付时请同时保留原证据文件。

`manual_passed` 仅代表观察表四项人工通过，工具不验证证据内容。`automated_gameplay_verified` 始终 false。未测试为 incomplete；任一失败为 failed；后台快照变化或读回不完整为 needs_review，单项失败仍保留在 checks 中。

会话比较的后台快照仅包括作品元数据、章节列表、变量/数组定义、商品与 UI；不包括每个场景 Lua、素材文件或编辑器未保存内容。日志仅收集已有支持的作品打开事件，不做完整运行错误扫描。此功能不是自动原生试玩、自动截图或存档真实性证明。

## 本版验证（2026-10-29）

- 单元测试：228 项，226 通过，2 项 Windows 符号链接权限跳过。
- 新增 13 项覆盖跨资源恢复、冲突、回包丢失、冻结内容和人工验收报告。
- 真实 stdio MCP 握手、58 个工具发现、正常及错误调用通过；本地工作流冒烟测试通过，生成 3 份路线快照。
- 云端写入及 Unity 原生试玩未在本轮执行；自动化测试使用模拟后台。
