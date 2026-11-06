import json,unittest
from unittest.mock import Mock
import httpx
from pegasus_bridge.scene import SceneService
from pegasus_bridge.core import BridgeError

class NativeConfigTests(unittest.TestCase):
    def make_service(self,body):
        self.calls=[]
        bridge=Mock();bridge._auth.return_value=({'Cookie':'TOKEN=OWN_OLD'}, {'csrf_token':'PORTAL'})
        def handle(request):
            self.calls.append(request)
            return httpx.Response(200,json=body)
        return SceneService(bridge,httpx.MockTransport(handle))

    def test_main_config_uses_native_read_request_without_leaking_config(self):
        service=self.make_service({'state':{'code':200000},'data':{'main':'PRIVATE_SCRIPT'}})
        result=service.native_config_status(42)
        self.assertTrue(result['valid']);self.assertEqual(result['data_shape']['main']['length'],14)
        self.assertNotIn('PRIVATE_SCRIPT',json.dumps(result))
        request=self.calls[0]
        self.assertEqual(str(request.url),'https://avg.163.com/avg-editor-api/game/42/config')
        self.assertEqual(request.headers['cookie'],'TOKEN=OWN_OLD')
        self.assertEqual(request.headers['user-agent'],'3DAvg/1.0 yidun')
        self.assertEqual(request.method,'GET')

    def test_rejection_bounds_diagnostics_and_redacts_credentials(self):
        service=self.make_service({'state':{'code':507034,'message':'命令不正确'},'data':{'data':[{'sourceId':8,'command':'csrf_token=PRIVATE '+('x'*1000)}]*40}})
        result=service.native_config_status(42,8)
        self.assertFalse(result['valid']);self.assertEqual(result['error_count'],40)
        self.assertEqual(len(result['errors']),12)
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertTrue(all(len(r['command'])<=200 for r in result['errors']))
        self.assertTrue(self.calls[0].url.path.endswith('/config/scene/8'))

    def test_dynamic_material_ids_are_checked_without_returning_urls(self):
        service=self.make_service({'state':{'code':200000},'data':{'resource':{
            'image':{'123':{'url':'PRIVATE_URL'},'$124':{'url':'PRIVATE_URL'}},'audio':{},'video':{'56':{}}}}})
        result=service.native_config_status(42,check_material_ids={'image':[123,124],'video':[56]})
        self.assertTrue(result['material_check']['image']['passed'])
        self.assertEqual(result['material_check']['image']['found'],2)
        self.assertEqual(result['resource_inventory']['image']['count'],2)
        self.assertNotIn('PRIVATE_URL',json.dumps(result))

    def test_missing_dynamic_material_is_reported(self):
        service=self.make_service({'state':{'code':200000},'data':{'resource':{'image':{'123':{}}}}})
        result=service.native_config_status(42,check_material_ids={'image':[123,125]})
        self.assertTrue(result['valid'])
        self.assertFalse(result['material_check']['image']['passed'])
        self.assertEqual(result['material_check']['image']['missing'],[125])

    def test_missing_resource_table_is_unavailable_not_empty(self):
        service=self.make_service({'state':{'code':200000},'data':{'commands':'source'}})
        result=service.native_config_status(42,check_material_ids={'image':[123]})
        self.assertIsNone(result['material_check']['image']['missing'])
        self.assertIsNone(result['resource_inventory']['image']['count'])
        self.assertFalse(result['material_check']['image']['passed'])

    def test_bad_material_checks_do_not_send_requests(self):
        service=self.make_service({})
        for check in [{'image':[True]},{'image':[1,1]},{'image':list(range(1,2002))},{'secret':[1]},['image']]:
            with self.assertRaises(BridgeError):service.native_config_status(42,check_material_ids=check)
        self.assertFalse(self.calls)
