import json,hashlib,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
import httpx
from pegasus_bridge.core import BridgeError
from pegasus_bridge.material_cache import MaterialCacheService,video_container_probe

class MaterialCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'owned.mp4';self.source.write_bytes(b'OWNED_VIDEO'*20)
        self.url='https://a13.fp.ps.netease.com/file/own'
        self.sourcecp=self.root/'upload.json'
        self.sourcecp.write_text(json.dumps(dict(game_id=42,id=11,category_id=4,url=self.url,file=str(self.source),sha256=hashlib.sha256(self.source.read_bytes()).hexdigest())))
        self.manifest=self.root/'manifest.json';self.manifest.write_text(json.dumps(dict(game_id=42,checkpoints=['upload.json'])))
        self.native=self.root/'native';bridge=Mock();bridge.data=self.native;bridge._auth.return_value=({}, {})
        self.remote_url=self.url;self.calls=[]
        def handle(request):
            self.calls.append(request)
            rows={'ownerList':[{'id':22}]} if '/3d/v2' in request.url.path else [dict(id=11,url=self.remote_url,size=self.source.stat().st_size)]
            return httpx.Response(200,json={'state':{'code':200000},'data':rows})
        self.service=MaterialCacheService(bridge,httpx.MockTransport(handle));self.output=self.root/'result.json'
    def run_cache(self):return self.service.cache(42,str(self.manifest),str(self.output))
    def test_verified_copy_reuse_and_read_only_cloud_requests(self):
        self.assertTrue(self.run_cache()['verified'])
        target=self.native/'AssetCache/Videos/11.mp4'
        self.assertEqual(target.read_bytes(),self.source.read_bytes())
        self.run_cache()
        self.assertTrue(json.loads(self.output.read_text())['files'][0]['reused'])
        self.assertTrue(all(r.method=='GET' for r in self.calls))
    def test_remote_identity_conflict_never_writes_cache(self):
        self.remote_url='https://a13.fp.ps.netease.com/file/different'
        with self.assertRaises(BridgeError):self.run_cache()
        self.assertFalse(self.native.exists())
    def test_modified_source_refused_before_network(self):
        self.source.write_bytes(b'CHANGED')
        with self.assertRaises(BridgeError):self.run_cache()
        self.assertEqual(self.calls,[])
    def test_stale_local_cache_preserved_before_replacement(self):
        target=self.native/'AssetCache/Videos/11.mp4';target.parent.mkdir(parents=True);target.write_bytes(b'STALE')
        self.run_cache()
        backup=json.loads(self.output.read_text())['files'][0]['backup']
        self.assertEqual(Path(backup).read_bytes(),b'STALE')
        self.assertEqual(target.read_bytes(),self.source.read_bytes())

    def test_fragmented_cache_is_verified_bytes_but_warns_about_native_playback(self):
        def box(kind,payload=b''):return struct.pack('>I4s',8+len(payload),kind)+payload
        self.source.write_bytes(box(b'ftyp',b'iso5')+box(b'moov')+box(b'moof')+box(b'mdat',b'encoded'))
        record=json.loads(self.sourcecp.read_text());record['sha256']=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.sourcecp.write_text(json.dumps(record))
        result=self.run_cache()
        self.assertTrue(result['verified']);self.assertFalse(result['native_playback_verified'])
        self.assertEqual(result['video_container_summary']['fragmented_mp4'],1)
        self.assertIn('Cannot read file',result['warnings'][0])
        self.assertEqual((self.native/'AssetCache/Videos/11.mp4').read_bytes(),self.source.read_bytes())

    def test_bounded_container_probe_handles_flat_extended_and_malformed_boxes(self):
        def box(kind,payload=b''):return struct.pack('>I4s',8+len(payload),kind)+payload
        self.source.write_bytes(box(b'ftyp')+struct.pack('>I4sQ',1,b'moov',16)+box(b'mdat'))
        self.assertEqual(video_container_probe(self.source),'flat_mp4')
        for raw in (b'',struct.pack('>I4s',3,b'ftyp'),struct.pack('>I4s',999999,b'ftyp'),struct.pack('>I4s',1,b'ftyp')):
            self.source.write_bytes(raw)
            self.assertEqual(video_container_probe(self.source),'unknown')

    def test_fragmented_import_does_not_destroy_existing_flat_cache(self):
        def box(kind,payload=b''):return struct.pack('>I4s',8+len(payload),kind)+payload
        self.source.write_bytes(box(b'ftyp',b'iso5')+box(b'moov')+box(b'moof')+box(b'mdat',b'encoded'))
        record=json.loads(self.sourcecp.read_text());record['sha256']=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.sourcecp.write_text(json.dumps(record))
        target=self.native/'AssetCache/Videos/11.mp4';target.parent.mkdir(parents=True)
        repaired=box(b'ftyp',b'isom')+box(b'moov')+box(b'mdat',b'encoded');target.write_bytes(repaired)
        with self.assertRaisesRegex(BridgeError,'拒绝'):
            self.run_cache()
        self.assertEqual(target.read_bytes(),repaired)
