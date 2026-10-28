"""Installation diagnostics and portable client configuration, without credentials."""
import asyncio
from importlib.metadata import distribution, version
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

from .core import BridgeError


def client_config(client='json'):
    entry = dict(command=sys.executable, args=['-m', 'pegasus_bridge.server'],
                 env={'PEGASUS_ALLOW_LOG_AUTH': '0'})
    if client == 'json':
        return {'mcpServers': {'pegasus-editor': entry}}
    if client != 'codex':
        raise BridgeError('配置格式只支持 json 或 codex')
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    return '\n'.join([
        '[mcp_servers.pegasus-editor]',
        'command = ' + quote(entry['command']),
        'args = ["-m", "pegasus_bridge.server"]',
        'startup_timeout_sec = 20',
        'tool_timeout_sec = 120',
        '[mcp_servers.pegasus-editor.env]',
        'PEGASUS_ALLOW_LOG_AUTH = "0"',
        '',
    ])


async def _probe():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    # A different cwd proves the installed entry point doesn't require a checkout.
    with tempfile.TemporaryDirectory(prefix='pegasus-mcp-check-') as cwd:
        params = StdioServerParameters(command=sys.executable,
                                       args=['-m', 'pegasus_bridge.server'], cwd=cwd)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                result = await session.list_tools()
                names = sorted(tool.name for tool in result.tools)
                required = {'pegasus_status', 'pegasus_apply_scene', 'pegasus_preview_start'}
                if not required <= set(names):
                    raise BridgeError('MCP 工具列表缺少必要接口')
                status = await session.call_tool('pegasus_status', {})
                if status.isError:
                    raise BridgeError('MCP status 调用失败')
                return dict(protocol_version=init.protocolVersion,
                            server_name=init.serverInfo.name, tool_count=len(names), tools=names,
                            initialize=True, list_tools=True, status_call=True)


def doctor_mcp():
    try:
        result = asyncio.run(asyncio.wait_for(_probe(), timeout=30))
    except BridgeError:
        raise
    except Exception:
        raise BridgeError('MCP 安装自检失败：请检查独立环境的依赖和进程权限') from None
    direct_url = json.loads(distribution('pegasus-bridge').read_text('direct_url.json') or '{}')
    editable = bool(direct_url.get('dir_info', {}).get('editable', False))
    return dict(version=version('pegasus-bridge'), python=sys.executable,
                package_directory=str(Path(__file__).resolve().parent), transport='stdio',
                preview_dependency=importlib.util.find_spec('lupa') is not None,
                editable_install=editable, source_checkout_required=editable, **result)
