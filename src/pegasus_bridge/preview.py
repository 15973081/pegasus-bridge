"""Local preview lifecycle shared by independent MCP and CLI processes."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from functools import wraps
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import httpx

from .core import BridgeError
from .preview_runtime import PreviewGame, load_bundle


def local_errors(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BridgeError:
            raise
        except (OSError, ValueError, TypeError, httpx.HTTPError):
            raise BridgeError('本地预览不可用；请检查配置、依赖和会话是否仍在运行') from None
    return wrapped


class PreviewService:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.environ.get('PEGASUS_PREVIEW_DIR') or
                              Path.home() / '.pegasus-bridge' / 'previews').resolve()

    def descriptor(self, session_id):
        if not isinstance(session_id, str) or not re.fullmatch('[a-f0-9]{32}', session_id):
            raise BridgeError('预览 session_id 无效')
        return self.directory / (session_id + '.json')

    @local_errors
    def start(self, manifest_path):
        # Validate without launching a process for malformed manifests.
        bundle = load_bundle(manifest_path)
        try:
            import lupa.lua54  # noqa: F401
        except ImportError:
            raise BridgeError('预览需要可选依赖：uv sync --extra preview --inexact') from None
        self.directory.mkdir(parents=True, exist_ok=True)
        session_id = secrets.token_hex(16)
        descriptor = self.descriptor(session_id)
        error_file = descriptor.with_suffix('.error')
        config = descriptor.with_suffix('.request')
        config.write_text(json.dumps({'manifest': str(Path(manifest_path).resolve()),
                                      'sha256': bundle['script_sha256']}), encoding='utf-8')
        kwargs = {}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs['start_new_session'] = True
        proc = subprocess.Popen([sys.executable, '-m', 'pegasus_bridge.preview', str(config)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, **kwargs)
        for _ in range(200):
            if descriptor.exists():
                try:
                    info = self.state(session_id)
                except Exception:
                    proc.terminate()
                    proc.wait(timeout=5)
                    descriptor.unlink(missing_ok=True)
                    raise
                # Reap the detached helper when it exits without blocking MCP.
                threading.Thread(target=proc.wait, daemon=True).start()
                return dict(session_id=session_id, url=self._connection(session_id),
                            revision=info['revision'], script_sha256=info['script_sha256'],
                            lifetime='until stop, two hours idle, or host process-tree cleanup; not a system service',
                            mode=info['mode'], native_runtime=False, limitations=info['limitations'])
            if proc.poll() is not None:
                message = error_file.read_text(encoding='utf-8') if error_file.exists() else '预览子进程启动失败'
                error_file.unlink(missing_ok=True)
                config.unlink(missing_ok=True)
                raise BridgeError(message[:1500])
            time.sleep(.05)
        proc.terminate()  # Only the child just created, never an arbitrary stored PID.
        proc.wait(timeout=5)
        config.unlink(missing_ok=True)
        raise BridgeError('预览启动超时，已停止本次子进程')

    def _connection(self, session_id):
        path = self.descriptor(session_id)
        if not path.is_file():
            raise BridgeError('预览会话不存在、已停止或闲置到期')
        data = json.loads(path.read_text(encoding='utf-8'))
        port, token = data.get('port'), data.get('token')
        if type(port) is not int or not 1024 <= port <= 65535 or not re.fullmatch('[a-f0-9]{48}', str(token)):
            raise BridgeError('预览会话描述无效')
        return f'http://127.0.0.1:{port}/{token}/'

    def _request(self, session_id, endpoint, payload=None):
        url = self._connection(session_id) + endpoint
        with httpx.Client(trust_env=False, timeout=8) as client:
            response = client.get(url) if payload is None else client.post(url, json=payload)
        if response.status_code != 200:
            try:
                message = response.json().get('error', '预览请求失败')
            except ValueError:
                message = '预览请求失败'
            raise BridgeError(str(message)[:1500])
        return response.json()

    @local_errors
    def state(self, session_id):
        return self._request(session_id, 'state')

    @local_errors
    def click(self, session_id, name, expected_revision):
        return self._request(session_id, 'click', {'name': name, 'revision': expected_revision})

    @local_errors
    def reset(self, session_id, expected_revision):
        return self._request(session_id, 'reset', {'revision': expected_revision})

    @local_errors
    def stop(self, session_id):
        return self._request(session_id, 'stop', {})


class PreviewHTTP(HTTPServer):
    allow_reuse_address = False

    def __init__(self, bundle):
        self.bundle = bundle
        self.game = PreviewGame(bundle)
        self.game.snapshot()  # Fail malformed controls before returning a ready URL.
        self.stopping = False
        self.token = secrets.token_hex(24)
        self.prefix = '/' + self.token + '/'
        self.last_request = time.monotonic()
        self.media = {str(i): value for i, value in enumerate(bundle['media'].values())}
        self.resources = {key: 'media/' + str(i) for i, key in enumerate(bundle['media'])}
        super().__init__(('127.0.0.1', 0), PreviewHandler)
        self.timeout = .5

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(3)
        return sock, address


class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Keep capability URLs out of access logs.

    def send(self, status, value, mime='application/json; charset=utf-8'):
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if isinstance(value, str):
            value = value.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(value)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'; media-src 'self'; frame-ancestors 'none'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(value)

    def route(self):
        host = f'127.0.0.1:{self.server.server_port}'
        if (self.headers.get('Host') != host or
                self.headers.get('Origin', 'http://' + host) != 'http://' + host or
                self.headers.get('Sec-Fetch-Site') == 'cross-site'):
            self.send(403, {'error': '仅允许本地同源预览请求'})
            return None
        path = urlsplit(self.path).path
        if not path.startswith(self.server.prefix):
            self.send(404, {'error': '预览地址无效'})
            return None
        self.server.last_request = time.monotonic()
        return path[len(self.server.prefix):]

    def do_GET(self):
        route = self.route()
        if route is None:
            return
        try:
            if route == '':
                self.send(200, Path(__file__).with_name('preview.html').read_bytes(), 'text/html; charset=utf-8')
            elif route == 'resources.json':
                self.send(200, self.server.resources)
            elif route == 'state':
                self.send(200, self.server.game.snapshot())
            elif route.startswith('media/') and route[6:] in self.server.media:
                content, mime = self.server.media[route[6:]]
                self.send(200, content, mime)
            else:
                self.send(404, {'error': '预览资源不存在'})
        except (BridgeError, ValueError, MemoryError):
            self.send(400, {'error': '预览状态无法序列化，请重置会话'})

    def do_POST(self):
        route = self.route()
        if route is None:
            return
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 4096:
                raise BridgeError('请求体大小无效')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise BridgeError('请求必须是对象')
            if route == 'click':
                result = self.server.game.click(data.get('name'), data.get('revision'))
            elif route == 'reset':
                previous = self.server.game.revision
                if type(data.get('revision')) is not int or data['revision'] != previous:
                    raise BridgeError('预览状态已变化，请重新读取后 reset')
                game = PreviewGame(self.server.bundle)
                game.revision = previous + 1
                self.server.game = game
                result = game.snapshot()
            elif route == 'stop':
                self.server.stopping = True
                result = {'stopped': True, 'native_runtime': False}
            else:
                self.send(404, {'error': '预览操作不存在'})
                return
            self.send(200, result)
        except (BridgeError, ValueError, TypeError) as exc:
            self.send(400, {'error': str(exc)[:1500]})


def serve(request_path):
    request = Path(request_path)
    descriptor = request.with_suffix('.json')
    try:
        config = json.loads(request.read_text(encoding='utf-8'))
        bundle = load_bundle(config['manifest'])
        if bundle['script_sha256'] != config['sha256']:
            raise BridgeError('启动期间脚本发生变化，请重试')
        with PreviewHTTP(bundle) as server:
            info = dict(port=server.server_port, token=server.token, pid=os.getpid())
            tmp = descriptor.with_suffix('.tmp')
            tmp.write_text(json.dumps(info), encoding='utf-8')
            tmp.replace(descriptor)
            request.unlink(missing_ok=True)
            while not server.stopping and time.monotonic() - server.last_request < 7200:
                server.handle_request()
    except Exception as exc:
        request.with_suffix('.error').write_text(str(exc)[:1500], encoding='utf-8')
    finally:
        descriptor.unlink(missing_ok=True)
        request.unlink(missing_ok=True)


if __name__ == '__main__':
    serve(sys.argv[1])
