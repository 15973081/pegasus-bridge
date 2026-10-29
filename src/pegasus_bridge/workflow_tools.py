"""MCP bindings for shared authoring, verification and deployment services."""
from .authoring import AuthoringService, build_scene
from .audio import AudioService
from .batch import BatchService
from .drafts import DraftService
from .project import project_check, project_pack
from .qa import test_route, preview_report
from .validation import validate_scene


def register(app,bridge):
    ro={'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}
    local={'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False}
    edit={'readOnlyHint':False,'destructiveHint':True,'openWorldHint':True}
    author=AuthoringService(bridge);batch=BatchService(bridge)

    @app.tool(annotations={**ro,'openWorldHint':True})
    def pegasus_list_drafts(game_id:int)->dict:
        """读取天马编辑器“打开/保存”窗口使用的原生命名备份列表。来源为后台已保存内容，不是玩家存档或本地剧情备份。"""
        return DraftService(bridge).list_drafts(game_id)

    @app.tool(annotations={**local,'openWorldHint':True,'idempotentHint':False})
    def pegasus_save_draft(game_id:int,name:str,checkpoint:str)->dict:
        """为后台已保存作品创建原生命名草稿，等同保存窗口的新增备份操作。checkpoint 记录请求并支持结果不确定时核对；服务端无幂等键，不可盲目换检查点重试。不提交编辑器内存中尚未同步的修改，不发布、不覆盖或恢复旧备份。"""
        return DraftService(bridge).save_draft(game_id,name,checkpoint)

    @app.tool(annotations=local)
    def pegasus_authoring_init(manifest_path:str,game_id:int)->dict:
        """创建新的角色/音频配置，不覆盖旧文件，不修改云端。"""
        return author.init(manifest_path,game_id)

    @app.tool(annotations=ro)
    def pegasus_authoring_read(manifest_path:str)->dict:
        """读取角色表情、节点布局、音频绑定及本地配置哈希。"""
        return author.read(manifest_path)

    @app.tool(annotations=edit)
    def pegasus_character_add(manifest_path:str,game_id:int,character_id:str,name:str,expressions:dict,expected_sha256:str|None=None)->dict:
        """上传现有 PNG 并注册角色/表情。expressions 为表情名到本地 PNG 路径映射；已有配置须提供 authoring_read 的哈希。支持断点复用，不生成图片；完成后 build_scene/apply-scene。"""
        return author.character_add(manifest_path,game_id,character_id,name,expressions,expected_sha256)

    @app.tool(annotations={**local,'destructiveHint':True})
    def pegasus_character_place(manifest_path:str,node_id:str,character_id:str,expression:str,layout:dict,expected_sha256:str)->dict:
        """在本地规范化配置设置节点立绘；layout 支持 x/y/width/height/index/inlayer/visible。每节点最多四人，visible=false 隐藏；不会自动识别任意 Lua。"""
        return author.character_place(manifest_path,node_id,character_id,expression,layout,expected_sha256)

    @app.tool(annotations=local)
    def pegasus_build_scene(script_path:str,manifest_path:str,output_path:str,node_variable:str='key',hook_anchor:str='-- PEGASUS-NODE-HOOK')->dict:
        """将角色与音频模块插入显式的唯一节点钩子，生成新 Lua 并校验。钩子应在节点画面完成后、node_variable 可见的作用域；不会覆盖文件或写云端。"""
        return build_scene(script_path,manifest_path,output_path,node_variable,hook_anchor)

    @app.tool(annotations=ro)
    def pegasus_validate_scene(script_path:str,material_ids:list[str]|None=None)->dict:
        """静态检查 Lua 语法、已知匿名回调问题、控件名复用与可选素材映射。动态引用和 Unity 运行仍需另验。"""
        return validate_scene(script_path,material_ids)

    @app.tool(annotations={**local,'openWorldHint':True})
    def pegasus_batch_plan(manifest_path:str,output_directory:str)->dict:
        """读取目标场景，冻结 before/target/hash/diff 到新目录；不写云端。输入含 game_id 和 scenes[{scene_id,script_path}]，路径相对输入清单。"""
        return batch.plan(manifest_path,output_directory)

    @app.tool(annotations=edit)
    def pegasus_batch_apply(plan_path:str)->dict:
        """执行冻结计划；先核对所有远端版本，再逐章备份、保存、读回。失败记录 uncertain，不盲目重试；不是原子事务。"""
        return batch.apply(plan_path)

    @app.tool(annotations=edit)
    def pegasus_batch_resume(plan_path:str)->dict:
        """续跑计划前先重读每章：目标已一致则跳过，原版本则保存，其他版本拒绝覆盖。支持写入成功但回包丢失后的核对。"""
        return batch.resume(plan_path)

    @app.tool(annotations=ro)
    def pegasus_history(backup_directory:str,game_id:int|None=None)->dict:
        """列出指定目录的剧情备份与前后哈希；不把本地备份当作后台完整历史。"""
        return batch.history(backup_directory,game_id)

    @app.tool(annotations=ro)
    def pegasus_diff(backup_path:str)->dict:
        """比较剧情备份中的 before/requested，返回最多 50000 字符差异。"""
        return batch.diff(backup_path)

    @app.tool(annotations=edit)
    def pegasus_restore(backup_path:str,expected_sha256:str,backup_directory:str)->dict:
        """将剧情恢复到指定备份 before；必须提供当前远端源码哈希，并再次备份。可恢复原空场景，不盲目覆盖新修改。"""
        return batch.restore(backup_path,expected_sha256,backup_directory)

    @app.tool(annotations={**local,'openWorldHint':True})
    def pegasus_upload_audio(game_id:int,audio_path:str,alias:str,checkpoint:str)->dict:
        """上传 32 MiB 以内 PCM WAV 到作品音频素材库，检查格式/时长并记录断点。注册结果不确定时拒绝自动重复注册；不生成配音。"""
        return AudioService(bridge).upload_audio(game_id,audio_path,alias,checkpoint)

    @app.tool(annotations={**local,'destructiveHint':True})
    def pegasus_audio_bind(manifest_path:str,node_id:str,name:str,res_id:str,loop:bool,volume:float,expected_sha256:str)->dict:
        """本地配置节点音频与 0–100 音量；res_id 为已上传的 $数字 ID。节点钩子会停止上个节点由本模块播放的音频；随后 build_scene 生成。"""
        return author.audio_bind(manifest_path,node_id,name,res_id,loop,volume,expected_sha256)

    @app.tool(annotations=local)
    def pegasus_test_route(manifest_path:str,route_path:str,output_directory:str)->dict:
        """使用 preview 清单执行本地 Lua 点击路线，输出断言与逐步快照。路线 version=1、initial、steps[{click,expect}]；不写玩家存档，不是原生试玩。"""
        return test_route(manifest_path,route_path,output_directory)

    @app.tool(annotations=local)
    def pegasus_preview_report(manifest_path:str,route_path:str,output_directory:str)->dict:
        """执行路线并用浏览器逐步截图、检查文字溢出。需 qa 可选依赖及 Chromium/Edge；只验证近似渲染。"""
        return preview_report(manifest_path,route_path,output_directory)

    @app.tool(annotations=ro)
    def pegasus_project_check(manifest_path:str)->dict:
        """检查 version=1 工程清单的 scenes[{scene_id,script}]、assets、variables、supporting_files；限制目录范围、检查素材与 Lua，输出哈希表。"""
        return project_check(manifest_path)

    @app.tool(annotations=local)
    def pegasus_project_pack(manifest_path:str,output_path:str)->dict:
        """把显式列出的已验证工程文件打包为新 ZIP，附内容哈希表；不遍历整个目录，不包含凭据配置，不提供导入或发布。"""
        return project_pack(manifest_path,output_path)
