import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from pegasus_bridge.core import BridgeError
from pegasus_bridge.mall_design import ASSETS, ROLES, MallDesignService, compile_design

BINDINGS={role:f'00000000-0000-4000-8000-{i:012d}' for i,role in enumerate(ROLES,1)}
THEME={'assets':{key:'$123' for key in ASSETS}}
STUB='''
objects={};styles={};shown={};closed={};locks={};events={};goods={};ac={var={custom_data={}}}
function ac.createLayer(c)objects[c.name]=c end
function ac.createImage(c)objects[c.name]=c end
function ac.createText(c)objects[c.name]=c end
function ac.createButton(c)assert(type(c.click_func)=="function");objects[c.name]=c end
function ac.createBuyButton(c)c.isBuy=true;objects[c.name]=c end
function ac.createStyle(c)styles[c.name]=c end
function ac.closeUI(c)
 closed[#closed+1]=c
 for k,v in pairs(objects)do if k==c.name or v.inlayer==c.name then objects[k]=nil end end
end
function ac.showUI(c)shown[#shown+1]=c end
function ac.getMallProductStatus()return goods end
function ac.createProductLock(c)locks[#locks+1]={action="create",id=c.productId}end
function ac.releaseProductLock(c)locks[#locks+1]={action="release",id=c.productId}end
function ac.quitProductLock(c)locks[#locks+1]={action="quit",id=c.productId}end
function ac.registerEvent(c)events[#events+1]=c end
'''


class DesignTests(unittest.TestCase):
    def test_invalid_assets_bindings_and_descriptions(self):
        for change in ('url','missing','description','duplicates','color'):
            theme=copy.deepcopy(THEME);bindings=dict(BINDINGS)
            if change=='url':theme['assets']['paper']='https://example.com'
            if change=='missing':del bindings['home']
            if change=='description':theme['descriptions']={'12':{'description':'x'*141}}
            if change=='duplicates':bindings['lock']=bindings['home']
            if change=='color':theme['colors']={'ink':'red'}
            with self.subTest(change=change),self.assertRaises(BridgeError):compile_design(bindings,theme)

    def test_build_checks_membership_before_creating_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'new'
            bridge=MagicMock();bridge.read.return_value={'response':{'data':[]}}
            with patch('pegasus_bridge.mall_design.MallUIService.read_ui',side_effect=BridgeError('wrong member')):
                with self.assertRaises(BridgeError):MallDesignService(bridge).build(42,8,BINDINGS,THEME,str(folder))
            self.assertFalse(folder.exists())

    def test_build_new_folder_manifest_and_no_backend_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'new';bridge=MagicMock();bridge.read.return_value={'response':{'data':[]}}
            with patch('pegasus_bridge.mall_design.MallUIService.read_ui',return_value={'name':'商城','sha256':'a'*64}):
                result=MallDesignService(bridge).build(42,8,BINDINGS,THEME,str(folder))
                self.assertFalse(result['applied']);self.assertEqual(len(result['pages']),5)
                for p in result['pages']:self.assertTrue(Path(p['script_path']).is_file())
                with self.assertRaises(BridgeError):MallDesignService(bridge).build(42,8,BINDINGS,THEME,str(folder))

    def test_foreign_goods_description_rejected(self):
        theme={**THEME,'descriptions':{'999':{'description':'not in game'}}}
        bridge=MagicMock();bridge.read.return_value={'response':{'data':[]}}
        with tempfile.TemporaryDirectory() as tmp,self.assertRaises(BridgeError):
            MallDesignService(bridge).build(42,8,BINDINGS,theme,str(Path(tmp)/'new'))


@unittest.skipUnless(importlib.util.find_spec('lupa'),'optional native Lua logic harness')
class LuaDesignTests(unittest.TestCase):
    def game(self,role,products=None,custom=None):
        from lupa import LuaRuntime
        lua=LuaRuntime(unpack_returned_tuples=True);lua.execute(STUB)
        lua.globals().goods=lua.table_from(products or [],recursive=True)
        lua.globals().ac['var']['custom_data']=lua.table_from(custom or {},recursive=True)
        lua.execute(compile_design(BINDINGS,THEME)[role])
        return lua

    def test_empty_home_has_no_template_goods_or_buy_button(self):
        lua=self.game('home');objects=lua.globals().objects
        self.assertEqual(objects['mall404_home_empty_title']['content'],'物资尚未入库')
        self.assertFalse(any(v['isBuy'] for _,v in objects.items()))

    def test_paging_filter_and_detail_routing(self):
        products=[dict(id=i,name='物资'+str(i),price=i,type=(i%3)+1,payType=1)for i in range(1,9)]
        lua=self.game('home',products);o=lua.globals().objects
        o['mall404_home_next']['click_func']()
        self.assertEqual(o['mall404_home_page']['content'],'2 / 2')
        o['mall404_home_item_1_detail']['click_func']()
        self.assertEqual(lua.globals().shown[1]['custom_data']['productId'],7)
        o['mall404_home_category_2']['click_func']()
        self.assertEqual(o['mall404_home_page']['content'],'1 / 1')
        self.assertEqual(o['mall404_home_item_1_name']['content'],'物资3')

    def test_detail_unknown_never_buys_fallback_id(self):
        lua=self.game('detail',custom={'mall_detail':{'productId':999}})
        self.assertIn('不可用',lua.globals().objects['mall404_detail_missing']['content'])
        self.assertFalse(any(v['isBuy'] for _,v in lua.globals().objects.items()))

    def test_detail_owned_and_repeat_purchase_semantics(self):
        product=dict(id=42,name='档案',price=5,type=1,payType=1,isAlreadyBuy=True)
        lua=self.game('detail',[product],{'mall_detail':{'productId':42}})
        self.assertIsNone(lua.globals().objects['mall404_detail_buy'])
        product['payType']=2
        lua=self.game('detail_repeat',[product],{'mall_detail':{'productId':42}})
        self.assertEqual(lua.globals().objects['mall404_detail_repeat_buy']['goodId'],42)
        self.assertEqual(len(lua.globals().shown),0)

    def test_lock_missing_and_unowned_close(self):
        lua=self.game('lock');self.assertEqual(len(lua.globals().locks),0)
        lua=self.game('lock',[],{'plot_lock_dialog_landscape':{'productId':'permit-42','productName':'许可'}})
        self.assertEqual(lua.globals().locks[1]['action'],'create')
        lua.globals().objects['mall404_lock_back']['click_func']()
        self.assertEqual(lua.globals().locks[2]['action'],'release')
        self.assertEqual(lua.globals().locks[3]['action'],'quit')

    def test_quote_injection_is_literal(self):
        theme={**THEME,'title':'"; error("bad") --'}
        from lupa import LuaRuntime
        lua=LuaRuntime();lua.execute(STUB);lua.execute(compile_design(BINDINGS,theme)['home'])
        self.assertEqual(lua.globals().objects['mall404_home_title']['content'],theme['title'])


if __name__=='__main__':unittest.main()
