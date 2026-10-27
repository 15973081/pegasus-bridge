import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
import httpx
from pegasus_bridge.chapters import ChapterService
from pegasus_bridge.core import BridgeError

class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'backups';self.tree=[];self.calls=[]
        self.bridge=Mock();self.bridge._auth.return_value=({},{});self.handler=self.handle
    def handle(self,req):
        self.calls.append(req);data={}
        if req.method=='GET':data=json.dumps(self.tree)
        elif req.method=='POST':
            body=json.loads(req.content)
            if req.url.path.endswith('/chapter'):
                data={'id':1,'chapterName':body['chapterName'],'scenes':[],'chapters':[]};self.tree.append(data)
            else:
                data={'id':2,'chapterId':body['chapterId'],'sceneName':body['sceneName']};self.tree[0]['scenes'].append(data)
        elif req.method=='PUT':
            body=json.loads(req.content)
            if 'chapterName' in body:self.tree[0]['chapterName']=body['chapterName']
            else:self.tree[0]['scenes'][0]['sceneName']=body['sceneName']
        return httpx.Response(200,json={'state':{'code':200000},'data':copy.deepcopy(data)})
    def ensure(self,**kw):
        return ChapterService(self.bridge,httpx.MockTransport(self.handler)).ensure(42,'第一章','醒来',str(self.path),**kw)
    def test_create_and_reuse(self):
        result=self.ensure();self.assertTrue(result['verified']);self.assertTrue(Path(result['backup']).exists())
        self.ensure();self.assertEqual(sum(r.method=='POST' for r in self.calls),2)
    def test_invalid_target_refuses_writes(self):
        with self.assertRaises(BridgeError):self.ensure(chapter_id=999)
        self.assertFalse(any(r.method!='GET' for r in self.calls))

    def test_invalid_name_refuses_before_network(self):
        with self.assertRaises(BridgeError):
            ChapterService(self.bridge,httpx.MockTransport(self.handler)).ensure(42,'第一章 · 苏醒','01 病房',str(self.path))
        self.assertFalse(self.calls)
    def test_duplicate_name_refuses(self):
        self.tree=[{'id':1,'chapterName':'第一章'},{'id':2,'chapterName':'第一章'}]
        with self.assertRaises(BridgeError):self.ensure()
        self.assertFalse(any(r.method!='GET' for r in self.calls))
    def test_backup_failure_refuses(self):
        self.path.write_text('blocked')
        with self.assertRaises(BridgeError):self.ensure()
        self.assertFalse(any(r.method!='GET' for r in self.calls))
    def test_rename_preserves_scene_id(self):
        self.tree=[{'id':1,'chapterName':'旧章','scenes':[{'id':2,'sceneName':'旧场景'}]}]
        result=self.ensure(chapter_id=1,scene_id=2)
        self.assertEqual(result['scene_id'],2);self.assertFalse(any(r.method=='POST' for r in self.calls))
    def test_concurrent_tree_change_refuses(self):
        count=0
        def handler(req):
            nonlocal count
            count+=1
            if count==2:self.tree=[{'id':3,'chapterName':'用户新增','scenes':[]}]
            return self.handle(req)
        self.handler=handler
        with self.assertRaises(BridgeError):self.ensure()
        self.assertFalse(any(r.method!='GET' for r in self.calls))
