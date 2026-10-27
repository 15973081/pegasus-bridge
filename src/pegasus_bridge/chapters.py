"""Non-deleting chapter/scene creation and explicit target renaming."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
import re
from .core import BridgeError, positive_id
from .ui import UIService, BASE, bounded_errors


class ChapterService(UIService):
    def tree(self, api, game_id):
        value = self.data(api.get(f'{BASE}/game/{game_id}/chapter/scene'))
        return json.loads(value) if isinstance(value, str) else value

    @bounded_errors
    def ensure(self, game_id, chapter_name, scene_name, backup_directory, chapter_id=None, scene_id=None):
        game_id = positive_id(game_id)
        if not all(isinstance(s,str) and 0 < len(s.strip()) <= 60 for s in [chapter_name,scene_name]):
            raise BridgeError('章节和场景名称须为 1–60 字符')
        if any(s[0].isdigit() or not re.fullmatch(r'[A-Za-z0-9_\u3000-\u30ff\u3400-\u9fff\uac00-\ud7af\u0e00-\u0e7f]+',s) for s in [chapter_name,scene_name]):
            raise BridgeError('天马名称不能以数字开头，且不能包含空格或标点；请使用中文、字母、数字或下划线')
        if scene_id is not None and chapter_id is None:
            raise BridgeError('指定场景 ID 时必须指定所属章节 ID')
        if chapter_id is not None: positive_id(chapter_id)
        if scene_id is not None: positive_id(scene_id)
        with self.client() as api:
            before = self.tree(api,game_id)
            def flatten(rows):
                return [c for row in rows for c in [row,*flatten(row.get('chapters',[]))]]
            chapters = flatten(before)
            matches = [c for c in chapters if c['id']==chapter_id] if chapter_id else [c for c in chapters if c['chapterName']==chapter_name]
            if len(matches)>1 or (chapter_id and not matches):
                raise BridgeError('章节无法唯一匹配，未修改')
            chapter = matches[0] if matches else None
            if chapter and any(c['id']!=chapter['id'] and c['chapterName']==chapter_name for c in chapters):
                raise BridgeError('同名章节已存在，未修改')
            scenes = chapter.get('scenes',[]) if chapter else []
            found = [s for s in scenes if s['id']==scene_id] if scene_id else [s for s in scenes if s['sceneName']==scene_name]
            if len(found)>1 or (scene_id and not found):
                raise BridgeError('场景不属于指定章节或无法唯一匹配，未修改')
            scene = found[0] if found else None
            if scene and any(s['id']!=scene['id'] and s['sceneName']==scene_name for s in scenes):
                raise BridgeError('同名场景已存在，未修改')
            folder=Path(backup_directory).expanduser().resolve();folder.mkdir(parents=True,exist_ok=True)
            backup=folder/f'chapters-{game_id}-{uuid.uuid4().hex}.json'
            with backup.open('x',encoding='utf-8') as f:
                json.dump({'before':before,'game_id':game_id,'chapter_name':chapter_name,'scene_name':scene_name,
                           'created_at':datetime.now(timezone.utc).isoformat()},f,ensure_ascii=False,indent=2)
            if self.tree(api,game_id)!=before:
                raise BridgeError('章节结构并发变化，未修改')
            base=f'{BASE}/game/{game_id}'
            if chapter is None:
                chapter=self.data(api.post(base+'/chapter',json={'chapterName':chapter_name}))
            elif chapter['chapterName']!=chapter_name:
                self.data(api.put(f"{base}/chapter/{chapter['id']}/name",json={'chapterName':chapter_name}))
            if scene is None:
                scene=self.data(api.post(base+'/scene',json={'chapterId':chapter['id'],'sceneName':scene_name}))
            elif scene['sceneName']!=scene_name:
                self.data(api.put(f"{base}/scene/{scene['id']}/name",json={'sceneName':scene_name}))
            after=flatten(self.tree(api,game_id))
            matched=[c for c in after if c['id']==chapter['id'] and c['chapterName']==chapter_name]
            verified=bool(matched and any(s['id']==scene['id'] and s['sceneName']==scene_name for s in matched[0].get('scenes',[])))
            if not verified: raise BridgeError('章节更新后读回不一致，请先检查结构，勿盲目重试')
            return {'game_id':game_id,'chapter_id':chapter['id'],'scene_id':scene['id'],'verified':True,'backup':str(backup)}
