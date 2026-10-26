"""Real stdio client/server handshake; no remote requests or app launch."""
import asyncio
import json
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "pegasus_bridge.server"])) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            assert len(names) == 10, names
            by_name = {tool.name: tool for tool in tools.tools}
            for name in ('pegasus_upload_image', 'pegasus_apply_ui_template'):
                assert by_name[name].annotations.readOnlyHint is False
                assert 'game_id' in by_name[name].inputSchema['required']
            assert by_name['pegasus_apply_ui_template'].annotations.destructiveHint is True
            result = await session.call_tool("pegasus_status", {})
            assert not result.isError, result
            payload = json.loads(result.content[0].text)
            assert "installed" in payload
            bad = await session.call_tool("pegasus_read", {"resource": "commands", "game_id": 42})
            assert bad.isError
            bad_upload = await session.call_tool('pegasus_upload_image', {
                'game_id': -1, 'image_path': 'missing.png', 'alias': 'test',
                'category_id': 2, 'checkpoint': 'unused.json'})
            assert bad_upload.isError
            bad_apply = await session.call_tool('pegasus_apply_ui_template', {
                'game_id': 42, 'section': 'playback', 'template': {},
                'expected_sha256': 'invalid', 'backup_directory': 'unused-backups'})
            assert bad_apply.isError
            print(json.dumps({"handshake": "passed", "tools": names, "status_call": "passed", "error_call": "passed"}))


asyncio.run(main())
