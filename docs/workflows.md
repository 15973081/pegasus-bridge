# 创作、验证与批量保存（0.12.0）

七类能力使用相同 Python 服务，同时开放 CLI 与 MCP。当前总计 48 个 MCP 工具。图片/配音生成仍由外部工具完成；这里接收已有文件。新增功能不会自动发布作品。

| CLI | MCP | 作用 |
|---|---|---|
| `authoring-init` / `authoring-read` | `pegasus_authoring_init` / `pegasus_authoring_read` | 创建/读取角色音频配置及哈希 |
| `character-add` / `character-place` | `pegasus_character_add` / `pegasus_character_place` | PNG 上传、角色表情注册、按节点布局 |
| `build-scene` | `pegasus_build_scene` | 在明确的节点钩子生成角色/音频 Lua |
| `validate-scene` | `pegasus_validate_scene` | 保存前语法、已知兼容性、素材检查 |
| `batch-plan` / `batch-apply` / `batch-resume` | 同名加 `pegasus_` 并将连字符改为下划线 | 冻结差异、批量保存、核对后续跑 |
| `history` / `diff` / `restore` | `pegasus_history` / `pegasus_diff` / `pegasus_restore` | 本地剧情备份历史、差异、受版本保护的恢复 |
| `upload-audio` / `audio-bind` | `pegasus_upload_audio` / `pegasus_audio_bind` | PCM WAV 上传及节点播放配置 |
| `test-route` / `preview-report` | `pegasus_test_route` / `pegasus_preview_report` | 实际 Lua 路线断言、浏览器截图和文字溢出检查 |
| `project-check` / `project-pack` | `pegasus_project_check` / `pegasus_project_pack` | 显式文件工程检查及确定性 ZIP |

所有示例 ID 都是占位值，线上调用前换成自己的作品与场景。在线操作需要有效登录和既有编辑会话，天马窗口不需要一直打开。

## 角色、表情与节点布局

```powershell
pegasus authoring-init .\cast.json --game-id 123456
pegasus authoring-read .\cast.json
```

结果返回配置的 `sha256`。此哈希用于更新本地配置，不是云端剧情哈希。每次更新后使用新返回的值。配置变更有本地备份；锁文件阻止同一配置并发操作。

`expressions.json`：

```json
{"neutral":"C:/game/assets/nurse-neutral.png","worried":"C:/game/assets/nurse-worried.png"}
```

```powershell
pegasus character-add .\cast.json nurse 许岚 --game-id 123456 --expressions-file .\expressions.json --expected-sha256 <配置哈希>
pegasus character-place .\cast.json nurse_station nurse worried --layout-file .\layout.json --expected-sha256 <新配置哈希>
```

`layout.json`：

```json
{"x":760,"y":510,"width":640,"height":960,"index":3,"inlayer":"w404_root","visible":true}
```

坐标沿用天马的左下原点。`inlayer` 必须是脚本已经建立的图层，或内置 `window`。本模块使用 `pgas_actor_`、`pgas_audio_` 名称前缀；避免手写同名控件。每节点最多四人。再次 place 同一角色可改位置、表情或设置 `visible:false`；到没有该角色的节点会自动隐藏。角色图片上传到组件分类，生成的素材 ID 保存在配置里；重试同一图片复用上传检查点。

## 显式节点钩子

支持自己可维护的规范化脚本，不自动猜测任意 Lua 的剧情结构。节点渲染函数结束前，在 `key` 仍可见的位置加入独立一行：

```lua
-- PEGASUS-NODE-HOOK
```

```powershell
pegasus build-scene .\source.lua .\cast.json .\built.lua --node-variable key
```

也可以用 `--hook-anchor` 明确提供源文件里**唯一出现**的锚点文本，模块调用插在其后。请选在原节点 UI 已创建之后、函数返回之前。工具检查插入后的 Lua，输出文件必须是新文件。从未编译的 `source.lua` 重建，不对 `built.lua` 反复插入。游戏菜单如果也需要角色退场，应经过同一节点钩子并使用没有绑定角色的节点 ID。

角色清单和脚本生成只在本地发生；保存用 `apply-scene` 或批量计划。运行中动态节点 ID、父图层是否已创建等需预览验证。不要把静态通过等同原生兼容。

## 音频

```powershell
pegasus upload-audio 123456 .\ambient.wav --alias hospital-ambient --checkpoint .\uploads\ambient.json
pegasus audio-bind .\cast.json nurse_station ambient '$12345678' --loop --volume 20 --expected-sha256 <配置哈希>
```

支持 32 MiB 以内的完整 PCM WAV，单/双声道、8–192 kHz；暂不接受 MP3 上传。检查点保存文件哈希、时长及素材 ID，不保存凭据。注册请求发出后如果结果不确定，会拒绝自动重复注册，必须先核对素材库。绑定后重新 `build-scene`。每次经过节点钩子会停止本模块旧音频，再播放新节点配置；跨节点连续 BGM 可继续由原脚本管理。

## 校验、批量计划与续跑

```powershell
pegasus validate-scene .\built.lua
```

可用 `--material-ids-file` 传入 `["$123","$456"]` 检查静态素材引用。语法错误、直接传入的匿名回调等已知崩溃写法会拒绝保存；同名控件复用是警告，因为也可能是更新操作。`apply-scene` 已内置该检查。

批量输入 `changes.json`：

```json
{"game_id":123456,"scenes":[{"scene_id":987654,"script_path":"built.lua"}]}
```

```powershell
pegasus batch-plan .\changes.json .\plans\change-001
# 查看计划目录中的 .diff、before.lua 和 target.lua
pegasus batch-apply .\plans\change-001\plan.json
pegasus batch-resume .\plans\change-001\plan.json
```

计划目录必须是新的；源码路径相对 `changes.json` 且不能逃出目录。计划冻结文件和哈希，因此随后修改工作文件不会暗中改变待部署内容。执行前核对所有场景，有任何未知版本则整批不开始写。每次写入仍复查并备份。

**后台没有跨场景事务，也没有原子 CAS。** 中途失败可能已有若干章完成。`progress.json` 记录结果；resume 先重读：已等于目标就跳过，仍等于原版本则继续，其他版本报冲突。它可处理保存成功但响应丢失的情况。`apply.lock` 不会被自动抢占；进程异常退出后，确认没有旧执行并核对记录再处理残留锁。

## 历史和恢复

```powershell
pegasus history .\plans\change-001\backups --game-id 123456
pegasus diff .\plans\change-001\backups\<备份文件>.json
pegasus restore .\plans\change-001\backups\<备份文件>.json --expected-sha256 <当前远端源码哈希> --backup-directory .\restore-backups
```

范围是本工具的**剧情备份**，不是后台完整版本历史，也不是 UI/商品回滚。恢复会检查备份原文哈希、当前远端哈希并再次备份；支持恢复原来的空场景。历史目录不递归扫描全部磁盘。

## 自动试玩和画面报告

```powershell
uv sync --extra preview --extra qa
pegasus test-route examples/workflows/preview.json examples/workflows/route.json .\qa\route-001
pegasus preview-report examples/workflows/preview.json examples/workflows/route.json .\qa\visual-001
```

画面报告需要 Playwright Chromium 或已安装的 Edge。没有两者时可运行 `python -m playwright install chromium`。路线格式：

```json
{"version":1,"initial":{"text":{"title":"调查开始"}},"steps":[{"click":"next","expect":{"text":{"title":"调查完成"},"visible":{"next":true}}}]}
```

断言支持 text（控件文本）、visible（含父图层可见性）、variables（运行变量）、audio（播放请求存在性）。按实际 Lua 回调执行，每步保留快照；首次失败停止并保留报告。`preview-report` 额外输出 PNG 和文字溢出结果。CLI 的失败报告返回非零退出码。该工作流不运行 Unity、不写原生存档、不验证实际音频时序，也不自动发现所有剧情分支；需要提供路线。

## 工程检查和打包

工程清单示例见 `examples/workflows/project-manifest.json`：场景用 `script` 字段，素材用 `$ID` 到相对路径映射，额外文件明确列于 `supporting_files`。不同于批量更新清单。

```powershell
pegasus project-check examples/workflows/project-manifest.json
pegasus project-pack examples/workflows/project-manifest.json .\exports\game.zip
```

只打包显式引用的文件；限制目录、类型、大小，拒绝越界路径和含已知凭据字段的 JSON。ZIP 含 `project.json`、`checksums.json`，相同内容产生相同 ZIP 哈希，不覆盖既有输出。素材仍绑定原作品；不声称可以把 ZIP 一键导入另一作品或迁移素材权益。
