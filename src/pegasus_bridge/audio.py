"""WAV upload using the same material service as the verified game uploader."""
import hashlib
from pathlib import Path
import re
import wave
import json
import math
import os
import subprocess

from .core import BridgeError, positive_id
from .ui import UIService, BASE, bounded_errors
from .workfiles import atomic_json, read_json, file_lock


class AudioService(UIService):
    @bounded_errors
    def upload_audio(self, game_id, audio_path, alias, checkpoint):
        game_id = positive_id(game_id)
        path = Path(audio_path).expanduser().resolve()
        suffix=path.suffix.lower()
        if suffix not in ('.wav','.mp3') or not path.is_file() or not 44 <= path.stat().st_size <= 32*1024*1024:
            raise BridgeError('音频须为 32 MiB 以内 PCM WAV 或 MP3')
        if not isinstance(alias,str) or not 1 <= len(alias) <= 100:
            raise BridgeError('素材名须为 1–100 字符')
        if suffix=='.mp3':
            try:
                info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
                    'format=duration:stream=codec_type,codec_name','-of','json',str(path)],
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                    stderr=subprocess.DEVNULL,timeout=30))
                audio=[s for s in info['streams'] if s.get('codec_type')=='audio']
                duration=float(info['format']['duration'])
                if len(audio)!=1 or audio[0]['codec_name']!='mp3' or len(info['streams'])!=1 or not math.isfinite(duration) or duration<=0:
                    raise BridgeError('MP3 须为单一有效 MPEG Layer III 音轨')
            except (subprocess.SubprocessError,OSError):
                raise BridgeError('MP3 验证需要可用的 ffprobe') from None
        else:
            try:
                with wave.open(str(path)) as wav:
                    if wav.getcomptype() != 'NONE' or wav.getnchannels() not in (1,2) or not 8000 <= wav.getframerate() <= 192000:
                        raise BridgeError('仅支持单声道或立体声、8–192 kHz PCM WAV')
                    frames, rate = wav.getnframes(), wav.getframerate()
                    if not frames or len(wav.readframes(frames)) != frames*wav.getnchannels()*wav.getsampwidth():
                        raise BridgeError('WAV 数据截断或为空')
                    duration = frames/rate
            except (wave.Error, EOFError):
                raise BridgeError('无法解析 PCM WAV') from None
        raw = path.read_bytes()
        identity = dict(game_id=game_id, sha256=hashlib.sha256(raw).hexdigest(), alias=alias, category_id=3)
        cp = Path(checkpoint).expanduser().resolve()
        with file_lock(cp.with_name(cp.name+'.lock')):
            record = read_json(cp) if cp.exists() else identity.copy()
            if not isinstance(record,dict) or any(record.get(k) != v for k,v in identity.items()):
                raise BridgeError('上传检查点与音频/作品不匹配')
            if record.get('id'):
                return dict(**record, reused=True)
            if record.get('registration_pending'):
                raise BridgeError('上次注册结果不确定；先核对素材库，拒绝盲目重复注册')
            with self.client() as api:
                catalogs = self.data(api.get(f'{BASE}/game/{game_id}/material/catalog/3d/v2',params={'categoryId':3,'materialType':'audioV2'}))
                defaults=[x['id'] for x in catalogs['ownerList'] if x.get('isDefault')==1]
                if len(defaults)!=1:raise BridgeError('无法唯一确定音频目录')
                if not record.get('url'):
                    token=self.filepicker_grant(api,'MATERIAL_AUDIO')
                    with self.client(authenticated=False) as uploader:
                        result=uploader.post('https://fp.ps.netease.com/a13/file/new/',data={'Authorization':token},files={'fpfile':(path.name,raw,'audio/mpeg' if suffix=='.mp3' else 'audio/wav')})
                    fid=result.headers.get('response-fid','')
                    if result.status_code not in (200,201) or not re.fullmatch(r'[A-Za-z0-9_-]+',fid):
                        raise BridgeError(f'音频文件上传未确认成功（HTTP {result.status_code}；response-fid 无效）')
                    record.update(url='https://a13.fp.ps.netease.com/file/'+fid,duration=duration,file=str(path))
                    atomic_json(cp,record)
                if not re.fullmatch(r'https://a13\.fp\.ps\.netease\.com/file/[A-Za-z0-9_-]+',record['url']):
                    raise BridgeError('音频检查点 URL 无效')
                record['registration_pending']=True
                atomic_json(cp,record)
                info=dict(materialAlias=alias,url=record['url'],catalogId=defaults[0],categoryId=3,fileType=suffix[1:],size=len(raw),duration=duration)
                material=self.data(api.post(f'{BASE}/game/{game_id}/material',json=info))
                record.update(id=positive_id(material['id']),gameMaterialId=material.get('gameMaterialId'),registration_pending=False)
                atomic_json(cp,record)
            return dict(**record,reused=False)
