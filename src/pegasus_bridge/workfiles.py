"""Small bounded file primitives shared by authoring and deployment workflows."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import uuid

from .core import BridgeError


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_json(path, limit=8*1024*1024):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size > limit:
        raise BridgeError('JSON 文件不存在或过大')
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError):
        raise BridgeError('JSON 文件格式无效') from None


def child(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise BridgeError('工程文件必须使用目录内相对路径')
    root = Path(root).resolve()
    path = (root/name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise BridgeError('工程文件越过项目目录')
    return path


def new_text(path, text):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return path


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    try:
        new_text(temp, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def file_lock(path):
    """Never silently take over a lock left by an interrupted process."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise BridgeError('任务锁已存在；请确认旧任务已结束并核对记录后再处理锁文件') from None
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))
        yield
    finally:
        path.unlink()
