"""Frozen multi-resource changes with reconciliation and guarded reverse plans."""
import difflib
import json
from pathlib import Path
import tempfile

from .batch import now
from .core import BridgeError, positive_id
from .scene import SceneService, source_hash
from .storytree import StoryTreeService
from .ui import UIService, SECTIONS, bounded_errors
from .validation import validate_source
from .workfiles import read_json, child, digest, new_text, atomic_json, file_lock


class ChangePlanService:
    def __init__(self, bridge):
        self.bridge = bridge
        self.scene = SceneService(bridge)
        self.ui = UIService(bridge)
        self.tree = StoryTreeService(bridge)

    def identity(self, item):
        kind = item.get('kind')
        if kind in ('scene', 'story_tree'):
            return kind, positive_id(item.get('scene_id'))
        if kind == 'ui' and item.get('section') in SECTIONS:
            return kind, item['section']
        raise BridgeError('仅支持 scene、story_tree 和五类基础 ui')

    def validate(self, gid, item, value, allow_empty=False):
        kind, key = self.identity(item)
        if kind == 'scene':
            if not isinstance(value, str) or len(value.encode('utf-8')) > 1024*1024:
                raise BridgeError('剧情必须为 1 MiB 以内字符串')
            if not value.strip() and allow_empty:
                return
            if not value.strip() or not validate_source(value)['valid']:
                raise BridgeError('剧情未通过校验')
        elif kind == 'story_tree':
            self.tree.validate(gid, key, value)
        elif not isinstance(value, dict):
            raise BridgeError('UI 分类必须为对象')

    def remote(self, gid, item):
        kind, key = self.identity(item)
        resource = {'scene': 'commands', 'story_tree': 'story_tree', 'ui': 'ui_template'}[kind]
        value = self.bridge.read(resource, gid, key if kind != 'ui' else None)['response']['data']
        if kind == 'scene':
            return value['command'] if value['command'] is not None else ''
        if kind == 'ui':
            template = value['template']
            return (json.loads(template) if isinstance(template, str) else template)[key]
        return value

    @staticmethod
    def hash(item, value):
        return source_hash(value) if item['kind'] == 'scene' else digest(value)

    def save_plan(self, gid, items, folder, reverse_of=None):
        folder = Path(folder).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=False)
        plan = dict(version=1, game_id=gid, created_at=now(), items=items, reverse_of=reverse_of)
        plan['digest'] = digest(plan)
        new_text(folder/'plan.json', json.dumps(plan, ensure_ascii=False, indent=2))
        changes = []
        for item in items:
            def render(v):
                return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=2, sort_keys=True)
            changes.append(''.join(difflib.unified_diff(
                render(item['before']).splitlines(True), render(item['target']).splitlines(True),
                fromfile=str(self.identity(item))+'/before', tofile=str(self.identity(item))+'/target')))
        new_text(folder/'changes.diff', '\n'.join(changes))
        return dict(plan_path=str(folder/'plan.json'), digest=plan['digest'], items=len(items),
                    applied=False, atomic_transaction=False)

    @bounded_errors
    def plan(self, manifest_path, output_directory):
        path = Path(manifest_path).expanduser().resolve()
        manifest = read_json(path)
        if not isinstance(manifest, dict) or set(manifest) != {'game_id', 'items'}:
            raise BridgeError('输入须包含 game_id 和 items')
        gid = positive_id(manifest['game_id'])
        rows = manifest['items']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise BridgeError('计划须包含 1–100 项')
        items, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise BridgeError('计划项必须为对象')
            identity = self.identity(row)
            field = 'section' if row['kind'] == 'ui' else 'scene_id'
            if set(row) != {'kind', field, 'path'} or identity in seen:
                raise BridgeError('计划字段无效或目标重复')
            seen.add(identity)
            file = child(path.parent, row['path'])
            if not file.is_file() or file.stat().st_size > 1024*1024:
                raise BridgeError('目标文件不存在或超过 1 MiB')
            target = file.read_text(encoding='utf-8-sig') if row['kind'] == 'scene' else read_json(file)
            item = dict(kind=row['kind'], **{field: identity[1]}, target=target)
            self.validate(gid, item, target)
            items.append(item)
        for item in items:
            item['before'] = self.remote(gid, item)
            if item['kind'] == 'ui' and set(item['before']) != set(item['target']):
                raise BridgeError('UI 分类顶层字段必须保留')
            item['before_sha256'] = self.hash(item, item['before'])
            item['target_sha256'] = self.hash(item, item['target'])
        return self.save_plan(gid, items, output_directory)

    def load(self, plan_path):
        path = Path(plan_path).expanduser().resolve()
        plan = read_json(path, 220*1024*1024)
        if not isinstance(plan, dict) or set(plan) != {'version','game_id','created_at','items','reverse_of','digest'} or plan['version'] != 1:
            raise BridgeError('项目计划格式无效')
        if digest({k:v for k,v in plan.items() if k != 'digest'}) != plan['digest']:
            raise BridgeError('项目计划校验失败')
        gid = positive_id(plan['game_id'])
        if not isinstance(plan['items'], list) or not 1 <= len(plan['items']) <= 100:
            raise BridgeError('计划项数量无效')
        seen = set()
        for item in plan['items']:
            identity = self.identity(item)
            if identity in seen:
                raise BridgeError('计划目标重复')
            seen.add(identity)
            for key in ('before','target'):
                if self.hash(item, item[key]) != item[key+'_sha256']:
                    raise BridgeError('冻结内容校验失败')
            self.validate(gid, item, item['target'], allow_empty=plan['reverse_of'] is not None)
            if item['kind'] == 'ui' and set(item['before']) != set(item['target']):
                raise BridgeError('UI 分类顶层字段必须保留')
        return path, plan

    def write(self, gid, item, backup):
        kind, key = self.identity(item)
        target, expected = item['target'], item['before_sha256']
        if kind == 'ui':
            return self.ui.apply_template(gid, key, target, expected, str(backup))
        if kind == 'story_tree':
            return self.tree.apply(gid, key, target, expected, str(backup))
        with tempfile.TemporaryDirectory(prefix='pegasus-change-') as temp:
            file = new_text(Path(temp)/'target.lua', target)
            return self.scene.apply_scene(gid, key, str(file), expected, str(backup), allow_empty=True)

    @bounded_errors
    def apply(self, plan_path):
        path, plan = self.load(plan_path)
        with file_lock(path.parent/'apply.lock'):
            journal = dict(plan_digest=plan['digest'], status='checking', items=[], updated_at=now())
            progress = path.parent/'progress.json'
            def report(status):
                journal.update(status=status, updated_at=now())
                atomic_json(progress, journal)
                return dict(**journal, complete=status == 'complete', progress_path=str(progress),
                            atomic_transaction=False)
            for item in plan['items']:
                actual = self.hash(item, self.remote(plan['game_id'], item))
                status = 'verified' if actual == item['target_sha256'] else 'pending' if actual == item['before_sha256'] else 'conflict'
                journal['items'].append(dict(identity=self.identity(item), status=status, observed_sha256=actual))
            if any(i['status'] == 'conflict' for i in journal['items']):
                return report('conflict')
            report('applying')
            for item, step in zip(plan['items'], journal['items']):
                if step['status'] == 'verified':
                    continue
                step['status'] = 'writing'
                report('applying')
                try:
                    result = self.write(plan['game_id'], item, path.parent/'backups')
                    if not result.get('verified'):
                        raise BridgeError('未确认保存')
                    step.update(status='verified', result=result)
                except Exception:
                    step.update(status='uncertain', next_action='重新执行 change-apply；先读回核对，不盲目重发')
                    return report('needs_reconcile')
                report('applying')
            return report('complete')

    @bounded_errors
    def reverse_plan(self, plan_path, output_directory):
        _, plan = self.load(plan_path)
        items = []
        for original in reversed(plan['items']):
            item = dict(original)
            item['before'], item['target'] = original['target'], original['before']
            item['before_sha256'], item['target_sha256'] = original['target_sha256'], original['before_sha256']
            # A native empty story tree cannot be restored by the verified API.
            # Refuse before making a misleading or partially usable reverse plan.
            self.validate(plan['game_id'], item, item['target'], allow_empty=True)
            items.append(item)
        return self.save_plan(plan['game_id'], items, output_directory, plan['digest'])
