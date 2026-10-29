"""CLI bindings call exactly the services used by the MCP tools."""
from .workfiles import read_json
from .authoring import AuthoringService,build_scene
from .audio import AudioService
from .batch import BatchService
from .drafts import DraftService
from .validation import validate_scene
from .project import project_check,project_pack
from .qa import test_route,preview_report
from .change_plan import ChangePlanService
from .native_qa import NativeQAService

COMMANDS={'authoring-init','authoring-read','character-add','character-place','build-scene','validate-scene',
          'batch-plan','batch-apply','batch-resume','history','diff','restore','upload-audio','audio-bind',
          'test-route','preview-report','project-check','project-pack','list-drafts','save-draft',
          'change-plan','change-apply','change-reverse-plan','native-qa-begin','native-qa-report'}

def add_arguments(commands):
    def command(name,*positionals,**options):
        p=commands.add_parser(name)
        for key in positionals:p.add_argument(key)
        for key,value in options.items():p.add_argument('--'+key.replace('_','-'),**value)
        return p
    command('authoring-init','manifest_path',game_id=dict(type=int,required=True))
    command('authoring-read','manifest_path')
    command('character-add','manifest_path','character_id','name',game_id=dict(type=int,required=True),expressions_file=dict(required=True),expected_sha256=dict())
    command('character-place','manifest_path','node_id','character_id','expression',layout_file=dict(required=True),expected_sha256=dict(required=True))
    command('build-scene','script_path','manifest_path','output_path',node_variable=dict(default='key'),hook_anchor=dict(default='-- PEGASUS-NODE-HOOK'))
    command('validate-scene','script_path',material_ids_file=dict())
    command('batch-plan','manifest_path','output_directory')
    command('change-plan','manifest_path','output_directory')
    command('change-apply','plan_path')
    command('change-reverse-plan','plan_path','output_directory')
    p=commands.add_parser('native-qa-begin')
    p.add_argument('game_id',type=int)
    p.add_argument('output_directory')
    command('native-qa-report','session_path','observations_path','output_path')
    command('batch-apply','plan_path');command('batch-resume','plan_path')
    command('history','backup_directory',game_id=dict(type=int))
    command('diff','backup_path')
    command('restore','backup_path',expected_sha256=dict(required=True),backup_directory=dict(required=True))
    p=command('upload-audio','game_id','audio_path',alias=dict(required=True),checkpoint=dict(required=True))
    command('audio-bind','manifest_path','node_id','name','res_id',loop=dict(action='store_true'),volume=dict(type=float,default=25),expected_sha256=dict(required=True))
    command('test-route','manifest_path','route_path','output_directory')
    command('preview-report','manifest_path','route_path','output_directory')
    command('project-check','manifest_path');command('project-pack','manifest_path','output_path')
    p=commands.add_parser('list-drafts')
    p.add_argument('game_id',type=int)
    p=commands.add_parser('save-draft')
    p.add_argument('game_id',type=int)
    p.add_argument('name')
    p.add_argument('--checkpoint',required=True)

def run(args,bridge):
    c=args.command;a=args;author=AuthoringService(bridge);batch=BatchService(bridge)
    if c=='change-plan':return ChangePlanService(bridge).plan(a.manifest_path,a.output_directory)
    if c=='change-apply':return ChangePlanService(bridge).apply(a.plan_path)
    if c=='change-reverse-plan':return ChangePlanService(bridge).reverse_plan(a.plan_path,a.output_directory)
    if c=='native-qa-begin':return NativeQAService(bridge).begin(a.game_id,a.output_directory)
    if c=='native-qa-report':return NativeQAService(bridge).report(a.session_path,a.observations_path,a.output_path)
    if c=='authoring-init':return author.init(a.manifest_path,a.game_id)
    if c=='authoring-read':return author.read(a.manifest_path)
    if c=='character-add':return author.character_add(a.manifest_path,a.game_id,a.character_id,a.name,read_json(a.expressions_file),a.expected_sha256)
    if c=='character-place':return author.character_place(a.manifest_path,a.node_id,a.character_id,a.expression,read_json(a.layout_file),a.expected_sha256)
    if c=='build-scene':return build_scene(a.script_path,a.manifest_path,a.output_path,a.node_variable,a.hook_anchor)
    if c=='validate-scene':return validate_scene(a.script_path,read_json(a.material_ids_file) if a.material_ids_file else None)
    if c=='batch-plan':return batch.plan(a.manifest_path,a.output_directory)
    if c=='batch-apply':return batch.apply(a.plan_path)
    if c=='batch-resume':return batch.resume(a.plan_path)
    if c=='history':return batch.history(a.backup_directory,a.game_id)
    if c=='diff':return batch.diff(a.backup_path)
    if c=='restore':return batch.restore(a.backup_path,a.expected_sha256,a.backup_directory)
    if c=='upload-audio':return AudioService(bridge).upload_audio(int(a.game_id),a.audio_path,a.alias,a.checkpoint)
    if c=='audio-bind':return author.audio_bind(a.manifest_path,a.node_id,a.name,a.res_id,a.loop,a.volume,a.expected_sha256)
    if c=='test-route':return test_route(a.manifest_path,a.route_path,a.output_directory)
    if c=='preview-report':return preview_report(a.manifest_path,a.route_path,a.output_directory)
    if c=='project-check':return project_check(a.manifest_path)
    if c=='project-pack':return project_pack(a.manifest_path,a.output_path)
    if c=='list-drafts':return DraftService(bridge).list_drafts(a.game_id)
    if c=='save-draft':return DraftService(bridge).save_draft(a.game_id,a.name,a.checkpoint)
    raise AssertionError(c)
