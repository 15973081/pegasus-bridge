"""Inspect literal video choices in editor-exported Lua and prepare binding edits."""
from luaparser import ast, astnodes as lua


def fields(table):
    if not isinstance(table, lua.Table):
        return {}
    return {f.key.id: f.value for f in table.fields if isinstance(f.key, lua.Name)}


def literal(node):
    if isinstance(node, lua.String):
        return node.s.decode("utf-8")
    if isinstance(node, lua.Number):
        return node.n
    return None


def command(node):
    if isinstance(node, lua.Call) and isinstance(node.func, lua.Index):
        receiver, method = node.func.value, node.func.idx
        if isinstance(receiver, lua.Name) and receiver.id == "ac" and isinstance(method, lua.Name):
            return method.id
    return None


def choices(source):
    def scan(block, inherited):
        scope = dict(inherited)
        for stmt in block.body:
            if isinstance(stmt, lua.LocalFunction):
                scope[stmt.name.id] = stmt
                yield from scan(stmt.body, scope)
            if command(stmt) != "showVideoOption" or not stmt.args:
                continue
            cfg = fields(stmt.args[0])
            items = cfg.get("option_items")
            if not isinstance(items, lua.Table):
                continue
            for index, item in enumerate(items.fields, 1):
                opt = fields(item.value)
                yield cfg, index, opt, scope
    return scan(ast.parse(source).body, {})


def inspect_options(source):
    result = []
    for cfg, index, opt, scope in choices(source):
        cb = opt.get("click_func")
        target = scope.get(cb.id) if isinstance(cb, lua.Name) else cb
        body = getattr(target, "body", None)
        steps = []
        if isinstance(body, lua.Block):
            for stmt in body.body:
                kind = command(stmt)
                if kind in ("showVideo", "showVideoOption", "createSwitchChapter") and stmt.args:
                    args = fields(stmt.args[0])
                    steps.append({"command": kind, "target": literal(args.get(
                        "chapterId" if kind == "createSwitchChapter" else "res_id"))})
                elif not isinstance(stmt, lua.LocalFunction):
                    steps.append({"command": kind or "other"})
        result.append({"node": literal(cfg.get("name")), "video": literal(cfg.get("res_id")),
                       "index": index, "text": literal(opt.get("text_content")),
                       "text_style": literal(opt.get("text_style")),
                       "option_line": opt["text_content"].first_token.line if "text_content" in opt else None,
                       "callback": getattr(cb, "id", None),
                       "binding": ("unresolved" if not isinstance(body, lua.Block)
                                   else "bound" if body.body else "empty"), "steps": steps})
    return {"options": result, "scope": "literal editor options and direct callback statements"}


def prepare_binding(source, node, index, expected_text, callback):
    matches = [(opt, scope) for cfg, i, opt, scope in choices(source)
               if literal(cfg.get("name")) == node and i == index]
    if len(matches) != 1:
        raise ValueError("Expected exactly one matching video option")
    opt, scope = matches[0]
    if literal(opt.get("text_content")) != expected_text or callback not in scope:
        raise ValueError("Option text changed or callback is outside local scope")
    old = opt.get("click_func")
    if not isinstance(old, (lua.Name, lua.AnonymousFunction)):
        raise ValueError("Expected a named or inline callback")
    start, end = old.first_token.start, old.last_token.stop + 1
    updated = source[:start] + callback + source[end:]
    report = inspect_options(updated)
    return {"source": updated, "option": next(row for row in report["options"]
            if row["node"] == node and row["index"] == index), "applied": False}
