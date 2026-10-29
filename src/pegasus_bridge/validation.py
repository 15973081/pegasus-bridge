"""Static Lua checks: syntax and known Pegasus code-stream compatibility issues."""
import contextlib
import io
from pathlib import Path
import re

from .core import BridgeError
from .scene import source_hash


def literal(node):
    from luaparser import astnodes as n
    if isinstance(node, n.String):
        return node.s.decode('utf-8') if isinstance(node.s, bytes) else node.s
    return None


def validate_source(source, material_ids=None):
    from luaparser import ast, astnodes as n
    errors, warnings = [], []
    if not isinstance(source, str) or not source.strip() or len(source.encode()) > 1024*1024:
        return dict(valid=False, errors=[{'code':'source_size','message':'Lua 必须非空且不超过 1 MiB'}], warnings=[])
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            tree = ast.parse(source)
    except Exception:
        return dict(valid=False, errors=[{'code':'lua_syntax','message':'Lua 语法解析失败'}], warnings=[])
    references, names = set(), {}
    for node in ast.walk(tree):
        line = getattr(getattr(node, 'first_token', None), 'line', None)
        if isinstance(node, n.Call) and any(isinstance(a, n.AnonymousFunction) for a in node.args):
            errors.append(dict(code='anonymous_callback', line=line, message='直接传入匿名函数会触发编辑器指令列表空引用，请改为具名函数'))
        if isinstance(node, (n.AndLoOp, n.OrLoOp)) and any(isinstance(x, n.AnonymousFunction) for x in (node.left,node.right)):
            errors.append(dict(code='conditional_callback', line=line, message='条件表达式内匿名函数存在编辑器兼容风险，请使用具名函数'))
        value = literal(node)
        if isinstance(value, str) and re.fullmatch(r'\$\d+', value):
            references.add(value)
        if isinstance(node, n.Call) and isinstance(node.func, n.Index) and isinstance(node.func.value, n.Name) and node.func.value.id == 'ac':
            method = getattr(node.func.idx, 'id', '')
            if method.startswith('create') and node.args and isinstance(node.args[0], n.Table):
                for field in node.args[0].fields:
                    if getattr(field.key, 'id', None) == 'name':
                        name = literal(field.value)
                        if name:
                            if name in names:
                                warnings.append(dict(code='reused_control_name', line=line, name=name, message='控件名重复；请确认这是更新同一控件'))
                            names[name] = line
    if material_ids is not None:
        if not isinstance(material_ids, list) or not all(isinstance(x,str) and re.fullmatch(r'\$\d+',x) for x in material_ids):
            raise BridgeError('material_ids 必须为 $数字 素材 ID 列表')
        for missing in sorted(references-set(material_ids)):
            errors.append(dict(code='missing_material', material_id=missing, message='素材映射未包含此 ID'))
    warnings.append(dict(code='static_only', message='动态素材引用和原生 Unity 行为仍需运行验证；本报告不证明所有脚本兼容'))
    return dict(valid=not errors, sha256=source_hash(source), errors=errors, warnings=warnings,
                material_references=sorted(references), native_runtime_verified=False)


def validate_scene(script_path, material_ids=None):
    path = Path(script_path).expanduser().resolve()
    if path.suffix.lower() != '.lua' or not path.is_file() or path.stat().st_size > 1024*1024:
        raise BridgeError('需要 1 MiB 以内的 Lua 文件')
    return validate_source(path.read_text(encoding='utf-8-sig'), material_ids)
