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
            assert len(names) == 8, names
            result = await session.call_tool("pegasus_status", {})
            assert not result.isError, result
            payload = json.loads(result.content[0].text)
            assert "installed" in payload
            bad = await session.call_tool("pegasus_read", {"resource": "commands", "game_id": 42})
            assert bad.isError
            print(json.dumps({"handshake": "passed", "tools": names, "status_call": "passed", "error_call": "passed"}))


asyncio.run(main())
