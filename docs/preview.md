# 同源 Lua 交互预览（0.10.0）

这是独立的本地预览服务：Python/Lupa 执行提供的 Lua 脚本，适配层收集控件和点击回调，浏览器使用 HTML/CSS 绘制。它不调用天马 Unity 窗口，也不是官方 Web 导出。

MCP 用于创建及操作会话，CLI 可继续操作 MCP 创建的同一会话。启动后台服务不会自动操作浏览器或桌面。所有状态都标有 `mode=lua_approximation`、`native_runtime=false`。

## 安装与示例

```powershell
uv sync --locked --extra preview --inexact
uv run --extra preview pegasus preview-start examples/preview/preview.json
```

pip 安装方式：`pip install "pegasus-bridge[preview]"`（本地源码可用 `pip install ".[preview]"`）。没有安装可选依赖时，其他 MCP/CLI 功能仍可使用，预览启动会给出安装提示。

启动结果包含 `session_id`、`url`、`revision`、脚本哈希及限制说明。打开返回的 URL 即可。端口由系统分配，URL 带仅供本地会话使用的随机路径；不要将它当作公开分享地址。

```powershell
pegasus preview-state <session_id>
pegasus preview-click <session_id> take_key --expected-revision 1
pegasus preview-click <session_id> open_door --expected-revision 2
pegasus preview-reset <session_id> --expected-revision 3
pegasus preview-stop <session_id>
```

对应 MCP：`pegasus_preview_start`、`pegasus_preview_state`、`pegasus_preview_click`、`pegasus_preview_reset`、`pegasus_preview_stop`。

点击使用控件 `name`，不是显示文案或屏幕坐标。先读取 state，使用其最新 `revision`；网页或其他客户端已经操作过时，过期点击会被拒绝。reset 重建启动时的初始状态并递增 revision，避免旧点击在重置后再次有效。reset/stop 会丢弃该预览进度。

## 配置格式

```json
{
  "version": 1,
  "title": "作品预览",
  "script": "scripts/chapter-1.lua",
  "width": 1920,
  "height": 1080,
  "variables": {"checkpoint": "", "has_key": false},
  "assets": {"$background": "assets/ward.png", "$hum": "assets/hum.ogg"}
}
```

脚本与素材路径相对于配置文件目录，解析后必须仍位于此目录内；父目录跳转、外部符号链接和绝对路径均不支持。需要共享素材时，把配置放在作品根目录。素材 ID 必须与 Lua 中使用的 ID 完全一致，包括 `$` 前缀。

支持 PNG/JPEG/WebP/GIF、MP3/WAV/OGG，不提供任意本地文件或外部 URL 读取。脚本最大 2 MiB，单个素材最大 32 MiB，总素材最大 128 MiB。启动时冻结脚本、初始变量与素材；reset 不会热加载改动，修改后需要重新启动会话。

## 当前适配范围

- `ac.createLayer/createImage/createText/createButton/createStyle`：记录具名控件，同名创建覆盖旧配置。
- 控件 `pos={x,y}` 为父图层内左下原点坐标；`size={w,h}`、百分比 `anchor={x,y}`、`inlayer`、`index`、`visible`、`opacity` 支持网页近似布局。
- 文字支持 `content/text_style/font_size/alignment`、样式 `color/bold`，字体采用浏览器中文字体，不复刻 Unity SDF。
- 按钮支持 `click_func`、`normal_resId/hover_resId`；无图片时使用简单文字按钮。父图层隐藏的按钮无法点击。
- `ac.createAudio/stopAudio` 收集音频请求，浏览器点击后尝试播放；不保证与天马音频时序一致，也不提供同名短音效重复触发的完整事件模拟。
- `ac.var` 使用配置提供的会话初值；`print` 保留最多 50 条日志（每条上限 4096 字符），不写天马日志。
- `ac.showUI` 只记录原生界面请求并显示提示，不执行其界面或 `close_cb`。无法验证原生存读档、商品购买、支付或解锁流程。

未列出的天马接口会报错；不支持任意作品自动兼容、原生剧情指令转换、动画系统和商城 API 模拟。当前用途是测试可适配 Lua 的剧情分支、背包逻辑、按钮回调和基础布局，原生验收仍需在天马进行。

## 运行边界

Lua 运行于独立预览进程，限制 64 MiB Lua 内存、每次操作指令量及控件数量；游戏环境不暴露 Python/OS/IO/package/debug/load。受限 `pcall` 保留普通异常处理，但不能吞掉指令超限。仍应只运行可信的本地项目，这些限制不等于完整的恶意代码隔离环境。

HTTP 仅监听 `127.0.0.1`，随机端口和路径，校验 Host/Origin，拒绝跨站调用，不允许任意资源路径。多个标签页共享同一会话进度；不同 start 创建不同会话。状态保存在内存，服务重启不恢复。关闭标签页不会停止服务，连续两小时无有效地址请求后自动退出，也可显式 stop。

预览不是系统服务。MCP 宿主若在断开时清理整棵子进程树，也会结束其启动的预览；Windows MCP SDK 的 Job Object 清理已实测如此。需要在 MCP 连接关闭后继续试玩，可从普通终端独立运行 CLI `preview-start`，再由 CLI/MCP 操作其会话。工具不会绕过宿主的进程清理策略；若终端管理器也清理全部子进程，应保持该宿主运行。

会话描述默认在 `~/.pegasus-bridge/previews`，可用 `PEGASUS_PREVIEW_DIR` 指定目录。CLI 与 MCP 必须使用同一目录及同一操作系统用户才能找到相同会话。异常终止可能留下旧描述文件；不会根据旧 PID 杀进程，state 会报告服务不可用。

## 验证

```powershell
uv run --extra preview python -m unittest discover -s tests -p test_preview.py -v
uv run --extra preview python tests/smoke_preview.py
```

前者验证真实 Lua 回调、过期版本、隐藏控件、异常后禁止继续执行、受限环境、死循环预算、路径边界和 HTTP 生命周期；后者用真实 MCP 创建服务，通过另一 CLI 进程点击，再由 MCP 读回、重置、停止。
