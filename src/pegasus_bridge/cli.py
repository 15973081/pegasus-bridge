import argparse
import json
import sys
from pathlib import Path

from .core import Pegasus, BridgeError


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="PegasusEditor CLI：作品编辑、玩家/背包观察、原生存档与诊断")
    parser.add_argument("--install-dir")
    parser.add_argument("--data-dir")
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser('preview-start', help='启动本地同源 Lua 近似预览，不运行 Unity')
    preview.add_argument('manifest_path')
    for name in ('preview-state', 'preview-click', 'preview-reset', 'preview-stop'):
        item = commands.add_parser(name, help='操作本地 Lua 预览会话，不写天马存档')
        item.add_argument('session_id')
        if name == 'preview-click':
            item.add_argument('name')
        if name in ('preview-click', 'preview-reset'):
            item.add_argument('--expected-revision', type=int, required=True)
    for name in ("status", "launch", "recent-games", "routes", "mcp", "doctor-mcp"):
        commands.add_parser(name)
    config = commands.add_parser('mcp-config', help='打印当前安装的客户端配置，不写入客户端或包含凭据')
    config.add_argument('--client', choices=['json', 'codex'], default='json')
    opening = commands.add_parser('open-game', help='通过原生协议打开作品，不操作鼠标键盘')
    opening.add_argument('game_id', type=int)
    opening.add_argument('--wait-seconds', type=int, default=3)
    editor = commands.add_parser('editor-state', help='读取进程与新的加载事件，不读取玩家实时状态')
    editor.add_argument('--since-file', help='上次 editor-state/open-game 的 JSON 输出文件')
    state = commands.add_parser('game-state', help='汇总后台已保存的作品状态')
    state.add_argument('game_id', type=int)
    state.add_argument('--scene-id', type=int)
    state.add_argument('--include-ui', action='store_true')
    overview = commands.add_parser('mall-overview', help='检查商城 UI 与商品配置')
    overview.add_argument('game_id', type=int)
    build_mall = commands.add_parser('build-mall-ui', help='生成五页原生商城设计与保存清单，不写后台')
    build_mall.add_argument('game_id', type=int)
    build_mall.add_argument('catalog_id', type=int)
    build_mall.add_argument('--bindings-file', required=True)
    build_mall.add_argument('--theme-file', required=True)
    build_mall.add_argument('--output-directory', required=True)
    for name in ('player-state', 'inventory'):
        player = commands.add_parser(name, help='读取作品主动上报的玩家状态或背包，保留新鲜度')
        player.add_argument('game_id', type=int)
        player.add_argument('--max-age-seconds', type=int, default=30)
    for name in ('list-archives', 'read-archive'):
        archive = commands.add_parser(name, help='读取本机原生存档，非当前实时状态')
        archive.add_argument('game_id', type=int)
        if name == 'read-archive':
            archive.add_argument('slot', type=int)
        archive.add_argument('--mode', choices=['Editor', 'Runtime'], default='Editor')
        archive.add_argument('--user-id', type=int)
    logs = commands.add_parser("logs")
    logs.add_argument("--lines", type=int, default=60)
    catalog = commands.add_parser("catalog")
    catalog.add_argument("game_id", type=int)
    for name in ("read", "export"):
        p = commands.add_parser(name)
        p.add_argument("resource", choices=["game", "scenes", "commands", "variables", "arrays", "ui_catalog", "ui_template", "story_tree", "mall_ui", "goods"] + (["catalog"] if name == "export" else []))
        p.add_argument("game_id", type=int)
        p.add_argument("--scene-id", type=int)
        p.add_argument("--catalog-id", type=int)
        p.add_argument("--ui-id")
        if name == "export":
            p.add_argument("--output", required=True)
    upload = commands.add_parser('upload-image')
    upload.add_argument('game_id', type=int)
    upload.add_argument('image_path')
    upload.add_argument('--alias', required=True)
    upload.add_argument('--category-id', type=int, choices=[1, 2], required=True)
    upload.add_argument('--checkpoint', required=True)
    apply = commands.add_parser('apply-ui')
    apply.add_argument('game_id', type=int)
    apply.add_argument('section', choices=['cover', 'configuration', 'record', 'playback', 'menu'])
    apply.add_argument('--template-file', required=True)
    apply.add_argument('--expected-sha256', required=True)
    apply.add_argument('--backup-directory', required=True)
    scene = commands.add_parser('apply-scene')
    scene.add_argument('game_id', type=int)
    scene.add_argument('scene_id', type=int)
    scene.add_argument('script_path')
    scene.add_argument('--expected-sha256', required=True)
    scene.add_argument('--backup-directory', required=True)
    chapter = commands.add_parser('ensure-chapter')
    chapter.add_argument('game_id',type=int)
    chapter.add_argument('chapter_name')
    chapter.add_argument('scene_name')
    chapter.add_argument('--backup-directory',required=True)
    chapter.add_argument('--chapter-id',type=int)
    chapter.add_argument('--scene-id',type=int)
    story = commands.add_parser('apply-story-tree')
    story.add_argument('game_id',type=int)
    story.add_argument('scene_id',type=int)
    story.add_argument('--tree-file',required=True)
    story.add_argument('--expected-sha256',required=True)
    story.add_argument('--backup-directory',required=True)
    mall = commands.add_parser('apply-mall-ui')
    mall.add_argument('game_id',type=int)
    mall.add_argument('catalog_id',type=int)
    mall.add_argument('ui_id')
    mall.add_argument('script_path')
    mall.add_argument('--expected-sha256',required=True)
    mall.add_argument('--backup-directory',required=True)
    goods = commands.add_parser('apply-goods')
    goods.add_argument('game_id',type=int)
    goods.add_argument('goods_id',type=int)
    goods.add_argument('--changes-file',required=True)
    goods.add_argument('--expected-sha256',required=True)
    goods.add_argument('--backup-directory',required=True)
    checkpoint = commands.add_parser('ensure-checkpoint-variable', help='注册或复用原生存档的普通字符串变量')
    checkpoint.add_argument('game_id', type=int)
    checkpoint.add_argument('name')
    checkpoint.add_argument('--expected-sha256', required=True)
    checkpoint.add_argument('--backup-directory', required=True)
    args = parser.parse_args()
    bridge = Pegasus(args.install_dir, args.data_dir)
    try:
        if args.command == "mcp":
            from .server import create_server
            create_server(bridge).run(transport="stdio")
            return
        if args.command == 'mcp-config':
            from .installation import client_config
            result = client_config(args.client)
            if isinstance(result, str):
                print(result)
                return
        elif args.command == 'doctor-mcp':
            from .installation import doctor_mcp
            result = doctor_mcp()
        elif args.command.startswith('preview-'):
            from .preview import PreviewService
            service = PreviewService()
            if args.command == 'preview-start':
                result = service.start(args.manifest_path)
            elif args.command == 'preview-click':
                result = service.click(args.session_id, args.name, args.expected_revision)
            elif args.command == 'preview-reset':
                result = service.reset(args.session_id, args.expected_revision)
            else:
                result = getattr(service, args.command.removeprefix('preview-'))(args.session_id)
        elif args.command == 'ensure-checkpoint-variable':
            from .variables import VariableService
            result = VariableService(bridge).ensure_checkpoint(args.game_id,args.name,args.expected_sha256,args.backup_directory)
        elif args.command in ('mall-overview', 'build-mall-ui'):
            from .mall_design import MallDesignService
            service = MallDesignService(bridge)
            if args.command == 'mall-overview':
                result = service.overview(args.game_id)
            else:
                bindings = json.loads(Path(args.bindings_file).read_text(encoding='utf-8-sig'))
                theme = json.loads(Path(args.theme_file).read_text(encoding='utf-8-sig'))
                result = service.build(args.game_id,args.catalog_id,bindings,theme,args.output_directory)
        elif args.command in ('player-state', 'inventory'):
            from .player import PlayerService
            service = PlayerService(bridge)
            result = getattr(service, args.command.replace('-', '_'))(args.game_id, args.max_age_seconds)
        elif args.command in ('list-archives', 'read-archive'):
            from .archives import ArchiveService
            service = ArchiveService(bridge)
            if args.command == 'list-archives':
                result = service.list_archives(args.game_id, args.mode, args.user_id)
            else:
                result = service.read_archive(args.game_id, args.slot, args.mode, args.user_id)
        elif args.command in ('open-game', 'editor-state', 'game-state'):
            from .editor import EditorService
            service = EditorService(bridge)
            if args.command == 'open-game':
                result = service.open_game(args.game_id, args.wait_seconds)
            elif args.command == 'game-state':
                result = service.game_state(args.game_id, args.scene_id, args.include_ui)
            else:
                since = None
                if args.since_file:
                    saved = json.loads(Path(args.since_file).read_text(encoding='utf-8-sig'))
                    if not isinstance(saved, dict):
                        raise BridgeError('游标文件必须是 editor-state/open-game 返回的 JSON 对象')
                    observed = saved.get('observation', saved)
                    if not isinstance(observed, dict):
                        raise BridgeError('游标文件中的 observation 格式无效')
                    since = observed.get('cursor')
                    if since is None:
                        raise BridgeError('文件中无有效 cursor；请重新运行 editor-state')
                result = service.state(since)
        elif args.command == "logs":
            result = bridge.logs(args.lines)
        elif args.command == "catalog":
            result = bridge.command_catalog(args.game_id)
        elif args.command == "read":
            result = bridge.read(args.resource, args.game_id, args.scene_id, args.catalog_id, args.ui_id)
        elif args.command == "export":
            result = bridge.export(args.resource, args.game_id, args.output, args.scene_id, args.catalog_id, args.ui_id)
        elif args.command == 'upload-image':
            from .ui import UIService
            result = UIService(bridge).upload_image(args.game_id, args.image_path, args.alias, args.category_id, args.checkpoint)
        elif args.command == 'ensure-chapter':
            from .chapters import ChapterService
            result = ChapterService(bridge).ensure(args.game_id,args.chapter_name,args.scene_name,args.backup_directory,args.chapter_id,args.scene_id)
        elif args.command == 'apply-story-tree':
            from .storytree import StoryTreeService
            tree=json.loads(Path(args.tree_file).read_text(encoding='utf-8'))
            result=StoryTreeService(bridge).apply(args.game_id,args.scene_id,tree,args.expected_sha256,args.backup_directory)
        elif args.command == 'apply-mall-ui':
            from .mall import MallUIService
            result=MallUIService(bridge).apply_ui(args.game_id,args.catalog_id,args.ui_id,args.script_path,args.expected_sha256,args.backup_directory)
        elif args.command == 'apply-goods':
            from .mall import GoodsService
            changes=json.loads(Path(args.changes_file).read_text(encoding='utf-8-sig'))
            result=GoodsService(bridge).apply(args.game_id,args.goods_id,changes,args.expected_sha256,args.backup_directory)
        elif args.command == 'apply-scene':
            from .scene import SceneService
            result = SceneService(bridge).apply_scene(args.game_id, args.scene_id, args.script_path, args.expected_sha256, args.backup_directory)
        elif args.command == 'apply-ui':
            from .ui import UIService
            template = json.loads(Path(args.template_file).read_text(encoding='utf-8'))
            result = UIService(bridge).apply_template(args.game_id, args.section, template, args.expected_sha256, args.backup_directory)
        else:
            result = getattr(bridge, args.command.replace("-", "_"))()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (BridgeError, OSError, ValueError) as exc:
        # Never dump raw HTTP exceptions (may contain credential-bearing URLs).
        message = str(exc) if isinstance(exc, BridgeError) else f"本地操作失败：{type(exc).__name__}"
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
