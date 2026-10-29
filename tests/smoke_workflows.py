"""Exercise new local workflows over real stdio MCP, without cloud writes."""
import asyncio,json,sys,tempfile
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='pegasus-workflow-smoke-') as tmp:
        folder=Path(tmp)
        async with stdio_client(StdioServerParameters(command=sys.executable,args=['-m','pegasus_bridge.server'])) as (r,w):
            async with ClientSession(r,w) as s:
                await s.initialize();tools=await s.list_tools();assert len(tools.tools)==60
                async def call(name,**args):
                    result=await s.call_tool('pegasus_'+name,args)
                    assert not result.isError,result.content
                    return json.loads(result.content[0].text)
                cast=str(folder/'cast.json')
                init=await call('authoring_init',manifest_path=cast,game_id=42)
                read=await call('authoring_read',manifest_path=cast);assert read['sha256']==init['sha256']
                check=await call('validate_scene',script_path=str(root/'examples/workflows/source.lua'));assert check['valid']
                built=await call('build_scene',script_path=str(root/'examples/workflows/source.lua'),manifest_path=cast,output_path=str(folder/'built.lua'))
                assert built['validation']['valid']
                route=await call('test_route',manifest_path=str(root/'examples/workflows/preview.json'),route_path=str(root/'examples/workflows/route.json'),output_directory=str(folder/'route'))
                assert route['passed']
                project=await call('project_check',manifest_path=str(root/'examples/workflows/project-manifest.json'));assert project['valid']
                packed=await call('project_pack',manifest_path=str(root/'examples/workflows/project-manifest.json'),output_path=str(folder/'game.zip'));assert packed['verified']
                history=await call('history',backup_directory=str(folder));assert history['backups']==[]
                print(json.dumps(dict(tool_count=60,local_mcp_workflows=True,cloud_writes=False,route_snapshots=route['snapshots']),indent=2))

if __name__=='__main__':asyncio.run(main())
