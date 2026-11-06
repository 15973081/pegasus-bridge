# Pegasus Bridge · 天马工坊编辑器第三方工具

**通过 CLI 和 MCP 读取、编辑、预览和检查天马作品，让重复的创作操作可以由脚本或 AI 助手完成。**

Pegasus Bridge 是面向 **Windows 天马工坊编辑器（PegasusEditor）** 的第三方适配工具，非官方产品。它提供终端命令 `pegasus` 和标准 stdio MCP 服务 `pegasus-mcp`，两种入口共用同一套功能，不修改编辑器安装文件。

**主分支版本 · 0.17.1+local.26 · Windows · Python 3.12+ · 68 个 MCP 工具**

[快速开始](#快速开始) · [功能一览](#功能一览) · [案例与截图](#案例与截图) · [测试结果](#测试结果) · [完整文档](#完整文档)

## 功能一览

| 你要做的事 | 工具能完成什么 | 主要 CLI 命令 |
|---|---|---|
| 查找和核对作品 | 分页遍历后台作品，按名称或 ID 匹配，返回同名候选与扫描完整性 | `list-games`、`resolve-game` |
| 查看和导出作品 | 读取作品、场景、剧情、变量、UI、故事线及商品配置 | `game-state`、`read`、`export` |
| 保存命名草稿 | 调用编辑器“保存”使用的备份接口，创建新草稿并读回列表核对 | `save-draft`、`list-drafts` |
| 修改剧情和章节 | 保存 Lua 源码，创建或复用章节/场景，更新故事线节点与连线 | `apply-scene`、`ensure-chapter`、`apply-story-tree` |
| 添加角色立绘 | 上传 PNG，登记角色及表情，配置节点中的位置、大小和显隐，生成 Lua | `character-add`、`character-place`、`build-scene` |
| 添加音频 | 上传 PCM WAV 或 MP3，配置节点音频、音量和循环，生成播放代码 | `upload-audio`、`audio-bind` |
| 修改界面与商城 | 上传图片，更新基本 UI 和商城页面，生成商城布局，编辑已有商品数据 | `upload-image`、`apply-ui`、`build-mall-ui`、`apply-mall-ui`、`apply-goods` |
| 保存前检查 | 检查 Lua 语法、已知不兼容写法及指定素材清单中的引用 | `validate-scene`；`apply-scene` 内置检查 |
| 批量修改多个场景 | 生成差异计划、核对远端版本、逐场景备份保存、失败后核对续跑 | `batch-plan`、`batch-apply`、`batch-resume` |
| 查看和恢复修改 | 查看本工具的剧情备份、比较差异、检查当前版本后恢复 | `history`、`diff`、`restore` |
| 试玩与画面检查 | 在浏览器预览 Lua，执行指定点击路线，输出断言、截图和文字溢出报告 | `preview-start`、`test-route`、`preview-report` |
| 整理工程交付 | 检查清单中的脚本与素材，生成带 SHA256 清单的 ZIP | `project-check`、`project-pack` |
| 查看编辑器和玩家数据 | 诊断进程与日志、发起打开作品请求、读取本机存档和已接入的玩家状态上报 | `status`、`open-game`、`read-archive`、`player-state`、`inventory` |

以上业务功能也通过 MCP 提供。例如，CLI 的 `character-place` 对应 `pegasus_character_place`，`preview-report` 对应 `pegasus_preview_report`。完整参数和清单格式见 [创作工作流](docs/workflows.md)，其他读写操作见 [操作参考](docs/reference.md)。

### 素材迁移与播放诊断

| MCP 工具 | 用途 |
|---|---|
| `pegasus_upload_image` | 校验 PNG 并导入背景或组件图片，使用检查点记录登记结果 |
| `pegasus_upload_video` | 验证 H.264 MP4、生成封面并登记素材。需要本机 `ffmpeg` 和 `ffprobe`，通过检查点核对已登记结果 |
| `pegasus_image_catalog`、`pegasus_video_catalog` | 查找图片分类，分页核对素材名称和 ID |
| `pegasus_video_upload_status` | 检查上传入口或对已知素材 URL 发起 Range 请求，核对文件实际可读性 |
| `pegasus_cache_materials` | 核对素材目录中的 ID、URL、长度及本地文件哈希，备份冲突后写入本机编辑器缓存 |
| `pegasus_native_config_status` | 检查主配置和场景配置的后台状态，核对素材 ID 是否进入编译资源表 |

图片、音频和视频使用原生素材上传入口，门户编辑使用独立会话。图片和视频登记支持检查点，响应不明确时先核对目录再续跑。剧情保存前可备份和核对源码 SHA-256，保存后可读回源码与依赖。连接诊断会报告 MCP 与编辑器的 Windows 权限级别差异。

### Lua 浏览器预览

- 运行本地 Lua 脚本与计时器，流式播放 MP4，检查播放、暂停、续播和视频结束后的剧情切换。
- 显示图片、文字、带底图的按钮和固定内容的竖向滚动容器。可用真实鼠标与键盘操作，测试焦点、滚动和点击后的状态。
- 可显式启用 `lazy_assets`，按需读取图片和音频。浏览器预览与天马原生试玩分别验收。 [预览配置与支持范围](docs/preview.md)

## 快速开始

### 1. 安装并检查

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Git，在 PowerShell 中执行：

```powershell
git clone https://github.com/15973081/pegasus-bridge.git
cd pegasus-bridge
uv tool install --python 3.12 ".[preview,qa]"
pegasus doctor-mcp
```

安装到独立用户环境，无需管理员权限。`doctor-mcp` 会实际启动 MCP 子进程，检查初始化、工具发现和状态调用。当前主分支应返回版本 `0.17.1+local.26` 和 `tool_count: 68`。如果命令未加入 PATH，运行 `uv tool dir --bin` 查看命令目录。

也支持本地 wheel / Windows ZIP 安装，详见 [安装与升级](docs/install.md)。目前不通过 PyPI 或插件市场分发。

### 2. 先运行一个本地示例

在刚克隆的仓库目录执行，无需登录天马：

```powershell
pegasus preview-start examples/workflows/preview.json
```

打开返回的 `url`，点击“继续 / 返回”，可在“调查开始”和“调查完成”之间切换。返回结果也包含 `session_id`，可供 `preview-state`、`preview-click`、`preview-reset` 和 `preview-stop` 操作。

### 3. 接入 MCP 客户端

```powershell
pegasus mcp-config --client json
```

将输出的配置加入支持 `mcpServers` JSON 的客户端。服务使用 **stdio**，由客户端启动；浏览器预览的 HTTP 地址不能作为 MCP 服务地址。其他配置格式与接入步骤见 [MCP 安装文档](docs/install.md)。升级后重新连接该服务，客户端才会加载新工具。

接入并配置天马会话后，可以让 AI 助手执行这样的任务：

> 读取我的作品章节，把准备好的角色 PNG 登记为角色表情，在指定节点配置立绘；先生成 Lua 并检查，再生成修改计划给我查看。

### 4. 连接自己的天马作品

在线读取和保存需要有效登录。客户端协作 SID 优先取自编辑器缓存；未生成时由桥接器独立持久化。先在天马客户端登录，再为 CLI 或 MCP 服务配置认证。

```powershell
# 显式允许 CLI 使用本机天马日志中已有的登录会话
$env:PEGASUS_ALLOW_LOG_AUTH = '1'
pegasus recent-games
# 将 123456 替换为自己的作品 ID
pegasus game-state 123456
```

MCP 服务的环境变量要在客户端中单独配置。默认不会从日志自动提取认证。窗口关闭后，只要认证和所需编辑会话仍有效，接口读写可以继续；这不代表能在关闭窗口时执行原生试玩。详细认证方式见 [操作参考](docs/reference.md#在线操作的认证配置)。

## 原生保存与命名草稿

可以像编辑器的“保存”窗口一样，为后台当前内容创建命名备份，再从编辑器“打开”列表找到它：

```powershell
pegasus list-drafts 123456
pegasus save-draft 123456 "第一章_立绘完成" --checkpoint .\exports\draft-001.json
```

已通过真实 MCP 创建并核对《404号病房》的草稿 **`404病房_角色与调查版_MCP_20261029`**（备份 ID `672294`）。重复调用复用原结果，六章剧情源码保持一致。它保存后台已收到的内容，不强制提交编辑器内存中尚未同步的修改。用法、验证证据与限制见 [原生命名草稿](docs/native-drafts.md)。

## 案例与截图

### 《飞越13号房》迁移到天马

从 Steam 安装版整理剧情、视频和素材引用，用 MCP 接入天马作品《蹲下飞跃14号房子》。先用序章和第一章验证上传、登记和原生播放，再在原天马项目中修复素材引用和开场流程。迁移优先采用天马原生的视频、图片、文字、按钮与剧情切换功能。

| 验证环节 | 实际结果 |
|---|---|
| 素材恢复 | 重新登记 92 段曾被文件服务拒绝的视频，逐条确认新素材可读取，并核对本机编辑器缓存 |
| 剧情保存 | 11 章 Lua 已保存到目标作品，逐章读回核对源码、编译结果和素材依赖 |
| 天马原生试玩 | 两章验证工程成功播放开场。原天马项目随后确认播放了前两段，并在第三段短片按剧情暂停等待菜单 |
| 浏览器回归 | 修正深色画面上的菜单可读性后，用已保存的 Lua 从空进度播放开场，实际点击「继续剧情」，确认进入下一段视频 |

![天马原生试玩中播放《飞越13号房》开场视频](docs/cases/breakout/native-opening.png)

*天马编辑器原生试玩。画面取自用于验证素材导入的序章和第一章工程。*

![本地 Lua 浏览器预览中显示「继续剧情」和「从序章重新开始」](docs/cases/breakout/browser-menu.png)

*原天马项目修复版的本地 Lua 浏览器预览。两段开场视频自然播完后，菜单显示白色文字和按钮底图。*

原生试玩目前验证到开场，修复后的菜单及后续章节仍待原生验证。完整 UI、QTE 与小游戏属于后续迁移范围。截图用于说明测试结果，原始素材与剧情脚本保存在各自的工程中。

### 《午夜禁区：404号病房》：角色与调查流程

这个已有游戏用于验证真实脚本的预览、角色显隐和多章节批量核对。本次自动路线为：**进入病房 → 检查腕带 → 返回调查 → 打开背包**。

![《午夜禁区：404号病房》第一章，角色立绘、调查热点和选项同屏显示](docs/cases/ward404/screen-000.png)

*本地 Lua 浏览器预览中的病房调查场景。*

| 检查点 | 实际结果 |
|---|---|
| 进入病房 | 角色立绘可见，姓名显示“你” |
| 检查腕带 | 页面标题切换为“没有姓名的腕带” |
| 返回调查 | 角色姓名更新为“林默” |
| 打开背包 | 角色立绘隐藏，切换为物品页面 |
| 路线与布局 | 4 份状态快照、4 张截图；断言通过，未检出文字溢出 |
| 六章批量核对 | 真实 MCP 完成计划、执行和续跑核对；目标与云端相同，云端写入 0 次 |

![打开背包后角色隐藏，页面显示随身物品与调查操作](docs/cases/ward404/screen-003.png)

*同一脚本路线中的背包页面。画面中出现“保存/读取存档”按钮，不表示本次已验收原生存读档功能。*

完整截图、路线 JSON、检查报告及验证边界见 [案例与测试记录](docs/cases.md)。仓库提供案例截图和测试证据，不包含该游戏的完整剧本及原始素材包；可直接复现的最小示例位于 [`examples/workflows`](examples/workflows)。

## 测试结果

**0.17.1+local.26。** 398 项自动化测试中 397 项通过，1 项跳过。实际 stdio 会话核验 68 个 MCP 工具。浏览器回归覆盖视频续播、真实鼠标和键盘输入，以及带底图的按钮文字与点击。

**0.17.0，本机验证日期：2026-10-29。** 共 248 项单元测试，246 项通过、2 项因 Windows 符号链接权限跳过。真实 stdio MCP 发现 60 个工具；以每页 2 个作品遍历 3 页，核对当前账号 5 个作品，名称和 ID 均定位到测试作品 2452144。多候选、截断和无匹配返回状态均通过实测。详见 [后台作品查找](docs/game-discovery.md)。

**0.16.0，本机验证日期：2026-10-29。** 单元测试共 228 项，226 项通过，2 项因 Windows 符号链接权限跳过。新增跨资源计划、反向恢复与原生人工验收报告；本轮未进行云端写入或 Unity 原生试玩。详见 [项目修改与原生验收](docs/project-changes.md)。下面保留历史版本验收记录。

**0.13.0，本机验证日期：2026-10-29。**

| 验证项目 | 结果 |
|---|---|
| 自动化测试 | 共 188 项：186 项通过，2 项因 Windows 符号链接权限跳过 |
| MCP 协议测试 | 实际启动 stdio 服务，发现 50 个工具，正常调用和错误调用检查通过 |
| 新工作流 MCP 测试 | 配置读写、Lua 检查/生成、路线测试、工程检查/打包通过；无云端写入 |
| 仓库内最小示例 | “开始 → 完成 → 开始”3 份快照，截图与布局检查通过 |
| 原生命名草稿 | 真实 MCP 新建备份并读回，重复调用复用；六章源码一致 |
| 真实游戏案例 | 上述 4 状态路线通过，6 章同内容批量核对通过 |

复现测试与画面报告：

```powershell
uv sync --locked --extra preview --extra qa
$env:B13_DOM_SMOKE = '1'
uv run python -m unittest discover -s tests
uv run python tests/smoke_mcp.py
uv run python tests/smoke_workflows.py
uv run pegasus preview-report examples/workflows/preview.json examples/workflows/route.json artifacts/my-first-report
```

画面报告使用 Playwright Chromium 或本机 Edge；若两者都不可用，执行 `uv run python -m playwright install chromium`。输出目录必须是新目录，重复运行请换名字。当前草稿功能证据见 [原生命名草稿](docs/native-drafts.md)，0.12.0 画面案例与历史测试输出见 [测试证据](docs/cases.md#验证记录)。

## 使用边界

- **第三方适配。** 依赖已观测的天马接口和本地数据格式，上游更新可能影响兼容性；不包含编辑器本体或登录凭据。
- **预览范围。** Lua 浏览器预览用于逻辑与布局检查，不能代替 Unity 原生试玩、动画、实际音频时序、支付或原生存读档验收。
- **保存范围。** 写入前检查和备份、写后读回核对；后台没有跨场景事务或原子版本锁，仍应避免编辑器与工具同时保存同一内容。
- **未实现或未完成实机验证。** 不支持发布作品、控制原生试玩或写云存档；已有商品修改仅做模拟接口测试，新增音频上传尚未完成本轮真实上传验收。玩家状态读取需要作品主动接入上报。
- **交付范围。** 工程 ZIP 用于整理与校验文件，不提供跨作品一键导入，也不迁移素材权益。

## 完整文档

| 文档 | 内容 |
|---|---|
| [安装与接入](docs/install.md) | 源码、Windows ZIP、MCP 配置、自检与升级 |
| [后台作品查找](docs/game-discovery.md) | 独立列举与搜索、分页续查、同名候选及登录错误 |
| [原生命名草稿](docs/native-drafts.md) | 编辑器保存窗口对应的命名备份，创建与列举 |
| [创作工作流](docs/workflows.md) | 角色、音频、Lua 检查、批量任务、恢复与工程打包 |
| [浏览器预览](docs/preview.md) | 预览清单、交互接口、支持范围和会话管理 |
| [操作参考](docs/reference.md) | 认证、作品读写、UI、章节、故事线、商城、存档与观察数据 |
| [案例与测试记录](docs/cases.md) | 真实截图、复现命令、JSON 报告和测试输出 |
| [历史验证记录](docs/validation-history.md) | 各版本的测试与原生接口验证记录 |

## 横竖版工程（0.15）

`pegasus_get_orientation` 返回两处后台方向、逻辑画布尺寸和创建能力。
新增 `pegasus_create_orientation_variant`，按源作品基本信息创建横版或竖版空白工程，
固定使用天马 `mode=8`，保存检查点并核验初始化结果；原作不变，内容迁移单独报告为未开始。
旧 `pegasus_set_orientation` 对方向变更返回 `unsupported`，停止发送已实测无效的修改请求；
旧检查点仍可读回核对。新建竖版不等于既有作品已转换或 UI 已适配。
参数与验证边界见 [横竖版工程](docs/orientation.md)。

## 项目修改与原生验收（0.16）

新增 `change-plan`、`change-apply`、`change-reverse-plan`，把剧情、基础 UI 和故事线纳入统一的冻结计划、冲突预检、备份保存及反向恢复。另提供 `native-qa-begin`、`native-qa-report`，记录原生人工试玩观察与证据哈希；不自动启动试玩，不把进程状态当作验收通过。使用清单与限制见 [项目修改与原生验收](docs/project-changes.md)。
