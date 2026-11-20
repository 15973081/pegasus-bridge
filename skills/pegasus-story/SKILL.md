---
name: pegasus-story
description: 核对天马工坊作品的视频剧情和选项分支、运行 Lua 浏览器预览、为指定选项准备或保存回调接线。适用于 Pegasus Bridge MCP。
---

# 天马剧情分支

- **查走向**　用 pegasus_resolve_game 定位作品，pegasus_read 读取 scenes 和目标场景 commands，将 command 字符串传给 pegasus_inspect_video_options。用视频素材目录解释资源 ID，用场景目录解释 chapterId。区分选项视频与点击后播放的视频。empty 表示绑定的函数体为空；unresolved 和 other 需要阅读原脚本。素材存在或编号连续都不能证明可达。
- **试玩取证**　用 pegasus_preview_start 加载原始 Lua，在清单 video_assets 和 assets 中分别提供 MP4 与按钮底图。浏览器播放到选项时间后，按 pegasus_preview_state 返回的控件名和 revision 调用 pegasus_preview_click。用 pegasus_test_route 的截图配合前后状态核对。当前支持无条件选项、execute_until_end=0，章节切换记录目标 ID；跨场景需打开目标脚本。截图标明浏览器预览或原生试玩。
- **准备接线**　以原始 source、视频控件名 node、从 1 开始的 index、按钮原文 expected_text、已有局部函数名 callback 调用 pegasus_prepare_option_binding。核对返回 option 的 steps 和源码差异。此调用只返回候选文本。
- **保存修改**　用户明确要求修改作品时，将候选 source 写入本地 Lua 文件，使用 pegasus_apply_scene，传入目标场景、修改前读得的 expected_sha256 和备份目录，再读回对应场景验证。仅要求查看、截图或提出修法时，停在只读结果或候选源码。
