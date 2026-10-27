import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import httpx
from pegasus_bridge.core import BridgeError
from pegasus_bridge.mall import MallUIService, GoodsService
from pegasus_bridge.scene import source_hash
from pegasus_bridge.ui import digest

UI_ID='d6611bde-3d76-4e2f-bf53-7ff939297a7f'

class MallUITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'mall.lua'
        self.old='-- 商城 😀\nreturn 1';self.new='-- 新版商城\nreturn 2';self.remote=self.old
        self.path.write_text(self.new,encoding='utf-8');self.calls=[];self.reads=0
        self.member={'id':UI_ID,'name':'商城UI','isFlow':1,'errorStatus':0}
        self.catalogs=[{'id':7,'type':12,'mode':8,'command':json.dumps([self.member])}]
        self.bridge=Mock();self.bridge._auth.return_value=({},{});self.handler=self.handle
    def handle(self,req):
        self.calls.append(req)
        if req.url.path.endswith('custom-ui-catalog'):data=copy.deepcopy(self.catalogs)
        elif req.url.path.endswith('/token'):data={'token':'private-token'}
        elif req.method=='PUT':
            commands=json.loads(req.content)['commandList']
            self.remote=next(c['content']for c in commands if c['type']=='INSERT');data={}
        else:self.reads+=1;data={'command':self.remote}
        return httpx.Response(200,json={'state':{'code':200000},'data':data})
    def service(self):return MallUIService(self.bridge,httpx.MockTransport(self.handler),lambda gid:'private-session')
    def apply(self,**kw):
        return self.service().apply_ui(42,7,kw.get('ui_id',UI_ID),str(self.path),kw.get('sha',source_hash(self.old)),str(self.root/'backups'))
    def writes(self):return [r for r in self.calls if r.method=='PUT']
    def test_read_and_hash(self):
        r=self.service().read_ui(42,7,UI_ID);self.assertEqual(r['name'],'商城UI');self.assertEqual(r['sha256'],source_hash(self.old));self.assertFalse(self.writes())
    def test_update_utf16_backup_and_no_secrets(self):
        r=self.apply();self.assertTrue(r['verified'])
        request=json.loads(self.writes()[0].content)
        self.assertEqual(request['commandList'][0]['length'],len(self.old.encode('utf-16-le'))//2)
        saved=Path(r['backup']).read_text(encoding='utf-8')
        self.assertEqual(json.loads(saved)['before'],self.old)
        self.assertNotIn('private-token',saved);self.assertNotIn('private-session',saved)
    def test_empty_only_insert(self):
        self.old=self.remote='';self.apply()
        self.assertEqual([c['type']for c in json.loads(self.writes()[0].content)['commandList']],['INSERT'])
    def test_idempotent(self):
        self.remote=self.old=self.new;self.assertFalse(self.apply()['changed']);self.assertFalse(self.writes())
    def test_wrong_catalog_type(self):
        self.catalogs[0]['type']=99
        with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.writes())
    def test_missing_member(self):
        self.catalogs[0]['command']='[]'
        with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.writes())
    def test_duplicate_member(self):
        self.catalogs[0]['command']=json.dumps([self.member,self.member])
        with self.assertRaises(BridgeError):self.apply()
    def test_path_injection_rejected_before_network(self):
        with self.assertRaises(BridgeError):self.apply(ui_id='../other')
        self.assertFalse(self.calls)
    def test_hash_conflict(self):
        with self.assertRaises(BridgeError):self.apply(sha='0'*64)
        self.assertFalse(self.writes())
    def test_empty_code(self):
        self.path.write_text(' ',encoding='utf-8')
        with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.calls)
    def test_concurrent_change(self):
        def handler(req):
            if req.url.path.endswith('/token'):self.remote='user edit'
            return self.handle(req)
        self.handler=handler
        with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.writes())
    def test_backup_failure(self):
        (self.root/'backups').write_text('blocked')
        with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.writes())
    def test_readback_failure(self):
        def handler(req):
            result=self.handle(req)
            if req.method=='PUT':self.remote='server changed it'
            return result
        self.handler=handler
        with self.assertRaises(BridgeError):self.apply()
        self.assertEqual(len(self.writes()),1)

class GoodsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.before=dict(id=9,name='调查道具',price=10,payType=1,productId='binding',type=3,valueOper='preserve',childrenList='[]')
        self.rows=[copy.deepcopy(self.before)];self.calls=[];self.handler=self.handle
        self.bridge=Mock();self.bridge._auth.return_value=({},{});self.changes={'name':'新名称','price':12}
    def handle(self,req):
        self.calls.append(req)
        if req.method=='PUT':self.rows[0].update(json.loads(req.content));data='ok'
        else:data=copy.deepcopy(self.rows)
        return httpx.Response(200,json={'state':{'code':200000},'data':data})
    def apply(self):return GoodsService(self.bridge,httpx.MockTransport(self.handler)).apply(42,9,self.changes,digest(self.before),self.tmp.name)
    def writes(self):return [r for r in self.calls if r.method=='PUT']
    def test_update_preserves_binding_and_benefits(self):
        r=self.apply();self.assertTrue(r['verified']);self.assertEqual(self.rows[0]['valueOper'],'preserve');self.assertEqual(self.rows[0]['childrenList'],'[]')
        body=json.loads(self.writes()[0].content);self.assertEqual(body['productId'],'binding');self.assertNotIn('valueOper',body)
        self.assertEqual(json.loads(Path(r['backup']).read_text(encoding='utf-8'))['before'],self.before)
    def test_wrong_goods(self):
        self.rows=[]
        with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.writes())
    def test_reject_invalid_fields_and_values(self):
        for change in [{'price':True},{'price':0},{'price':1.5},{'payType':3},{'payType':False},{'name':''},{'description':'x'},{'productId':'other'},{'type':2},{}]:
            with self.subTest(change=change):
                self.changes=change
                with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.calls)
    def test_pay_type(self):
        self.changes={'payType':2};self.assertTrue(self.apply()['verified']);self.assertEqual(self.rows[0]['payType'],2)
    def test_idempotent(self):
        self.changes={'price':10};self.assertFalse(self.apply()['changed']);self.assertFalse(self.writes())
    def test_conflict(self):
        self.rows[0]['price']=20
        with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.writes())
    def test_concurrent_change(self):
        def handler(req):
            if len(self.calls)==1:self.rows[0]['price']=99
            return self.handle(req)
        self.handler=handler
        with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.writes())
    def test_missing_type_refused(self):
        del self.before['type'];self.rows=[copy.deepcopy(self.before)]
        with self.assertRaises(BridgeError):self.apply()
        self.assertFalse(self.writes())
    def test_readback_mismatch(self):
        def handler(req):
            result=self.handle(req)
            if req.method=='PUT':self.rows[0]['price']=999
            return result
        self.handler=handler
        with self.assertRaises(BridgeError):self.apply()
        self.assertEqual(len(self.writes()),1)
