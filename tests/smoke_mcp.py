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
            assert len(names) == 58, names
            by_name = {tool.name: tool for tool in tools.tools}
            for name in ('pegasus_change_plan', 'pegasus_change_apply', 'pegasus_change_reverse_plan',
                         'pegasus_native_qa_begin', 'pegasus_native_qa_report'):
                assert name in by_name
            assert by_name['pegasus_change_apply'].annotations.destructiveHint is True
            invalid_plan = await session.call_tool('pegasus_change_apply', {'plan_path':'missing-change-plan.json'})
            assert invalid_plan.isError
            invalid_qa = await session.call_tool('pegasus_native_qa_begin', {'game_id':-1, 'output_directory':'unused-native-qa'})
            assert invalid_qa.isError
            variant = by_name['pegasus_create_orientation_variant']
            assert variant.annotations.destructiveHint is False
            assert variant.annotations.readOnlyHint is False
            assert set(variant.inputSchema['required']) == {
                'source_game_id', 'name', 'orientation', 'expected_sha256', 'checkpoint'}
            assert variant.inputSchema['properties']['orientation']['enum'] == ['horizontal', 'vertical']
            bad_variant = await session.call_tool('pegasus_create_orientation_variant', {
                'source_game_id': -1, 'name': 'test', 'orientation': 'vertical',
                'expected_sha256': '0'*64, 'checkpoint': 'unused-variant.json'})
            assert bad_variant.isError
            assert by_name['pegasus_get_orientation'].annotations.readOnlyHint is True
            assert by_name['pegasus_set_orientation'].annotations.readOnlyHint is False
            assert by_name['pegasus_set_orientation'].annotations.destructiveHint is False
            orientation_schema = by_name['pegasus_set_orientation'].inputSchema
            assert set(orientation_schema['required']) == {'game_id', 'orientation', 'expected_sha256', 'checkpoint'}
            assert orientation_schema['properties']['orientation']['enum'] == ['horizontal', 'vertical']
            invalid_orientation = await session.call_tool('pegasus_set_orientation', {
                'game_id': 42, 'orientation': 'diagonal', 'expected_sha256': '0'*64,
                'checkpoint': 'unused-orientation.json'})
            assert invalid_orientation.isError
            assert by_name['pegasus_list_drafts'].annotations.readOnlyHint is True
            assert by_name['pegasus_list_drafts'].annotations.openWorldHint is True
            assert by_name['pegasus_save_draft'].annotations.readOnlyHint is False
            assert by_name['pegasus_save_draft'].annotations.destructiveHint is False
            assert by_name['pegasus_save_draft'].annotations.idempotentHint is False
            assert by_name['pegasus_save_draft'].annotations.openWorldHint is True
            assert set(by_name['pegasus_save_draft'].inputSchema['required']) == {'game_id', 'name', 'checkpoint'}
            assert by_name['pegasus_preview_state'].annotations.readOnlyHint is True
            assert by_name['pegasus_preview_reset'].annotations.destructiveHint is True
            for name in ('pegasus_preview_state', 'pegasus_preview_stop'):
                invalid = await session.call_tool(name, {'session_id': '../invalid'})
                assert invalid.isError
            for name in ('pegasus_upload_image', 'pegasus_apply_ui_template', 'pegasus_apply_scene', 'pegasus_ensure_chapter', 'pegasus_apply_story_tree', 'pegasus_apply_mall_ui', 'pegasus_apply_goods'):
                assert by_name[name].annotations.readOnlyHint is False
                assert 'game_id' in by_name[name].inputSchema['required']
            assert by_name['pegasus_apply_ui_template'].annotations.destructiveHint is True
            assert by_name['pegasus_mall_overview'].annotations.readOnlyHint is True
            assert by_name['pegasus_build_mall_ui'].annotations.readOnlyHint is False
            assert by_name['pegasus_build_mall_ui'].annotations.destructiveHint is False
            invalid_design = await session.call_tool('pegasus_build_mall_ui', {
                'game_id': 42, 'catalog_id': 8, 'bindings': {}, 'theme': {},
                'output_directory': 'unused-mall-design'})
            assert invalid_design.isError
            assert by_name['pegasus_ensure_checkpoint_variable'].annotations.readOnlyHint is False
            bad_checkpoint = await session.call_tool('pegasus_ensure_checkpoint_variable', {
                'game_id': 42, 'name': 'system_reserved', 'expected_sha256': '0'*64,
                'backup_directory': 'unused-checkpoint'})
            assert bad_checkpoint.isError
            result = await session.call_tool("pegasus_status", {})
            assert not result.isError, result
            payload = json.loads(result.content[0].text)
            assert "installed" in payload
            editor = await session.call_tool('pegasus_editor_state', {})
            assert not editor.isError, editor
            editor_payload = json.loads(editor.content[0].text)
            assert editor_payload['current_game_id'] is None
            assert editor_payload['observation']['events'] == []
            for name in ('pegasus_player_state', 'pegasus_inventory', 'pegasus_list_archives'):
                assert by_name[name].annotations.readOnlyHint is True
                invalid = await session.call_tool(name, {'game_id': -1})
                assert invalid.isError
            bad_open = await session.call_tool('pegasus_open_game', {'game_id': -1})
            assert bad_open.isError
            bad = await session.call_tool("pegasus_read", {"resource": "commands", "game_id": 42})
            assert bad.isError
            bad_mall = await session.call_tool('pegasus_read', {'resource': 'mall_ui', 'game_id': 42})
            assert bad_mall.isError
            bad_goods = await session.call_tool('pegasus_apply_goods', {
                'game_id': 42, 'goods_id': 9, 'changes': {'description': 'unsupported'},
                'expected_sha256': '0' * 64, 'backup_directory': 'unused-backups'})
            assert bad_goods.isError
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
