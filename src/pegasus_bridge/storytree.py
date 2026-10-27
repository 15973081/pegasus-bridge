"""Native story map data, independent of Lua scene commands."""
import json
import re
import uuid
from pathlib import Path
from .core import BridgeError, positive_id
from .ui import UIService, BASE, bounded_errors, digest


class StoryTreeService(UIService):
    @staticmethod
    def validate(game_id, scene_id, tree):
        positive_id(game_id); positive_id(scene_id)
        if not isinstance(tree, dict) or tree.get('gameId') != game_id or str(tree.get('sceneId')) != str(scene_id):
            raise BridgeError('故事线必须属于指定作品和场景')
        nodes = tree.get('nodes')
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 200:
            raise BridgeError('故事线须包含 1–200 个节点；本工具不清空故事线')
        ids, names, edges = set(), set(), {}
        for node in nodes:
            if not isinstance(node, dict): raise BridgeError('节点必须为对象')
            for key in ('name','video_id','title_text','story_res_id'):
                if not isinstance(node.get(key), str) or not node[key].strip():
                    raise BridgeError('节点缺少名称、标识、标题或封面')
            identity = node['video_id']
            if identity in ids or node['name'] in names: raise BridgeError('故事线节点标识或名称重复')
            ids.add(identity); names.add(node['name'])
            if not re.fullmatch(r'\$?[1-9][0-9]*',node['story_res_id']): raise BridgeError('封面须使用素材 ID')
            if not isinstance(node.get('next_video',''),str): raise BridgeError('next_video 须为逗号分隔的节点 ID')
            edges[identity]=[v.strip() for v in node.get('next_video','').split(',') if v.strip()]
        if any(v not in ids for targets in edges.values() for v in targets): raise BridgeError('连线指向不存在的节点')
        visiting, done = set(), set()
        def walk(k):
            if k in visiting: raise BridgeError('故事线布局不接受循环连线')
            if k in done:return
            visiting.add(k)
            for v in edges[k]:walk(v)
            visiting.remove(k);done.add(k)
        for k in edges:walk(k)

    @bounded_errors
    def apply(self, game_id, scene_id, tree, expected_sha256, backup_directory):
        self.validate(game_id,scene_id,tree)
        url=f'{BASE}/game/{game_id}/scene/{scene_id}/story-tree'
        with self.client() as api:
            before=self.data(api.get(url))
            if digest(before)!=expected_sha256:raise BridgeError('故事线已变化，未写入')
            folder=Path(backup_directory).expanduser().resolve();folder.mkdir(parents=True,exist_ok=True)
            backup=folder/f'story-tree-{game_id}-{scene_id}-{uuid.uuid4().hex}.json'
            with backup.open('x',encoding='utf-8') as f:
                json.dump({'game_id':game_id,'scene_id':scene_id,'before':before,'after':tree},f,ensure_ascii=False,indent=2)
            if self.data(api.get(url))!=before:raise BridgeError('故事线并发变化，未写入')
            if before!=tree:self.data(api.put(url,json=tree))
            after=self.data(api.get(url))
            if after!=tree:raise BridgeError('故事线读回不一致，请检查备份，勿盲目重试')
            return {'game_id':game_id,'scene_id':scene_id,'verified':True,'nodes':len(tree['nodes']),'sha256':digest(after),'backup':str(backup)}
