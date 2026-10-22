import argparse
import json
import sys

from .core import Pegasus, BridgeError


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="PegasusEditor CLI：本地诊断与只读接口适配")
    parser.add_argument("--install-dir")
    parser.add_argument("--data-dir")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "launch", "recent-games", "routes", "mcp"):
        commands.add_parser(name)
    logs = commands.add_parser("logs")
    logs.add_argument("--lines", type=int, default=60)
    catalog = commands.add_parser("catalog")
    catalog.add_argument("game_id", type=int)
    for name in ("read", "export"):
        p = commands.add_parser(name)
        p.add_argument("resource", choices=["game", "scenes", "commands", "variables", "arrays", "ui_catalog"] + (["catalog"] if name == "export" else []))
        p.add_argument("game_id", type=int)
        p.add_argument("--scene-id", type=int)
        if name == "export":
            p.add_argument("--output", required=True)
    args = parser.parse_args()
    bridge = Pegasus(args.install_dir, args.data_dir)
    try:
        if args.command == "mcp":
            from .server import create_server
            create_server(bridge).run(transport="stdio")
            return
        if args.command == "logs":
            result = bridge.logs(args.lines)
        elif args.command == "catalog":
            result = bridge.command_catalog(args.game_id)
        elif args.command == "read":
            result = bridge.read(args.resource, args.game_id, args.scene_id)
        elif args.command == "export":
            result = bridge.export(args.resource, args.game_id, args.output, args.scene_id)
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
