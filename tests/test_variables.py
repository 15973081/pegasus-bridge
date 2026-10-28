import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import httpx
from pegasus_bridge.variables import VariableService
from pegasus_bridge.core import BridgeError
from pegasus_bridge.ui import digest


class CheckpointVariableTests(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(id=-1,catalogName='全部',values=[])]
        self.calls=[];self.reads=0;self.conflict=False
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.bridge=Mock();self.bridge._auth.return_value=({}, {})
    def handle(self,req):
        self.calls.append(req.method)
        if req.method=='GET':
            self.reads+=1
            if self.conflict and self.reads==2:self.rows[0]['catalogName']='changed'
            data=copy.deepcopy(self.rows)
        else:
            data=json.loads(req.content)
            self.assertEqual(data['category'],2);self.assertEqual(data['valueType'],2)
            self.assertEqual(data['stringValue'],'');self.assertIsNone(data['catalogId'])
            data['id']=73;self.rows[0]['values'].append(data)
        return httpx.Response(200,json={'state':{'code':200000},'data':data})
    def apply(self,name='w404_checkpoint_v5',sha=None):
        return VariableService(self.bridge,httpx.MockTransport(self.handle)).ensure_checkpoint(42,name,sha or digest(self.rows),self.tmp.name)
    def test_create_backup_and_readback(self):
        before=copy.deepcopy(self.rows);r=self.apply()
        self.assertTrue(r['verified']);self.assertEqual(r['variable_id'],73)
        self.assertEqual(json.loads(Path(r['backup']).read_text(encoding='utf-8'))['before'],before)
        self.assertEqual(self.calls,['GET','GET','POST','GET'])
    def test_reuse_keeps_existing_contents(self):
        self.apply();self.rows[0]['values'][0]['stringValue']='keep';self.calls=[]
        self.assertFalse(self.apply()['changed']);self.assertNotIn('POST',self.calls)
        self.assertEqual(self.rows[0]['values'][0]['stringValue'],'keep')
    def test_incompatible_same_name_never_overwritten(self):
        self.apply();self.rows[0]['values'][0]['valueType']=1;self.calls=[]
        with self.assertRaises(BridgeError):self.apply()
        self.assertNotIn('POST',self.calls)
    def test_stale_and_concurrent_catalog_rejected(self):
        with self.assertRaises(BridgeError):self.apply(sha='0'*64)
        self.reads=0;self.conflict=True
        with self.assertRaises(BridgeError):self.apply()
        self.assertNotIn('POST',self.calls)
    def test_invalid_names_before_network(self):
        for name in ['system_secret','bad.name','9bad','a'*65]:
            with self.assertRaises(BridgeError):self.apply(name)
        self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
