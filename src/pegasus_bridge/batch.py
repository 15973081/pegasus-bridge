"""Reviewable frozen scene plans, resumable writes, and guarded restoration."""
from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import tempfile

from .core import BridgeError, positive_id
from .scene import SceneService, source_hash
from .ui import bounded_errors
from .validation import validate_source
from .workfiles import read_json, child, digest, atomic_json, new_text, file_lock


def now():return datetime.now(timezone.utc).isoformat()


class BatchService:
    def __init__(self,bridge,scene_service=None):
        self.bridge=bridge
        self.scene=scene_service or SceneService(bridge)

    def remote(self,gid,sid):
        value=self.bridge.read('commands',gid,sid)['response']['data']['command']
        if value is None:return ''
        if not isinstance(value,str):raise BridgeError('后台剧情格式无效')
        return value

    @bounded_errors
    def plan(self,manifest_path,output_directory):
        manifest_path=Path(manifest_path).expanduser().resolve()
        manifest=read_json(manifest_path)
        if not isinstance(manifest,dict) or set(manifest)!={'game_id','scenes'}:raise BridgeError('计划输入须包含 game_id 与 scenes')
        gid=positive_id(manifest['game_id']);scenes=manifest['scenes']
        if not isinstance(scenes,list) or not 1<=len(scenes)<=100:raise BridgeError('每个计划须包含 1–100 个场景')
        prepared=[];seen=set()
        for row in scenes:
            if not isinstance(row,dict) or set(row)!={'scene_id','script_path'}:raise BridgeError('场景项须包含 scene_id 与 script_path')
            sid=positive_id(row['scene_id'])
            if sid in seen:raise BridgeError('计划包含重复场景')
            seen.add(sid);path=child(manifest_path.parent,row['script_path'])
            if path.suffix.lower()!='.lua' or not path.is_file() or path.stat().st_size>1024*1024:raise BridgeError('场景文件无效')
            code=path.read_text(encoding='utf-8-sig');check=validate_source(code)
            if not check['valid']:raise BridgeError('场景未通过校验：'+str(sid))
            prepared.append((sid,code))
        # Read every old revision before creating the immutable plan directory.
        frozen=[(sid,code,self.remote(gid,sid)) for sid,code in prepared]
        folder=Path(output_directory).expanduser().resolve();folder.mkdir(parents=True,exist_ok=False)
        plan=dict(version=1,game_id=gid,created_at=now(),scenes=[])
        for i,(sid,code,before) in enumerate(frozen,1):
            old_name=f'{i:03d}-before.lua';new_name=f'{i:03d}-target.lua'
            new_text(folder/old_name,before);new_text(folder/new_name,code)
            delta=''.join(difflib.unified_diff(before.splitlines(True),code.splitlines(True),fromfile=old_name,tofile=new_name))
            new_text(folder/f'{i:03d}.diff',delta)
            plan['scenes'].append(dict(scene_id=sid,before=old_name,target=new_name,before_sha256=source_hash(before),target_sha256=source_hash(code)))
        plan['digest']=digest(plan)
        new_text(folder/'plan.json',json.dumps(plan,ensure_ascii=False,indent=2))
        return dict(plan_path=str(folder/'plan.json'),digest=plan['digest'],game_id=gid,scenes=len(frozen),applied=False,atomic_transaction=False)

    def load_plan(self,path):
        path=Path(path).expanduser().resolve();plan=read_json(path)
        if not isinstance(plan,dict) or set(plan)!={'version','game_id','created_at','scenes','digest'} or plan['version']!=1:raise BridgeError('计划格式无效')
        payload={k:v for k,v in plan.items() if k!='digest'}
        if digest(payload)!=plan['digest']:raise BridgeError('计划内容校验失败')
        gid=positive_id(plan['game_id']);seen=set();loaded=[]
        if not isinstance(plan['scenes'],list) or not 1<=len(plan['scenes'])<=100:raise BridgeError('计划场景数量无效')
        for row in plan['scenes']:
            if not isinstance(row,dict) or set(row)!={'scene_id','before','target','before_sha256','target_sha256'}:raise BridgeError('计划场景字段无效')
            sid=positive_id(row['scene_id'])
            if sid in seen:raise BridgeError('计划场景重复')
            seen.add(sid)
            files={k:child(path.parent,row[k]) for k in ['before','target']};contents={}
            for k,file in files.items():
                if not file.is_file() or file.stat().st_size>1024*1024:
                    raise BridgeError('冻结的源码文件不存在或过大')
                contents[k]=file.read_text(encoding='utf-8')
                if source_hash(contents[k])!=row[k+'_sha256']:
                    raise BridgeError('冻结的源码文件已变化：'+k)
            if not validate_source(contents['target'])['valid']:raise BridgeError('计划脚本验证失败')
            loaded.append((row,contents['target']))
        return path,plan,gid,loaded

    @bounded_errors
    def apply(self,plan_path):
        path,plan,gid,loaded=self.load_plan(plan_path)
        with file_lock(path.parent/'apply.lock'):
            journal_path=path.parent/'progress.json'
            journal=dict(plan_digest=plan['digest'],updated_at=now(),status='checking',scenes=[])
            conflict=False
            # Always reconcile actual remote content, including previously 'uncertain' saves.
            for row,target in loaded:
                actual=source_hash(self.remote(gid,row['scene_id']))
                status='verified' if actual==row['target_sha256'] else 'pending' if actual==row['before_sha256'] else 'conflict'
                conflict|=status=='conflict'
                journal['scenes'].append(dict(scene_id=row['scene_id'],status=status,observed_sha256=actual))
            if conflict:
                journal['status']='conflict';atomic_json(journal_path,journal)
                return dict(**journal,progress_path=str(journal_path),complete=False,atomic_transaction=False)
            atomic_json(journal_path,journal)
            for index,(row,target) in enumerate(loaded):
                step=journal['scenes'][index]
                if step['status']=='verified':continue
                step['status']='writing';atomic_json(journal_path,journal)
                try:
                    # Pass only the verified in-memory snapshot, never a mutable plan path.
                    with tempfile.TemporaryDirectory(prefix='pegasus-batch-') as temp:
                        source=new_text(Path(temp)/'target.lua',target)
                        result=self.scene.apply_scene(gid,row['scene_id'],str(source),row['before_sha256'],str(path.parent/'backups'))
                    if not result.get('verified'):raise BridgeError('写入未确认')
                    step.update(status='verified',result=result)
                except Exception:
                    # Do not blindly retry a write: a lost reply may follow a successful PUT.
                    step.update(status='uncertain',message='本项未确认；resume 会先读取实际内容再决定是否保存')
                    journal.update(status='needs_reconcile',updated_at=now());atomic_json(journal_path,journal)
                    return dict(**journal,progress_path=str(journal_path),complete=False,atomic_transaction=False)
                journal['updated_at']=now();atomic_json(journal_path,journal)
            journal['status']='complete';atomic_json(journal_path,journal)
            return dict(**journal,progress_path=str(journal_path),complete=True,atomic_transaction=False)

    def resume(self,plan_path):return self.apply(plan_path)

    @bounded_errors
    def history(self,backup_directory,game_id=None):
        root=Path(backup_directory).expanduser().resolve()
        if not root.is_dir():raise BridgeError('备份目录不存在')
        if game_id is not None:game_id=positive_id(game_id)
        rows=[]
        for file in sorted(root.glob('*.json')):
            if len(rows)>=1000:break
            if file.is_symlink() or file.stat().st_size>4*1024*1024:continue
            try:
                item=read_json(file)
                if not isinstance(item,dict) or not isinstance(item.get('before'),str) or not isinstance(item.get('requested'),str):continue
                gid,sid=positive_id(item['game_id']),positive_id(item['scene_id'])
                if game_id is not None and gid!=game_id:continue
                rows.append(dict(backup_path=str(file),game_id=gid,scene_id=sid,before_sha256=source_hash(item['before']),after_sha256=source_hash(item['requested'])))
            except (BridgeError,KeyError,ValueError):continue
        return dict(backups=rows,scope='scene backups in this directory only')

    def backup(self,backup_path):
        value=read_json(backup_path,4*1024*1024)
        if not isinstance(value,dict) or not all(isinstance(value.get(k),str) for k in ('before','requested','expected_sha256')):raise BridgeError('不是剧情备份')
        positive_id(value.get('game_id'));positive_id(value.get('scene_id'))
        if source_hash(value['before'])!=value['expected_sha256']:raise BridgeError('备份原文校验失败')
        return value

    @bounded_errors
    def diff(self,backup_path):
        value=self.backup(backup_path)
        text=''.join(difflib.unified_diff(value['before'].splitlines(True),value['requested'].splitlines(True),fromfile='before.lua',tofile='requested.lua'))
        return dict(game_id=value['game_id'],scene_id=value['scene_id'],diff=text[:50000],truncated=len(text)>50000)

    @bounded_errors
    def restore(self,backup_path,expected_sha256,backup_directory):
        value=self.backup(backup_path)
        with tempfile.TemporaryDirectory(prefix='pegasus-restore-') as temp:
            file=new_text(Path(temp)/'restore.lua',value['before'])
            return self.scene.apply_scene(value['game_id'],value['scene_id'],str(file),expected_sha256,backup_directory,allow_empty=True)
