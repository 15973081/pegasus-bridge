"""Native acceptance evidence; never infer gameplay success from a process."""
import hashlib
import json
from pathlib import Path

from .core import BridgeError, positive_id
from .editor import EditorService, now
from .ui import bounded_errors
from .workfiles import read_json, child, new_text, digest

CHECKS = ('portrait', 'audio', 'chapter_navigation', 'save_load')


class NativeQAService:
    def __init__(self, bridge):
        self.editor = EditorService(bridge)

    @bounded_errors
    def begin(self, game_id, output_directory):
        gid = positive_id(game_id)
        state = self.editor.state()
        snapshot = self.editor.game_state(gid, include_ui=True)
        if not snapshot['complete']:
            raise BridgeError('后台快照读取不完整；请检查登录后重试')
        session = dict(version=1, game_id=gid, started_at=now(), editor=state,
                       resource_hashes={k:v['sha256'] for k,v in snapshot['resources'].items()})
        session['digest'] = digest(session)
        folder = Path(output_directory).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=False)
        new_text(folder/'session.json', json.dumps(session, ensure_ascii=False, indent=2))
        template = dict(session_digest=session['digest'], game_id=gid, reviewer='',
                        checks={k:dict(status='not_tested', note='', evidence=[]) for k in CHECKS})
        new_text(folder/'observations.json', json.dumps(template, ensure_ascii=False, indent=2))
        return dict(session_path=str(folder/'session.json'), observations_path=str(folder/'observations.json'),
                    gameplay_verified=False, next_action='打开目标作品并人工原生试玩；填写观察表，证据文件放在观察表目录内，再生成报告')

    @bounded_errors
    def report(self, session_path, observations_path, output_path):
        session = read_json(session_path)
        if session.get('version') != 1 or digest({k:v for k,v in session.items() if k != 'digest'}) != session.get('digest'):
            raise BridgeError('验收会话校验失败')
        observation_file = Path(observations_path).expanduser().resolve()
        observations = read_json(observation_file)
        if observations.get('session_digest') != session['digest'] or observations.get('game_id') != session['game_id']:
            raise BridgeError('观察表不属于本验收会话')
        reviewer = observations.get('reviewer')
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
            raise BridgeError('必须填写验收人')
        checks = observations.get('checks')
        if not isinstance(checks, dict) or set(checks) != set(CHECKS):
            raise BridgeError('观察表须包含立绘、音频、章节跳转、存读档四项')
        evidence = {}
        for name, check in checks.items():
            if not isinstance(check, dict) or set(check) != {'status','note','evidence'}:
                raise BridgeError('验收项须包含 status/note/evidence')
            if check['status'] not in ('passed','failed','not_tested') or not isinstance(check['note'], str):
                raise BridgeError('验收状态或说明无效')
            files = check['evidence']
            if not isinstance(files, list) or len(files) > 20:
                raise BridgeError('每项最多 20 个证据文件')
            if check['status'] != 'not_tested' and (not check['note'].strip() or not files):
                raise BridgeError('已验收项须填写说明并附证据')
            for filename in files:
                file = child(observation_file.parent, filename)
                if not file.is_file() or file.stat().st_size > 32*1024*1024:
                    raise BridgeError('证据文件不存在或超过 32 MiB')
                evidence[filename] = dict(path=str(file), sha256=hashlib.sha256(file.read_bytes()).hexdigest())
        state = self.editor.state(session['editor']['observation']['cursor'])
        snapshot = self.editor.game_state(session['game_id'], include_ui=True)
        hashes = {k:v['sha256'] for k,v in snapshot['resources'].items()}
        unchanged = snapshot['complete'] and hashes == session['resource_hashes']
        status = 'failed' if any(c['status'] == 'failed' for c in checks.values()) else 'manual_passed' if all(c['status'] == 'passed' for c in checks.values()) else 'incomplete'
        result = dict(version=1, game_id=session['game_id'], session_digest=session['digest'],
                      completed_at=now(), status=status, reviewer=reviewer, checks=checks, evidence=evidence,
                      backend_metadata_unchanged=unchanged, editor=state, automated_gameplay_verified=False,
                      limitations=['通过状态来自人工观察，工具仅校验证据存在及哈希，不验证证据内容',
                                   '后台比较仅覆盖元数据、变量、商品、UI，不覆盖各场景 Lua 或未保存编辑',
                                   '日志仅包含已支持的作品打开事件，不代表完整运行错误扫描'])
        if not unchanged:
            result['status'] = 'needs_review'
        new_text(output_path, json.dumps(result, ensure_ascii=False, indent=2))
        return dict(**result, report_path=str(Path(output_path).resolve()))
