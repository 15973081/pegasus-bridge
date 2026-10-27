import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
import httpx
from pegasus_bridge.storytree import StoryTreeService
from pegasus_bridge.ui import digest
from pegasus_bridge.core import BridgeError
class StoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.before={'gameId':42,'sceneId':7,'nodes':[]};self.remote=copy.deepcopy(self.before);self.writes=0;self.gets=0
  self.target={'gameId':42,'sceneId':7,'nodes':[{'name':'a','video_id':'a','title_text':'开始','story_res_id':'$123','next_video':'b'},{'name':'b','video_id':'b','title_text':'结局','story_res_id':'$124','next_video':''}]}
  self.bridge=Mock();self.bridge._auth.return_value=({},{});self.change=False;self.drop=False
 def handler(self,req):
  if req.method=='GET':
   self.gets+=1
   if self.change and self.gets==2:self.remote['nodes']=[{'user':'edit'}]
  else:
   self.writes+=1
   if not self.drop:self.remote=json.loads(req.content)
  return httpx.Response(200,json={'state':{'code':200000},'data':copy.deepcopy(self.remote)})
 def apply(self):return StoryTreeService(self.bridge,httpx.MockTransport(self.handler)).apply(42,7,self.target,digest(self.before),self.tmp.name)
 def test_write_backup_readback(self):
  r=self.apply();self.assertTrue(r['verified']);self.assertEqual(self.writes,1);self.assertEqual(json.loads(Path(r['backup']).read_text(encoding='utf-8'))['before'],self.before)
 def test_duplicate_refused(self):
  self.target['nodes'][1]['video_id']='a'
  with self.assertRaises(BridgeError):self.apply()
  self.assertEqual(self.gets,0)
 def test_dangling_refused(self):
  self.target['nodes'][0]['next_video']='missing'
  with self.assertRaises(BridgeError):self.apply()
  self.assertEqual(self.writes,0)
 def test_cycle_refused(self):
  self.target['nodes'][1]['next_video']='a'
  with self.assertRaises(BridgeError):self.apply()
 def test_wrong_target_refused(self):
  self.target['sceneId']=8
  with self.assertRaises(BridgeError):self.apply()
 def test_hash_conflict(self):
  self.remote['nodes']=[{'user':'edit'}]
  with self.assertRaises(BridgeError):self.apply()
  self.assertEqual(self.writes,0)
 def test_concurrent_edit(self):
  self.change=True
  with self.assertRaises(BridgeError):self.apply()
  self.assertEqual(self.writes,0)
 def test_failed_readback(self):
  self.drop=True
  with self.assertRaises(BridgeError):self.apply()
 def test_idempotent(self):
  self.remote=copy.deepcopy(self.target);self.before=copy.deepcopy(self.target)
  self.apply();self.assertEqual(self.writes,0)
