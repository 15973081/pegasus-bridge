"""Real MCP -> independent CLI -> MCP preview lifecycle. No native game writes."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    with tempfile.TemporaryDirectory() as directory:
        env = dict(os.environ, PEGASUS_PREVIEW_DIR=directory)
        params = StdioServerParameters(command=sys.executable,
                                       args=['-m', 'pegasus_bridge.server'], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(name, args):
                    result = await session.call_tool('pegasus_preview_' + name, args)
                    assert not result.isError, result
                    return json.loads(result.content[0].text)

                started = await call('start', {'manifest_path': str(Path('examples/preview/preview.json').resolve())})
                ident = started['session_id']
                try:
                    cli = subprocess.run([sys.executable, '-m', 'pegasus_bridge.cli',
                                          'preview-click', ident, 'take_key', '--expected-revision', '1'],
                                         env=env, capture_output=True, encoding='utf-8', timeout=15, check=True)
                    assert json.loads(cli.stdout)['variables']['has_key'] is True
                    state = await call('state', {'session_id': ident})
                    assert state['revision'] == 2
                    state = await call('click', {'session_id': ident, 'name': 'open_door', 'expected_revision': 2})
                    assert '门打开了' in state['objects']['message']['content']
                    state = await call('reset', {'session_id': ident, 'expected_revision': 3})
                    assert state['variables']['has_key'] is False
                    assert state['revision'] == 4
                finally:
                    await call('stop', {'session_id': ident})
        print(json.dumps({'mcp_start': True, 'cli_click': True, 'mcp_state_click_reset_stop': True,
                          'native_runtime': False}))


asyncio.run(main())
