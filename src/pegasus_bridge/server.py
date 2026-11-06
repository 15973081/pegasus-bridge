from typing import Annotated,Literal,Union
import logging
from pydantic import BaseModel,ConfigDict,Field,StrictInt,field_validator
from mcp.server.fastmcp import FastMCP
from .core import Pegasus
from .ui import UIService
from .scene import SceneService
from .chapters import ChapterService
from .storytree import StoryTreeService
from .mall import MallUIService, GoodsService
from .editor import EditorService
from .player import PlayerService
from .mall_design import MallDesignService


class _PreviewEvent(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
    generation:str=Field(min_length=1,max_length=100)
    sequence:int=Field(ge=1,le=9007199254740991)


class _PreviewPointer(_PreviewEvent):
    kind:Literal['pointer']
    phase:Literal['down','move','up','cancel']
    id:int=Field(ge=0,le=2147483647)
    pointer_type:Literal['mouse','touch','pen']
    x:float=Field(ge=0,le=1920)
    y:float=Field(ge=0,le=1080)


class _PreviewHover(_PreviewEvent):
    kind:Literal['hover']
    phase:Literal['enter','leave']
    name:str=Field(min_length=1,max_length=256)
    button_generation:int=Field(ge=1)


class _PreviewWheel(_PreviewEvent):
    kind:Literal['wheel']
    x:float=Field(ge=0,le=1920)
    y:float=Field(ge=0,le=1080)
    deltaX:float=Field(ge=-100000,le=100000)
    deltaY:float=Field(ge=-100000,le=100000)
    deltaMode:Literal[0,1,2]

    @field_validator('deltaMode',mode='before')
    @classmethod
    def exact_integer_mode(cls,value):
        if type(value) is not int:raise ValueError('deltaMode must be an integer, not bool')
        return value


class _PreviewViewport(_PreviewEvent):
    kind:Literal['viewport']
    left:float=Field(ge=-100000,le=100000)
    top:float=Field(ge=-100000,le=100000)
    width:float=Field(ge=1,le=32768)
    height:float=Field(ge=1,le=32768)
    scale:float=Field(ge=.0001,le=100)
    pixelRatio:float=Field(ge=.1,le=16)
    canvasWidth:float=Field(ge=100,le=4096)
    canvasHeight:float=Field(ge=100,le=4096)
    clientWidth:float=Field(ge=1,le=32768)
    clientHeight:float=Field(ge=1,le=32768)


PreviewInputEvent=Annotated[Union[_PreviewPointer,_PreviewHover,_PreviewWheel,_PreviewViewport],Field(discriminator='kind')]


def create_server(bridge: Pegasus | None = None):
    # FastMCP enables INFO logging; HTTP client request URLs contain csrf_token.
    # Do not let transport diagnostics expose authenticated query strings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app = FastMCP("pegasus-editor", instructions="PegasusEditor 适配层。支持配置、原生本地存档、显式上报的玩家与背包状态读取，以及带备份的 UI、剧情、章节、故事线、商城编辑和原生命名草稿创建/列表读取。草稿基于后台已保存内容，不提交编辑器内存中尚未同步的修改。在线操作需授权及有效登录，不支持发布。定位作品优先 list_games/resolve_game，通过后台列表遍历，不需要逐个打开作品。recent_games 仅是历史日志；player_state/inventory 必须检查 status/fresh，非实时内存查询，无上报不能当空背包。")
    bridge = bridge or Pegasus()
    readonly = {"readOnlyHint": True, "destructiveHint": False}

    @app.tool(annotations={**readonly, "openWorldHint": True})
    def pegasus_get_orientation(game_id: int) -> dict:
        """读取两处后台方向、并发哈希、逻辑画布尺寸与方向版本创建能力。1=横版，2=竖版；mode=8 是天马编辑器。已有作品原地切换不受支持；不等于原生界面验证。"""
        from .orientation import OrientationService
        return OrientationService(bridge).get_orientation(game_id)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def pegasus_set_orientation(game_id: int, orientation: Literal['horizontal', 'vertical'],
                                expected_sha256: str, checkpoint: str) -> dict:
        """兼容旧版方向请求和检查点。已有作品方向不同时返回 unsupported，不再向后台发送修改；相同方向或旧检查点仅读回核验，并写本地检查点。创建新方向版本请用 create_orientation_variant。"""
        from .orientation import OrientationService
        return OrientationService(bridge).set_orientation(game_id, orientation, expected_sha256, checkpoint)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                           "idempotentHint": False, "openWorldHint": True})
    def pegasus_create_orientation_variant(source_game_id: int, name: str,
                                           orientation: Literal['horizontal', 'vertical'],
                                           expected_sha256: str, checkpoint: str) -> dict:
        """根据源作品基本信息创建独立横/竖版空白工程，mode 固定 8、模板 28/32，不修改原作或参加比赛。先 get_orientation 获取源哈希。检查点保存新 ID、初始化状态及双重读回；不确定请求不重发，同名作品不自动接管。verified 仅表示新方向工程已核验，素材/剧情/UI 未复制，迁移状态另列；不发布。"""
        from .orientation_variant import OrientationVariantService
        return OrientationVariantService(bridge).create_variant(
            source_game_id, name, orientation, expected_sha256, checkpoint)

    from .preview import PreviewService
    preview = PreviewService()

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    def pegasus_preview_start(manifest_path: str) -> dict:
        """启动本机 Lua 浏览器预览，返回 URL/session_id/revision。配置 version=1，script、assets及video_assets均为配置目录内相对路径，variables为初值。MP4流式播放并回传真实时间/结束事件；instruction_limit可配置100000–200000000，默认5000000。需preview依赖；非Unity，不写天马存档，两小时闲置停止，MCP宿主清理子进程也可能结束预览。"""
        return preview.start(manifest_path)

    @app.tool(annotations={**readonly, "openWorldHint": False})
    def pegasus_preview_state(session_id: str) -> dict:
        """读取本地预览控件、变量、音频请求、原生界面请求及 revision。不是天马玩家状态。"""
        return preview.state(session_id)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    def pegasus_preview_click(session_id: str, name: str, expected_revision: int) -> dict:
        """按控件 name 执行本地 Lua 点击；expected_revision 必须匹配最近 state，拒绝过期点击。不操作鼠标键盘或原生天马。"""
        return preview.click(session_id, name, expected_revision)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    def pegasus_preview_key(session_id: str,
                            key: Literal['ArrowUp', 'ArrowDown', 'Enter', 'Escape', ' ', '1', '2', '3', '4', '5'],
                            expected_revision: int, repeat: bool = False) -> dict:
        """向指定本地预览发送规范按键和 repeat；expected_revision 必须匹配最近 state。Lua 通过 pegasusBindPreviewKeys(fn) 注册 fn(key,repeat)，后绑定优先，返回真停止派发；返回的解绑函数可重复调用，最多64项。仅浏览器近似预览，不控制 Windows 或原生天马键盘。"""
        return preview.key(session_id, key, expected_revision, repeat)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    def pegasus_preview_input(session_id: str, event: PreviewInputEvent, expected_revision: StrictInt) -> dict:
        """发送严格有界的本地 raw pointer/hover/wheel/viewport。event 必须含 kind、generation=观测时 state.input_generation、sequence=最近 state.input_sequence+1；expected_revision 匹配 state。pointer: phase(down/move/up/cancel)、id、pointer_type(mouse/touch/pen)、x(0..1920)、y(0..1080)，须 down 序列；hover: phase(enter/leave)、name、button_generation=objects[name].preview_generation；wheel: x(0..1920)/y(0..1080，事件发生点)、deltaX/deltaY/deltaMode；viewport: left/top/width/height/scale/pixelRatio/canvasWidth/canvasHeight/clientWidth/clientHeight。拒绝额外字段、非有限数、重放和旧控件/modal。Lua pegasusBindPreviewInput(fn(event)) 与 pegasusBindPreviewFrames(fn(seconds,frame)) 各64项，返回取消函数；帧由真实服务时钟泵驱动。pegasusReadPreviewViewport() 测量前 nil，pegasusPreviewNow() 单调秒。不操作 Windows/原生输入，不将 ac click 当手势。"""
        return preview.input(session_id, event.model_dump() if isinstance(event,BaseModel) else event, expected_revision)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    def pegasus_preview_reset(session_id: str, expected_revision: int) -> dict:
        """清空指定预览会话进度，用启动时冻结的脚本和初始变量重启；不影响天马存档。"""
        return preview.reset(session_id, expected_revision)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    def pegasus_preview_stop(session_id: str) -> dict:
        """停止指定本地预览服务并丢弃该会话进度。关闭浏览器标签页本身不会停止服务。"""
        return preview.stop(session_id)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def pegasus_ensure_checkpoint_variable(game_id: int, name: str, expected_sha256: str, backup_directory: str) -> dict:
        """注册或复用普通字符串进度变量，供 Lua ac.var[name] 和原生存档使用。哈希为完整 variables data 的 digest；不修改同名定义或玩家进度。先备份并检查并发，创建后读回。"""
        from .variables import VariableService
        return VariableService(bridge).ensure_checkpoint(game_id,name,expected_sha256,backup_directory)

    @app.tool(annotations=readonly)
    def pegasus_mall_overview(game_id: int) -> dict:
        """检查商城 UI ID、名称、源码哈希、原生商品读取和模板静态商品线索；返回后台商品数，不是玩家库存。"""
        return MallDesignService(bridge).overview(game_id)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def pegasus_build_mall_ui(game_id: int, catalog_id: int, bindings: dict, theme: dict, output_directory: str) -> dict:
        """生成医院恐怖风商城首页、三类详情与剧情锁 Lua。bindings 必须指定 home/detail/detail_repeat/detail_permanent/lock 的真实 UI ID；theme 支持 title/subtitle/currency/font/assets/colors/descriptions，assets 含 background/vignette/paper/card/seal/button/hover/accent。读取真实归属及旧哈希，仅写全新本地目录；用 apply_mall_ui 逐页保存，不购买或创建商品。"""
        return MallDesignService(bridge).build(game_id,catalog_id,bindings,theme,output_directory)

    @app.tool(annotations=readonly)
    def pegasus_player_state(game_id: int, max_age_seconds: int = 30) -> dict:
        """读取已接入 runtime_observer.lua 的作品最近上报的节点、章节、进度标记与物品。非实时内存查询；返回时间、新鲜度及身份未绑定说明。无上报返回 unavailable，不猜测玩家状态。"""
        return PlayerService(bridge).player_state(game_id, max_age_seconds)

    @app.tool(annotations=readonly)
    def pegasus_inventory(game_id: int, max_age_seconds: int = 30) -> dict:
        """读取作品上报的背包物品 ID、名称、数量、类别与来源，附新鲜度。无数据 items=null，不是空背包；已购商品不是背包余量。不修改物品。"""
        return PlayerService(bridge).inventory(game_id, max_age_seconds)

    @app.tool(annotations=readonly)
    def pegasus_list_archives(game_id: int, mode: Literal['Editor', 'Runtime'] = 'Editor', user_id: int | None = None) -> dict:
        """列出本机目标作品 Local 存档；Editor 为编辑器测试档，Runtime 为运行端本地档。user_id 显式选择身份目录，省略不自动选择他人。不读取云端缓存充当当前云档。"""
        from .archives import ArchiveService
        return ArchiveService(bridge).list_archives(game_id, mode, user_id)

    @app.tool(annotations=readonly)
    def pegasus_read_archive(game_id: int, slot: int, mode: Literal['Editor', 'Runtime'] = 'Editor', user_id: int | None = None) -> dict:
        """只读指定本地存档的场景、变量和数组快照；不是当前玩家实时值，不执行存档脚本。只返回白名单字段并校验作品/身份及路径。"""
        from .archives import ArchiveService
        return ArchiveService(bridge).read_archive(game_id, slot, mode, user_id)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def pegasus_open_game(game_id: int, wait_seconds: int = 3) -> dict:
        """通过天马原生协议打开指定作品，不操作鼠标键盘。需已登录且有作品读取权限；发送成功不代表就绪，已有作品可能触发原生未保存确认。不启动试玩。"""
        return EditorService(bridge).open_game(game_id, wait_seconds)

    @app.tool(annotations=readonly)
    def pegasus_editor_state(since: dict | None = None) -> dict:
        """读取原生进程和管道可用性；since 为上次返回的 observation.cursor 或 open_game.cursor，只观察此后的脱敏加载事件。首次调用不回放旧日志。不提供当前页面或实时游戏变量。"""
        return EditorService(bridge).state(since)

    @app.tool(annotations=readonly)
    def pegasus_game_state(game_id: int, scene_id: int | None = None, include_ui: bool = False) -> dict:
        """汇总已保存的作品、章节、变量定义、数组与商品；可选 UI、指定场景的剧情和故事线。返回逐资源读取时间/哈希/错误；非玩家实时状态，不包含未保存编辑。"""
        return EditorService(bridge).game_state(game_id, scene_id, include_ui)

    @app.tool(annotations=readonly)
    def pegasus_status() -> dict:
        """检查安装、版本、进程及认证配置状态；不返回凭据。"""
        return bridge.status()

    @app.tool(annotations={"destructiveHint": False})
    def pegasus_launch() -> dict:
        """启动 Windows PegasusEditor；登录仍由用户在编辑器中完成。"""
        return bridge.launch()

    @app.tool(annotations=readonly)
    def pegasus_recent_games() -> dict:
        """列出本地历史日志中出现的作品和场景 ID，非完整作品列表。查找作品请优先 list_games/resolve_game。"""
        return bridge.recent_games()

    @app.tool(annotations=readonly)
    def pegasus_routes() -> dict:
        """列出本地日志实际出现的接口路径，不返回查询参数和凭据。"""
        return bridge.routes()

    @app.tool(annotations=readonly)
    def pegasus_logs(lines: int = 60) -> dict:
        """读取最多 500 行脱敏日志。"""
        return bridge.logs(lines)

    @app.tool(annotations=readonly)
    def pegasus_command_catalog(game_id: int) -> dict:
        """读取本地缓存的指令分类；不等同于作品中的剧情指令。"""
        return bridge.command_catalog(game_id)

    @app.tool(annotations=readonly)
    def pegasus_read(resource: Literal["game", "scenes", "commands", "variables", "arrays", "ui_catalog", "ui_template", "story_tree", "mall_ui", "goods"], game_id: int, scene_id: int | None = None, catalog_id: int | None = None, ui_id: str | None = None) -> dict:
        """从后台读取作品/场景/指令/变量/故事线；commands/story_tree须scene_id，mall_ui须catalog_id/ui_id。commands返回精确source_sha256供版本校验；source_redacted=true时展示源码已脱敏，勿按展示文本计算哈希。goods返回商品列表。"""
        return bridge.read(resource, game_id, scene_id, catalog_id, ui_id)

    @app.tool(annotations={"destructiveHint": False})
    def pegasus_export(resource: Literal["game", "scenes", "commands", "variables", "arrays", "ui_catalog", "ui_template", "story_tree", "mall_ui", "goods", "catalog"], game_id: int, output: str, scene_id: int | None = None, catalog_id: int | None = None, ui_id: str | None = None) -> dict:
        """导出一个资源到本地新 JSON 文件；已有文件不会覆盖。catalog 可离线导出。"""
        return bridge.export(resource, game_id, output, scene_id, catalog_id, ui_id)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def pegasus_upload_image(game_id: int, image_path: str, alias: str, category_id: Literal[1, 2], checkpoint: str) -> dict:
        """经用户授权，将一个本地 PNG 上传到指定作品；1=背景、2=组件。独立检查点可避免重复上传。"""
        return UIService(bridge).upload_image(game_id, image_path, alias, category_id, checkpoint)

    @app.tool(annotations={**readonly, "openWorldHint": True})
    def pegasus_image_catalog(game_id: int, category_id: Literal[1, 2] = 2) -> dict:
        """读取作品背景或组件图片分类。得到 catalog_id 后可用 pegasus_video_catalog 分页核对登记结果。"""
        return UIService(bridge).image_catalog(game_id, category_id)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def pegasus_upload_video(game_id: int, video_path: str, alias: str, checkpoint: str) -> dict:
        """将本地 H.264 MP4 和封面导入作品原生视频库；需 ffprobe/ffmpeg。持久检查点支持续传；注册不确定时先核对，不自动重发。"""
        from .video import VideoService
        return VideoService(bridge).upload_video(game_id,video_path,alias,checkpoint)

    @app.tool(annotations={**readonly, "openWorldHint": True})
    def pegasus_video_catalog(game_id: int, catalog_id: int | None = None, offset: int = 0, limit: int = 100) -> dict:
        """读取作品原生视频分类；指定 catalog_id 时分页读取分类内容。不是发布或导出视频。"""
        from .video import VideoService
        return VideoService(bridge).catalog(game_id,catalog_id,offset,limit)

    @app.tool(annotations={**readonly, "openWorldHint": True})
    def pegasus_video_upload_status(game_id: int, check_urls: list[str] | None = None) -> dict:
        """诊断原生上传渠道（不返回令牌）。传check_urls时仅匿名检测最多10个固定素材域URL，不申请上传令牌。均不上传、不注册素材，不宣称额度或审核拒绝原因。"""
        from .video import VideoService
        return VideoService(bridge).upload_status(game_id, check_urls)

    @app.tool(annotations={**readonly, "openWorldHint": True})
    def pegasus_native_config_status(game_id: int, scene_id: int | None = None, check_material_ids: dict[str, list[int]] | None = None) -> dict:
        """调用编辑器原生config构建GET并返回有界错误摘要，检查远端命令编译兼容性。不同于标准Lua语法校验和源码读回；不发布，不返回配置正文或访问令牌。"""
        from .scene import SceneService
        return SceneService(bridge).native_config_status(game_id, scene_id, check_material_ids)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def pegasus_cache_materials(game_id: int, manifest_path: str, checkpoint: str) -> dict:
        """补齐原生素材导入的本机预览缓存：清单checkpoints列已登记的上传检查点。核对自有素材目录的ID/URL/字节数与本地SHA，备份缓存冲突并原子写入原生路径。不会改变云端审核或访问状态。"""
        from .material_cache import MaterialCacheService
        return MaterialCacheService(bridge).cache(game_id,manifest_path,checkpoint)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    def pegasus_apply_ui_template(game_id: int, section: Literal['cover', 'configuration', 'record', 'playback', 'menu'], template: dict, expected_sha256: str, backup_directory: str) -> dict:
        """经用户授权更新一个基本 UI 分类，自动备份并读回验证。expected_sha256 是旧分类对象排序、紧凑 JSON（ensure_ascii=False）的 UTF-8 SHA256。后台无原子 CAS；编辑器应避免同时保存。可用备份 before[section] 和当前哈希进行显式恢复。"""
        return UIService(bridge).apply_template(game_id, section, template, expected_sha256, backup_directory)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    def pegasus_apply_scene(game_id: int, scene_id: int, script_path: str, expected_sha256: str, backup_directory: str) -> dict:
        """经授权替换一段剧情，自动备份、比对旧源码 UTF-8 SHA256 并读回验证。后台没有原子 CAS，请避免同时保存；本工具不发布作品，不保证编辑器 Lua 兼容性。"""
        return SceneService(bridge).apply_scene(game_id, scene_id, script_path, expected_sha256, backup_directory)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def pegasus_ensure_chapter(game_id: int, chapter_name: str, scene_name: str, backup_directory: str, chapter_id: int | None = None, scene_id: int | None = None) -> dict:
        """经授权建立章节及一个场景。按名称复用，指定现有 ID 可重命名；不删除剧情。先备份结构、检查并发、读回验证。请求失败先重新读取，避免重复创建。"""
        return ChapterService(bridge).ensure(game_id,chapter_name,scene_name,backup_directory,chapter_id,scene_id)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    def pegasus_apply_story_tree(game_id: int, scene_id: int, tree: dict, expected_sha256: str, backup_directory: str) -> dict:
        """更新场景原生故事线节点/封面/连线，先校验目标、唯一标识、连线与无环布局，再备份、检查并发及读回。独立于剧情源码；不保证原生节点支持自定义 Lua 精确跳转。哈希规则与 apply_ui_template 相同。"""
        return StoryTreeService(bridge).apply(game_id,scene_id,tree,expected_sha256,backup_directory)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    def pegasus_apply_mall_ui(game_id: int, catalog_id: int, ui_id: str, script_path: str, expected_sha256: str, backup_directory: str) -> dict:
        """经授权替换商城首页/商品详情/剧情锁 UI 源码。校验作品目录归属，备份、UTF-16 指令更新、并发检查和读回；哈希为旧源码 UTF-8 SHA256。不是商品数据修改，不保证 Lua 运行兼容性。"""
        return MallUIService(bridge).apply_ui(game_id,catalog_id,ui_id,script_path,expected_sha256,backup_directory)

    @app.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    def pegasus_apply_goods(game_id: int, goods_id: int, changes: dict, expected_sha256: str, backup_directory: str) -> dict:
        """经授权修改已存在商品的 name、price、payType。保留商品绑定和权益，备份、并发检查、读回；哈希为 goods 列表中该完整商品对象的 digest。价格单位沿用后台。说明在商品详情 UI 中编辑，不下单、不创建商品或发布作品。"""
        return GoodsService(bridge).apply(game_id,goods_id,changes,expected_sha256,backup_directory)

    from .workflow_tools import register
    register(app, bridge)
    return app


def main():
    import argparse
    from importlib.metadata import version
    parser = argparse.ArgumentParser(description='PegasusEditor MCP Server (stdio)')
    parser.add_argument('--version', action='version', version='pegasus-mcp ' + version('pegasus-bridge'))
    parser.parse_args()
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
