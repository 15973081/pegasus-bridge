"""Native H.264 material import with durable registration checkpoints."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import uuid

import httpx
from .core import BridgeError, positive_id, redact
from .ui import UIService, BASE, bounded_errors
from .workfiles import atomic_json, file_lock, read_json

FILE_URL = re.compile(r'https://a13\.fp\.ps\.netease\.com/file/[A-Za-z0-9_-]+')


class VideoService(UIService):
    @bounded_errors
    def upload_status(self,game_id,check_urls=None):
        game_id=positive_id(game_id)
        check_urls=check_urls or []
        if not isinstance(check_urls,list) or len(check_urls)>10 or any(not isinstance(u,str) or not FILE_URL.fullmatch(u) for u in check_urls):
            raise BridgeError('仅可检查最多10个原生素材服务器URL')
        # Both routes occur in shipped client channel selection. Tokens never leave this process.
        report=[]
        with self.client() as api:
            for prefix in (() if check_urls else ('avg-portal-api','tianma-server-api')):
                headers,params=({}, {}) if prefix=='avg-portal-api' else self.bridge._tianma_auth()
                response=api.get(f'https://avg.163.com/{prefix}/filepicker/token',headers=headers,
                                 params={**params,'type':'TIANMA_MATERIAL_VIDEO'})
                body=response.json() if response.status_code==200 else {}
                state=body.get('state',{})
                data=body.get('data')
                report.append(dict(portal_prefix=prefix,http_status=response.status_code,
                                   code=state.get('code'),message=redact(str(state.get('message','')))[:200],
                                   token_returned=isinstance(data,dict) and bool(data.get('token'))))
        access=[]
        with self.client(authenticated=False) as anonymous:
            for url in check_urls:
                with anonymous.stream('GET',url,headers={'Range':'bytes=0-0','Referer':'https://avg.163.com/'}) as response:
                    detail=''
                    if response.status_code>=400:
                        first=next(response.iter_bytes(chunk_size=512),b'')
                        detail=redact(first.decode('utf-8',errors='replace'))[:400]
                    access.append(dict(url=url,http_status=response.status_code,
                                       content_type=response.headers.get('content-type'),
                                       server=response.headers.get('server'),error_excerpt=detail))
        return dict(game_id=game_id,source='fixed-origin token-route diagnostics',routes=report,anonymous_access=access,
                    files_uploaded=0,materials_registered=0,credentials_returned=False)

    def _upload_file(self, api, path, upload_type, mime):
        token = self.filepicker_grant(api,upload_type)
        with path.open('rb') as stream, self.client(authenticated=False) as uploader:
            result = uploader.post('https://fp.ps.netease.com/a13/file/new/',
                                   data={'Authorization': token},
                                   files={'fpfile': (path.name, stream, mime)},
                                   timeout=httpx.Timeout(600, connect=30))
        fid = result.headers.get('response-fid', '')
        if result.status_code not in (200, 201) or not re.fullmatch(r'[A-Za-z0-9_-]+', fid):
            raise BridgeError(f'视频文件上传未确认（HTTP {result.status_code}）')
        return 'https://a13.fp.ps.netease.com/file/' + fid

    @bounded_errors
    def catalog(self, game_id, catalog_id=None, offset=0, limit=100):
        game_id = positive_id(game_id)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise BridgeError('offset 必须为非负整数')
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise BridgeError('limit 必须为 1–100')
        with self.client() as api:
            if catalog_id is None:
                return self.data(api.get(f'{BASE}/game/{game_id}/material/catalog/3d/v2',
                                         params={'categoryId':4, 'materialType':'videoV2'}))
            rows = self.data(api.get(f'{BASE}/game/{game_id}/material/catalog/{positive_id(catalog_id)}',
                                     params={'limit':limit, 'offset':offset}))
            if not isinstance(rows,list):
                raise BridgeError('素材分页响应不是列表')
            return dict(items=rows,offset=offset,limit=limit,returned=len(rows),possibly_more=len(rows)==limit)

    @bounded_errors
    def upload_video(self, game_id, video_path, alias, checkpoint):
        game_id = positive_id(game_id)
        path = Path(video_path).expanduser().resolve()
        if path.suffix.lower() != '.mp4' or not 32 <= path.stat().st_size <= 512*1024*1024:
            raise BridgeError('仅支持 512MiB 以内非空 MP4')
        if not isinstance(alias, str) or not 1 <= len(alias) <= 100:
            raise BridgeError('素材名称长度必须为 1–100')
        if '.' in alias:
            raise BridgeError('视频素材名称不能包含英文句号，请使用下划线；未发起上传')
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        identity = dict(game_id=game_id, sha256=sha, alias=alias, category_id=4)
        cp = Path(checkpoint).expanduser().resolve()
        with file_lock(cp.with_suffix(cp.suffix + '.lock')):
            record = read_json(cp, limit=16384) if cp.exists() else identity.copy()
            if not isinstance(record, dict) or any(record.get(k) != v for k,v in identity.items()):
                raise BridgeError('上传检查点与作品 / 文件 / 分类不符')
            for key in ('url', 'cover_url'):
                if record.get(key) and not FILE_URL.fullmatch(record[key]):
                    raise BridgeError('检查点 URL 不符合固定素材服务器范围')
            if record.get('id'):
                positive_id(record['id'])
                return {**record, 'reused':True}
            if record.get('registration_pending'):
                raise BridgeError('素材注册结果不确定；先读取素材库核对，不可盲目重复注册')
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            try:
                info = json.loads(subprocess.check_output([
                    'ffprobe', '-v','error','-show_entries',
                    'format=duration:stream=codec_type,codec_name,width,height','-of','json',str(path)],
                    creationflags=flags, stderr=subprocess.DEVNULL, timeout=30))
            except (subprocess.SubprocessError, OSError):
                raise BridgeError('需要可用的 ffprobe，且 MP4 必须可解析') from None
            videos = [s for s in info['streams'] if s.get('codec_type') == 'video']
            duration = float(info['format']['duration'])
            if len(videos) != 1 or videos[0]['codec_name'] != 'h264' or not math.isfinite(duration) or duration <= 0:
                raise BridgeError('仅支持一个 H.264 视频轨道及有效时长')
            width,height = videos[0]['width'],videos[0]['height']
            if not 0 < width <= 8192 or not 0 < height <= 8192:
                raise BridgeError('视频尺寸超出范围')
            cover = cp.parent/'video-covers'/(sha+'.png')
            cover.parent.mkdir(parents=True,exist_ok=True)
            if not cover.exists():
                temp = cover.with_name(cover.stem+'.tmp-'+uuid.uuid4().hex+'.png')
                try:
                    subprocess.run(['ffmpeg','-v','error','-y','-i',str(path),'-frames:v','1',str(temp)],
                                   check=True, creationflags=flags, stderr=subprocess.DEVNULL, timeout=30)
                    temp.replace(cover)
                except (subprocess.SubprocessError, OSError):
                    raise BridgeError('视频封面提取失败；请检查 ffmpeg') from None
            with self.client() as api:
                catalogs = self.data(api.get(f'{BASE}/game/{game_id}/material/catalog/3d/v2',
                                             params={'categoryId':4,'materialType':'videoV2'}))
                defaults = [x['id'] for x in catalogs['ownerList'] if x.get('isDefault') == 1]
                if len(defaults) != 1:
                    raise BridgeError('无法唯一确定本作品默认视频分类')
                if not record.get('url'):
                    record.update(url=self._upload_file(api,path,'TIANMA_MATERIAL_VIDEO','video/mp4'),
                                  file=str(path),duration=duration,width=width,height=height)
                    atomic_json(cp,record)
                if not record.get('cover_url'):
                    record['cover_url'] = self._upload_file(api,cover,'MATERIAL_IMAGE','image/png')
                    atomic_json(cp,record)
                payload = dict(materialAlias=alias,url=record['url'],catalogId=defaults[0],categoryId=4,
                               fileType='mp4',size=path.stat().st_size,duration=duration,width=width,height=height,
                               frames=json.dumps({'cover':record['cover_url']},separators=(',',':')))
                record['registration_pending'] = True
                atomic_json(cp,record)
                material = self.data(api.post(f'{BASE}/game/{game_id}/material',json=payload))
                record.update(id=positive_id(material['id']),gameMaterialId=material.get('gameMaterialId'),
                              registration_pending=False)
                atomic_json(cp,record)
            return {**record,'reused':False}
