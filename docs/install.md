# 安装 Pegasus MCP

本项目是标准的 MCP Server，使用 stdio 传输，提供初始化、工具发现和工具调用。CLI 与 MCP 共用底层功能，但入口不同：

- `pegasus`：面向终端用户的 CLI。
- `pegasus-mcp`：由 MCP 客户端启动的服务，默认通过 stdin/stdout 收发 MCP 消息，不输出普通欢迎文本。
- 浏览器预览的 HTTP 地址是游戏预览页面，不是 MCP 服务地址。

当前发行方式为源码安装或本地 wheel/ZIP 安装。没有声称已经发布到 PyPI、MCP Registry 或插件市场。

## Windows 安装包

先安装 uv（https://docs.astral.sh/uv/getting-started/installation/）。解压 `pegasus-mcp-0.11.0-windows.zip` 后，在该目录的 PowerShell 执行：

```powershell
.\install.ps1
```

安装器调用 `uv tool install`，在用户级独立环境中安装 wheel 和浏览器预览依赖，创建 `pegasus`、`pegasus-bridge`、`pegasus-mcp` 命令。无需管理员权限，不依赖源码仓库的 `.venv`。需要网络下载 Python（缺少时）及依赖，因此此 ZIP 不是完整离线安装包。不会自动安装 uv、修改执行策略或覆盖冲突命令。

如本机策略不允许运行脚本，可直接执行同等安装命令，而无需修改策略：

```powershell
uv tool install --python 3.12 ".\pegasus_bridge-0.11.0-py3-none-any.whl[preview]"
```

如果命令目录尚未在 PATH 中，安装器会打印完整路径。可运行 `uv tool dir --bin` 查看该目录，再从完整路径执行命令。

## 从源码安装

```powershell
git clone https://github.com/15973081/pegasus-bridge.git
cd pegasus-bridge
uv tool install --python 3.12 ".[preview]"
```

这是非 editable 安装；完成后可在任意目录运行，修改源码不会自动改变已安装版本。开发环境仍可使用 `uv sync --locked --extra preview --inexact`。

## 检查实际 MCP 协议

```powershell
pegasus-mcp --version
pegasus doctor-mcp
```

`doctor-mcp` 从独立临时目录启动当前安装的 MCP 子进程，实际执行 `initialize`、`tools/list` 和 `pegasus_status`。成功结果含版本、协议版本、安装路径与工具数；不读取凭据、不连接天马后台、不打开编辑器、不改作品。客户端已配置与客户端当前已加载是不同状态，配置成功后仍需查看客户端的 MCP 连接状态。

## 接入 Codex

安装时可显式加入配置：

```powershell
.\install.ps1 -RegisterCodex
```

或在安装后使用完整的服务路径：

```powershell
$mcpBin = Join-Path (uv tool dir --bin) 'pegasus-mcp.exe'
codex mcp add pegasus-editor --env PEGASUS_ALLOW_LOG_AUTH=0 -- $mcpBin
codex mcp get pegasus-editor --json
```

安装器只在 `pegasus-editor` 不存在时添加；已有同名配置保持原样，避免丢失自定义环境和工具限制。使用当前安装路径生成 TOML：

```powershell
pegasus mcp-config --client codex
```

该命令只打印配置，不改客户端文件。Codex 官方说明：https://learn.chatgpt.com/docs/extend/mcp?surface=cli 。需要重新连接或开始新会话，让客户端加载新增服务；不要把配置写入成功当作当前聊天已经获得工具。

## 接入接受 mcpServers JSON 的其他客户端

```powershell
pegasus mcp-config --client json
```

输出实际安装环境 Python 的绝对路径和 `-m pegasus_bridge.server` 参数，复制到客户端对应的 MCP 设置中。不同客户端的配置文件位置不同；本工具不会擅自覆盖它们。使用 stdio 类型，不要填入浏览器预览 URL。

## 天马认证

离线预览、安装自检和本地诊断不需要天马会话。在线读取或编辑作品需要有效认证。生成的公开配置默认 `PEGASUS_ALLOW_LOG_AUTH=0`，不包含 TOKEN/Cookie。

若明确使用自己这台电脑已登录的天马会话，可在本机配置中启用 `PEGASUS_ALLOW_LOG_AUTH=1`；新注册时也可用 `install.ps1 -RegisterCodex -AllowLogAuth`。不会向安装包写入会话凭据。其他认证方式见仓库 README。

## 升级与卸载

升级时安装新的 wheel；使用 `uv tool install --upgrade` 允许更新现有工具及依赖。然后重启客户端的该 MCP 连接。不要为了普通升级使用 `--force` 覆盖不相关命令。

```powershell
codex mcp remove pegasus-editor
uv tool uninstall pegasus-bridge
```

卸载不会删除天马作品、存档或本地创作素材。其他客户端的对应 MCP 配置需自行移除。

## 构建可分发安装包

```powershell
uv build
python scripts/build_install_bundle.py
```

构建器仅打包 wheel、安装器、文档、简单示例和 SHA256 校验清单。不会将编辑器二进制、作品素材、仓库 `.venv` 或本机配置带入 ZIP。
