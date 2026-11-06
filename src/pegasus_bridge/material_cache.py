"""Complete the native importer's local cache step for owned registered media."""
from pathlib import Path
import hashlib,json,shutil,struct,uuid
from .core import BridgeError,positive_id
from .ui import UIService,BASE,bounded_errors
from .workfiles import read_json,atomic_json,file_lock

def file_sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def video_container_probe(path):
    """Bounded box-header inspection only; this does not prove decodability."""
    size=path.stat().st_size;found=set()
    with path.open('rb') as stream:
        for _ in range(4096):
            start=stream.tell()
            if start==size:
                return 'fragmented_mp4' if b'moof' in found else 'flat_mp4' if {b'ftyp',b'moov',b'mdat'}<=found else 'unknown'
            header=stream.read(8)
            if len(header)!=8:return 'unknown'
            length,kind=struct.unpack('>I4s',header)
            if length==1:
                extended=stream.read(8)
                if len(extended)!=8:return 'unknown'
                length=struct.unpack('>Q',extended)[0]
            elif length==0:length=size-start
            if length<stream.tell()-start or start+length>size:return 'unknown'
            found.add(kind)
            if kind==b'moof':return 'fragmented_mp4'
            stream.seek(start+length)
    return 'unknown'

class MaterialCacheService(UIService):
    @bounded_errors
    def cache(self,game_id,manifest_path,checkpoint):
        game_id=positive_id(game_id)
        manifest=Path(manifest_path).expanduser().resolve()
        value=read_json(manifest)
        entries=value.get('checkpoints',[])
        if value.get('game_id')!=game_id or not isinstance(entries,list) or not 1<=len(entries)<=1200:
            raise BridgeError('缓存清单须为同作品的1–1200个导入检查点')
        records=[];seen=set()
        for name in entries:
            path=(manifest.parent/name).resolve()
            record=read_json(path,limit=16384)
            category=record.get('category_id')
            if (record.get('game_id')!=game_id or category not in (1,2,3,4) or
                record.get('registration_pending') or not record.get('file')):
                raise BridgeError('缓存来源必须为同作品已确认登记的本地素材')
            identity=positive_id(record.get('id'))
            if identity in seen:raise BridgeError('缓存清单含重复素材ID')
            seen.add(identity)
            source=Path(record['file']).resolve()
            sha=file_sha(source)
            if sha!=record.get('sha256'):raise BridgeError('本地素材已变化，拒绝写入预览缓存')
            folder='Videos' if category==4 else 'Audio' if category==3 else 'Images'
            filename=str(identity)+('.mp4' if category==4 else '')
            target=self.bridge.data/'AssetCache'/folder/filename
            records.append(dict(id=identity,category=category,url=record.get('url'),source=str(source),
                                cache_path=str(target),sha256=sha,bytes=source.stat().st_size))
            if category==4:records[-1]['video_container']=video_container_probe(source)
        # Authoritative owned catalog lookup; never accept arbitrary checkpoint IDs/URLs.
        with self.client() as api:
            for category in sorted({r['category'] for r in records}):
                material_type='videoV2' if category==4 else 'audioV2' if category==3 else 'imageV2'
                catalogs=self.data(api.get(f'{BASE}/game/{game_id}/material/catalog/3d/v2',params={'categoryId':category,'materialType':material_type}))
                native={}
                for catalog in catalogs.get('ownerList',[]):
                    for offset in range(0,10000,100):
                        page=self.data(api.get(f'{BASE}/game/{game_id}/material/catalog/{positive_id(catalog["id"])}',params={'offset':offset,'limit':100}))
                        if not isinstance(page,list):raise BridgeError('原生素材目录结构异常')
                        native.update({x['id']:x for x in page})
                        if len(page)<100:break
                    else:raise BridgeError('素材目录分页超限，未写缓存')
                for record in (r for r in records if r['category']==category):
                    remote=native.get(record['id'])
                    if not remote or remote.get('url')!=record['url'] or remote.get('size')!=record['bytes']:
                        raise BridgeError('原生素材ID、URL或字节长度不一致；未写缓存')
        cp=Path(checkpoint).expanduser().resolve()
        with file_lock(cp.with_suffix(cp.suffix+'.lock')):
            state=dict(game_id=game_id,total=len(records),completed=0,status='running',files=[],
                       source='native TryCopyUploadedToCache protocol; local files only',cloud_access_changed=False)
            containers={kind:sum(r.get('video_container')==kind for r in records)
                        for kind in ('fragmented_mp4','flat_mp4','unknown')}
            state.update(video_container_summary=containers,native_playback_verified=False,
                         warnings=([f"{containers['fragmented_mp4']} 个分片 MP4：部分 Windows Unity 播放器可能报 Cannot read file；缓存 SHA 校验不能证明原生可播放。保留原件，另行验证无损重封装，勿以重上传绕过云端拒绝。"]
                                   if containers['fragmented_mp4'] else []))
            atomic_json(cp,state)
            needed={}
            for r in records:
                target=Path(r['cache_path']);target.parent.mkdir(parents=True,exist_ok=True)
                if target.is_file() and file_sha(target)==r['sha256']:continue
                if (r.get('video_container')=='fragmented_mp4' and target.is_file() and
                    video_container_probe(target)=='flat_mp4'):
                    raise BridgeError('原生缓存已有不同 SHA 的普通 MP4，可能是已验证的无损衍生文件；拒绝用原始分片文件覆盖。请先审计原件/衍生文件及登记字节长度。')
                volume=target.parent.resolve().anchor
                needed[volume]=needed.get(volume,0)+r['bytes']
            for volume,size in needed.items():
                if shutil.disk_usage(volume).free<size+512*1024*1024:
                    raise BridgeError('预览缓存所在磁盘空间不足；保留原文件，请先调整缓存存储位置')
            for r in records:
                source,target=Path(r['source']),Path(r['cache_path'])
                reused=target.is_file() and file_sha(target)==r['sha256']
                backup=None
                if not reused:
                    if target.exists():
                        backup=cp.parent/'native-cache-backups'/(str(r['id'])+'-'+uuid.uuid4().hex+'.bin')
                        backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
                    temp=target.with_name(target.name+'.import-'+uuid.uuid4().hex)
                    try:
                        shutil.copy2(source,temp)
                        if file_sha(temp)!=r['sha256']:raise BridgeError('缓存复制校验失败')
                        temp.replace(target)
                    finally:
                        if temp.exists():temp.unlink()
                state['files'].append({**r,'verified':True,'reused':reused,'backup':str(backup) if backup else None})
                state['completed']+=1
                if state['completed']%25==0:atomic_json(cp,state)
            state['status']='complete';atomic_json(cp,state)
        return {k:v for k,v in state.items() if k!='files'}|{'checkpoint':str(cp),'verified':True}
