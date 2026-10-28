"""Inspect and prepare native mall UI bundles without purchasing or publishing."""
import json
from pathlib import Path
import re

from .core import BridgeError, positive_id
from .mall import MallUIService
from .scene import source_hash


ROLES = ('home', 'detail', 'detail_repeat', 'detail_permanent', 'lock')
ASSETS = ('background', 'vignette', 'paper', 'card', 'seal', 'button', 'hover', 'accent')
COLORS = dict(title='#E8E0D2', muted='#ABA5A0', red='#C96970', ink='#302A26', inkRed='#7B3534')


def lua(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return '{' + ','.join('['+lua(key)+']='+lua(item) for key,item in value.items()) + '}'
    raise BridgeError('商城设计配置仅支持字符串和对象')


def validate_design(bindings, theme):
    if not isinstance(bindings, dict) or set(bindings) != set(ROLES):
        raise BridgeError('bindings 必须包含 home、detail、detail_repeat、detail_permanent、lock 五个 UI ID')
    for value in bindings.values():
        MallUIService.target(1, 1, value)
    if len(set(bindings.values())) != len(ROLES):
        raise BridgeError('五个商城 UI 必须使用不同 ID')
    if not isinstance(theme, dict) or set(theme) - {'title','subtitle','currency','font','assets','colors','descriptions'}:
        raise BridgeError('不支持的商城主题字段')
    result = dict(title='物资室', subtitle='医院物资档案', currency='金马尼', font='NotoSansSC-Regular SDF')
    for key in result:
        value = theme.get(key, result[key])
        limit = 24 if key == 'title' else 80
        if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c)<32 for c in value):
            raise BridgeError(f'主题 {key} 须为非空、{limit} 字以内的单行文本')
        result[key] = value
    assets = theme.get('assets')
    if not isinstance(assets, dict) or set(assets) != set(ASSETS):
        raise BridgeError('必须提供 background/vignette/paper/card/seal/button/hover/accent 八个素材 ID')
    if any(not isinstance(v,str) or not re.fullmatch(r'\$[A-Za-z0-9_-]{1,100}',v) for v in assets.values()):
        raise BridgeError('素材引用必须为 $ 开头的素材 ID，不接受 URL 或脚本')
    colors = theme.get('colors', {})
    if not isinstance(colors,dict) or set(colors)-set(COLORS) or any(not isinstance(v,str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}',v) for v in colors.values()):
        raise BridgeError('颜色仅支持 title/muted/red/ink/inkRed 的 #RRGGBB')
    descriptions = theme.get('descriptions', {})
    if not isinstance(descriptions,dict) or len(descriptions)>500:
        raise BridgeError('descriptions 必须为按商品 ID 索引的对象，最多 500 项')
    for key, value in descriptions.items():
        if not isinstance(key,str) or not re.fullmatch(r'[1-9]\d{0,9}',key) or not isinstance(value,dict) or set(value)!={'description'}:
            raise BridgeError('商品说明格式应为 {"商品ID":{"description":"说明"}}')
        desc=value['description']
        if not isinstance(desc,str) or not desc.strip() or len(desc)>140 or any(ord(c)<32 and c!='\n' for c in desc):
            raise BridgeError('商品说明须为 1–140 字文本')
    return result, assets, {**COLORS, **colors}, descriptions


def compile_design(bindings, theme):
    config,assets,colors,descriptions = validate_design(bindings,theme)
    root=Path(__file__).parent
    common=(root/'mall_common.lua').read_text(encoding='utf-8')
    result={}
    for role in ROLES:
        filename = 'mall_home.lua' if role=='home' else ('mall_lock.lua' if role=='lock' else 'mall_detail.lua')
        header='-- Generated native mall design. Products are read at runtime.\n'
        for name,value in [('role',role),('theme',config),('assets',assets),('colors',colors),('bindings',bindings),('descriptions',descriptions)]:
            header+='local '+name+'='+lua(value)+'\n'
        result[role]=header+common+'\n'+(root/filename).read_text(encoding='utf-8')
    return result


class MallDesignService:
    def __init__(self, bridge):
        self.bridge=bridge

    def overview(self, game_id):
        game_id=positive_id(game_id)
        rows=self.bridge.read('ui_catalog',game_id)['response']['data']
        goods=self.bridge.read('goods',game_id)['response']['data']
        if not isinstance(rows,list) or not isinstance(goods,list):
            raise BridgeError('商城目录或商品列表格式异常')
        pages=[]
        for row in rows:
            if row.get('type')!=12 or row.get('mode')!=8:continue
            try:
                members=json.loads(row['command']) if isinstance(row['command'],str) else row['command']
                if not isinstance(members,list):raise ValueError()
                for member in members:
                    source=MallUIService(self.bridge).read_ui(game_id,row['id'],member['id'])
                    code=source['response']['data'].get('command') or ''
                    pages.append(dict(catalog_id=row['id'],ui_id=member['id'],name=source['name'],
                                      sha256=source['sha256'],characters=len(code),
                                      native_product_status='ac.getMallProductStatus' in code,
                                      static_product_list_hint=bool(re.search(r'goodsList\s*=\s*\{',code))))
            except (KeyError, TypeError, ValueError):
                raise BridgeError('商城 UI 目录结构异常') from None
        return dict(game_id=game_id,source='live_api',goods_count=len(goods),pages=pages,
                    note='模板静态商品列表仅为文本线索；需检查源码。商品数为后台配置，不是玩家背包。')

    def build(self, game_id, catalog_id, bindings, theme, output_directory):
        positive_id(game_id);positive_id(catalog_id)
        sources=compile_design(bindings,theme)
        folder=Path(output_directory).expanduser().resolve()
        if folder.exists():raise BridgeError('输出目录已存在，请指定新的目录；不会覆盖已有设计')
        goods=self.bridge.read('goods',game_id)['response']['data']
        if not isinstance(goods,list):raise BridgeError('商品列表格式异常')
        known={str(item.get('id')) for item in goods if isinstance(item,dict)}
        if set(theme.get('descriptions',{}))-known:
            raise BridgeError('说明引用了不属于本作品的商品 ID')
        service=MallUIService(self.bridge)
        pages=[]
        for role in ROLES:
            before=service.read_ui(game_id,catalog_id,bindings[role])
            pages.append(dict(role=role,ui_id=bindings[role],name=before['name'],
                              script_path=str(folder/(role+'.lua')),expected_sha256=before['sha256'],
                              generated_sha256=source_hash(sources[role])))
        manifest=dict(game_id=game_id,catalog_id=catalog_id,pages=pages,goods_count=len(goods),
                      applied=False, native_runtime_verified=False,
                      note='仅生成本地 Lua；使用 apply-mall-ui 逐页保存，使用 expected_sha256 防止覆盖并发修改。没有创建商品、支付或发布。')
        folder.mkdir(parents=True,exist_ok=False)
        for role,source in sources.items():(folder/(role+'.lua')).write_text(source,encoding='utf-8')
        (folder/'theme.json').write_text(json.dumps(theme,ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        return manifest
