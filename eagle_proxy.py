#!/usr/bin/env python3
"""
Eagle Proxy Server
ポート8080でHTMLを配信しつつ、/api/* をEagle(41595)に転送する。
/file?id=XXX でアイテムの実ファイルを返す（フル解像度ダウンロード用）。
サムネイルはWebPに変換してキャッシュし通信量を削減する（リサイズなし）。

使い方:
  python eagle_proxy.py

設定:
  config.ini に APIトークン等を記載（config.ini.example を参照）
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import mimetypes
import configparser

# --- 設定ファイルの読み込み ---
_config = configparser.ConfigParser()
_config_path = os.path.join(os.path.dirname(__file__), 'config.ini')
if not os.path.exists(_config_path):
    print(f"[WARNING] config.ini が見つかりません。config.ini.example をコピーして設定してください。")
    print(f"  cp config.ini.example config.ini")
_config.read(_config_path, encoding='utf-8')

def _get(section, key, fallback):
    return _config.get(section, key, fallback=fallback)

EAGLE_PORT  = int(_get('eagle',  'port',       '41595'))
EAGLE_TOKEN =     _get('eagle',  'token',      '')
SERVE_PORT  = int(_get('server', 'port',       '8080'))
HTML_FILE   =     _get('server', 'html_file',  'eagle-viewer.html')
_WEBP_CFG   =     _get('thumbnail', 'webp_enabled', 'false').lower()
WEBP_QUALITY= int(_get('thumbnail', 'webp_quality',  '75'))

if not EAGLE_TOKEN:
    print("[WARNING] config.ini に Eagle V2 APIトークンが設定されていません。")
    print("  [eagle] セクションの token= を設定してください。")

try:
    from PIL import Image
    import io as _io
    WEBP_ENABLED = _WEBP_CFG in ('true', '1', 'yes') and True
    if WEBP_ENABLED:
        print(f"Pillow が見つかりました。WebP変換を有効化します (quality={WEBP_QUALITY})。")
    else:
        print("WebP変換は無効です（config.ini の webp_enabled = true で有効化）。")
except ImportError:
    WEBP_ENABLED = False
    print("Pillow が見つかりません。pip install Pillow で有効化できます。")

import queue as _queue
import threading as _threading
normalizer_log = _queue.Queue()
normalizer_status = {"running": False, "done": False, "result": None, "stop": False}
SERVE_PORT = 8080
HTML_FILE = "eagle-viewer.html"

# 転送量統計
_transfer_log = {}
_transfer_lock = _threading.Lock()

def add_transfer(key, nbytes):
    with _transfer_lock:
        _transfer_log[key] = _transfer_log.get(key, 0) + nbytes

# WebPキャッシュロック（同一ファイルへの同時変換を防ぐ）
_thumb_locks = {}
_thumb_locks_lock = _threading.Lock()

def get_thumb_lock(path):
    with _thumb_locks_lock:
        if path not in _thumb_locks:
            _thumb_locks[path] = _threading.Lock()
        return _thumb_locks[path]

def get_webp_cache_path(thumb_path):
    base, _ = os.path.splitext(thumb_path)
    return f"{base}_proxycache_q{WEBP_QUALITY}.webp"

def convert_to_webp(thumb_path):
    """PNG/JPEGサムネイルをWebPに変換してキャッシュ（リサイズなし）。戻り値: (bytes, mime)"""
    cache_path = get_webp_cache_path(thumb_path)
    lock = get_thumb_lock(thumb_path)
    with lock:
        # キャッシュが新鮮なら返す
        if os.path.exists(cache_path):
            try:
                if os.path.getmtime(cache_path) >= os.path.getmtime(thumb_path):
                    with open(cache_path, 'rb') as f:
                        return f.read(), 'image/webp'
            except OSError:
                pass
        # WebP変換（リサイズなし）
        try:
            with Image.open(thumb_path) as img:
                buf = _io.BytesIO()
                img.save(buf, format='WEBP', quality=WEBP_QUALITY, method=4)
                webp_bytes = buf.getvalue()
            with open(cache_path, 'wb') as f:
                f.write(webp_bytes)
            orig_kb = os.path.getsize(thumb_path) // 1024
            new_kb = len(webp_bytes) // 1024
            print(f"WebP: {orig_kb}KB → {new_kb}KB ({os.path.basename(thumb_path)[:40]})")
            return webp_bytes, 'image/webp'
        except Exception as e:
            print(f"WebP変換失敗: {e}")
            with open(thumb_path, 'rb') as f:
                body = f.read()
            mime, _ = mimetypes.guess_type(thumb_path)
            return body, mime or 'image/png'

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """並列リクエスト処理に対応したHTTPServer"""
    daemon_threads = True

class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        path = self.path
        if path.startswith('/api/v2/'):
            self._proxy_post_to_eagle_v2(path)
        elif path.startswith('/api/'):
            self._proxy_post_to_eagle(path)
        else:
            self.send_error(404)

    def _proxy_post_to_eagle(self, path):
        target = f"http://localhost:{EAGLE_PORT}{path}"
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length > 0 else b''
        try:
            req = urllib.request.Request(
                target, data=body,
                headers={'Content-Type': self.headers.get('Content-Type', 'application/json')},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_body = resp.read()
                content_type = resp.headers.get('Content-Type', 'application/json')
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(resp_body)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            resp_body = e.read()
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception as e:
            print(f"POST proxy error: {e}")
            self.send_error(502, str(e))

    def do_GET(self):
        path = self.path

        # テスト用: /test-img?id=XXX で画像タグ直接表示
        if path.startswith('/test-img'):
            qs = urllib.parse.urlparse(path).query
            params = urllib.parse.parse_qs(qs)
            item_id = params.get('id', [''])[0]
            item_ext = params.get('ext', ['png'])[0]
            body = f'<html><body style="background:#000;margin:0"><img src="/file?id={item_id}&ext={item_ext}" style="max-width:100%;display:block"></body></html>'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # Base64でファイルを返す（表示用）: /filedata?id=XXX
        if path.startswith('/filedata'):
            self._serve_filedata(path)
        # フル解像度ファイル配信: /file?id=XXX（ダウンロード用）
        elif path.startswith('/file'):
            self._serve_file(path)
        # サムネイル
        elif path.startswith('/api/item/thumbnail'):
            self._serve_thumbnail(path)
        # Eagle再起動
        elif path == '/restart-eagle':
            self._restart_eagle()
        elif path.startswith('/transfer-stats'):
            self._transfer_stats()
        # V2 API → Eagle に転送（トークン自動付与）
        elif path.startswith('/api/v2/'):
            self._proxy_to_eagle_v2(path)
        # その他の /api/* → Eagle に転送
        elif path.startswith('/api/'):
            self._proxy_to_eagle(path)
        # HTML
        elif path in ('/', '/eagle-viewer.html', '/index.html'):
            self._serve_html()
        elif path == '/tag-normalizer':
            self._serve_file_by_name('eagle_tag_normalizer.html')
        elif path.startswith('/run-normalizer'):
            self._run_normalizer(path)
        elif path == '/normalizer-log':
            self._get_normalizer_log()
        elif path == '/normalizer-stop':
            self._stop_normalizer()
        elif path.startswith('/run-bulk-tag'):
            self._run_bulk_tag(path)
        elif path == '/proxy-restart':
            self._restart_proxy()
        else:
            self.send_error(404)

    def _serve_filedata(self, path):
        """
        /filedata?id=XXX → ファイルをBase64エンコードしてJSONで返す
        ブラウザがダウンロードせずDataURLとして画像表示できる
        """
        import base64
        qs = urllib.parse.urlparse(path).query
        params = urllib.parse.parse_qs(qs)
        item_id = params.get('id', [None])[0]
        item_ext = params.get('ext', [''])[0]
        if not item_id:
            self.send_error(400, "id required")
            return
        try:
            thumb_url_str = f"http://localhost:{EAGLE_PORT}/api/item/thumbnail?id={item_id}"
            with urllib.request.urlopen(thumb_url_str, timeout=10) as resp:
                thumb_data = json.loads(resp.read().decode('utf-8'))
            thumb_path = urllib.parse.unquote(thumb_data.get('data', ''))
            info_dir = os.path.dirname(thumb_path)

            file_path = None
            if item_ext and os.path.isdir(info_dir):
                for fname in os.listdir(info_dir):
                    _, fext = os.path.splitext(fname)
                    if fext.lower() == '.' + item_ext.lower() and '_thumbnail' not in fname:
                        file_path = os.path.join(info_dir, fname)
                        break
            if not file_path and os.path.isdir(info_dir):
                for fname in os.listdir(info_dir):
                    if '_thumbnail' in fname: continue
                    _, fext = os.path.splitext(fname)
                    if fext.lower() == '.json': continue
                    file_path = os.path.join(info_dir, fname)
                    break

            if not file_path or not os.path.exists(file_path):
                self.send_error(404, "File not found")
                return

            mime, _ = mimetypes.guess_type(file_path)
            if not mime:
                mime = 'application/octet-stream'

            with open(file_path, 'rb') as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode('ascii')
            result = json.dumps({'dataUrl': f'data:{mime};base64,{b64}', 'mime': mime}).encode('utf-8')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(result)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(result)

        except Exception as e:
            print(f"Filedata error: {e}")
            self.send_error(502, str(e))

    def _serve_file(self, path):
        """
        /file?id=XXX
        サムネイルのパスからライブラリ構造を利用して元ファイルを返す。
        Eagle V1ライブラリ構造:
          [library]/images/[ID].info/[name].[ext]
        サムネイル: [library]/images/[ID].info/[name]_thumbnail.png
        """
        qs = urllib.parse.urlparse(path).query
        params = urllib.parse.parse_qs(qs)
        item_id = params.get('id', [None])[0]
        item_name = urllib.parse.unquote(params.get('name', ['file'])[0])
        item_ext = params.get('ext', [''])[0]
        if not item_id:
            self.send_error(400, "id parameter required")
            return

        try:
            # サムネイルAPIからライブラリの.infoフォルダパスを取得
            thumb_url = f"http://localhost:{EAGLE_PORT}/api/item/thumbnail?id={item_id}"
            with urllib.request.urlopen(thumb_url, timeout=10) as resp:
                thumb_data = json.loads(resp.read().decode('utf-8'))

            thumb_path = urllib.parse.unquote(thumb_data.get('data', ''))
            if not thumb_path:
                self.send_error(404, "Cannot resolve file path")
                return

            # .infoフォルダのパスを取得
            info_dir = os.path.dirname(thumb_path)

            # extが渡されている場合はその拡張子のファイルを直接探す
            file_path = None
            if item_ext and os.path.isdir(info_dir):
                for fname in os.listdir(info_dir):
                    _, fext = os.path.splitext(fname)
                    if fext.lower() == '.' + item_ext.lower() and '_thumbnail' not in fname:
                        file_path = os.path.join(info_dir, fname)
                        break

            # extがない場合はjsonと_thumbnail以外を探す（フォールバック）
            if not file_path and os.path.isdir(info_dir):
                candidates = []
                for fname in os.listdir(info_dir):
                    if '_thumbnail' in fname:
                        continue
                    _, fext = os.path.splitext(fname)
                    if fext.lower() == '.json':
                        continue
                    candidates.append(os.path.join(info_dir, fname))
                if candidates:
                    file_path = candidates[0]

            if not file_path or not os.path.exists(file_path):
                print(f"Original file not found in: {info_dir}")
                self.send_error(404, "Original file not found")
                return

            filename = os.path.basename(file_path)
            mime, _ = mimetypes.guess_type(file_path)
            if not mime:
                mime = 'application/octet-stream'

            with open(file_path, 'rb') as f:
                body = f.read()

            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'public, max-age=600')
            self.end_headers()
            self.wfile.write(body)

        except Exception as e:
            print(f"File serve error: {e}")
            self.send_error(502, str(e))

    def _serve_thumbnail(self, path):
        target = f"http://localhost:{EAGLE_PORT}{path}"
        try:
            with urllib.request.urlopen(target, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            thumb_path = data.get('data', '')
            thumb_path = urllib.parse.unquote(thumb_path)

            if not thumb_path or not os.path.exists(thumb_path):
                print(f"Thumb not found: {thumb_path}")
                self.send_error(404, "Thumbnail not found")
                return

            with open(thumb_path, 'rb') as f:
                body = f.read()
            mime, _ = mimetypes.guess_type(thumb_path)
            if not mime:
                mime = 'image/jpeg'

            # WebP変換（Pillowがあれば）
            if WEBP_ENABLED:
                body, mime = convert_to_webp(thumb_path)

            kb = len(body) // 1024
            if kb > 50:
                print(f"[LARGE THUMB] {kb}KB: {os.path.basename(thumb_path)}")
            add_transfer('thumb', len(body))

            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()
            self.wfile.write(body)

        except Exception as e:
            print(f"Thumbnail error: {e}")
            self.send_error(502, str(e))

    def _transfer_stats(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        if 'reset' in params:
            with _transfer_lock:
                _transfer_log.clear()
        thumb_kb = _transfer_log.get('thumb', 0) // 1024
        api_kb = _transfer_log.get('api', 0) // 1024
        total_kb = thumb_kb + api_kb
        msg = json.dumps({
            'thumb_kb': thumb_kb,
            'api_kb': api_kb,
            'total_kb': total_kb,
            'total_mb': round(total_kb / 1024, 2)
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(msg)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(msg)

    def _restart_eagle(self):
        import subprocess
        try:
            # Eagleプロセスを終了して再起動
            subprocess.Popen(['taskkill', '/F', '/IM', 'Eagle.exe'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import time
            time.sleep(2)
            # Eagleを再起動（プロキシから切り離して独立プロセスとして起動）
            import glob
            eagle_paths = glob.glob(r'C:\Users\*\AppData\Local\Programs\Eagle\Eagle.exe')
            if not eagle_paths:
                eagle_paths = glob.glob(r'C:\Program Files\Eagle\Eagle.exe')
            if eagle_paths:
                subprocess.Popen(
                    [eagle_paths[0]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
                msg = b'{"status":"success","message":"Eagle restarting"}'
            else:
                msg = b'{"status":"error","message":"Eagle.exe not found"}'
        except Exception as e:
            msg = f'{{"status":"error","message":"{str(e)}"}}'.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(msg)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(msg)

    def _proxy_to_eagle_v2(self, path):
        # GETパラメータにトークンを追加
        sep = '&' if '?' in path else '?'
        target = f"http://localhost:{EAGLE_PORT}{path}{sep}token={EAGLE_TOKEN}"
        try:
            with urllib.request.urlopen(target, timeout=30) as resp:
                body = resp.read()
                ct = resp.headers.get('Content-Type', 'application/json')
                print(f"[V2] {path[:60]} → {len(body)//1024}KB")
                add_transfer('api', len(body))
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            self.send_error(502, str(e))

    def _proxy_post_to_eagle_v2(self, path):
        sep = '&' if '?' in path else '?'
        target = f"http://localhost:{EAGLE_PORT}{path}{sep}token={EAGLE_TOKEN}"
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length > 0 else b''
        try:
            req = urllib.request.Request(
                target, data=body,
                headers={'Content-Type': self.headers.get('Content-Type', 'application/json')},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                print(f"[V2 POST] {path[:60]} req={len(body)//1024}KB → {len(resp_body)//1024}KB")
                add_transfer('api', len(resp_body))
                self.send_response(200)
                self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json'))
                self.send_header('Content-Length', str(len(resp_body)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(resp_body)
        except Exception as e:
            self.send_error(502, str(e))

    def _proxy_to_eagle(self, path):
        target = f"http://localhost:{EAGLE_PORT}{path}"
        try:
            with urllib.request.urlopen(target, timeout=15) as resp:
                body = resp.read()
                content_type = resp.headers.get('Content-Type', 'application/json')
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            body = e.read()
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            print(f"Proxy error: {e}")
            self.send_error(502, str(e))

    def _run_normalizer(self, path):
        global normalizer_log, normalizer_status
        qs = urllib.parse.urlparse(path).query
        params = urllib.parse.parse_qs(qs)
        dry_run = params.get('dry', ['1'])[0] != '0'

        if normalizer_status["running"]:
            msg = json.dumps({"status": "already_running"}).encode()
        else:
            # ログキューをリセット
            while not normalizer_log.empty():
                try: normalizer_log.get_nowait()
                except: break
            normalizer_status.update({"running": True, "done": False, "result": None})

            def do_run():
                global normalizer_status
                try:
                    import eagle_tag_normalizer as norm
                    result = norm.run(
                        dry_run=dry_run,
                        log=lambda msg: normalizer_log.put(msg),
                        stop_flag=lambda: normalizer_status.get("stop", False)
                    )
                    normalizer_status.update({"running": False, "done": True, "result": result, "stop": False})
                except Exception as e:
                    normalizer_log.put(f"エラー: {e}")
                    normalizer_status.update({"running": False, "done": True, "result": {"status": "error"}, "stop": False})

            _threading.Thread(target=do_run, daemon=True).start()
            mode = 'preview' if dry_run else 'apply'
            msg = json.dumps({"status": "started", "mode": mode}).encode()

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(msg)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(msg)

    def _get_normalizer_log(self):
        global normalizer_log, normalizer_status
        lines = []
        while not normalizer_log.empty():
            try: lines.append(normalizer_log.get_nowait())
            except: break
        data = {
            "lines": lines,
            "running": normalizer_status["running"],
            "done": normalizer_status["done"],
            "result": normalizer_status["result"],
        }
        msg = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(msg)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(msg)

    def _run_bulk_tag(self, path):
        global normalizer_log, normalizer_status
        qs = urllib.parse.urlparse(path).query
        params = urllib.parse.parse_qs(qs)
        dry_run = params.get('dry', ['1'])[0] != '0'
        search_tag = params.get('search_tag', [''])[0]
        add_tags = [t.strip() for t in params.get('add_tags', [''])[0].split(',') if t.strip()]

        if normalizer_status["running"]:
            msg = json.dumps({"status": "already_running"}).encode()
        else:
            while not normalizer_log.empty():
                try: normalizer_log.get_nowait()
                except: break
            normalizer_status.update({"running": True, "done": False, "result": None, "stop": False})

            def do_run():
                global normalizer_status
                try:
                    result = self._bulk_tag_run(
                        search_tag=search_tag,
                        add_tags=add_tags,
                        dry_run=dry_run,
                        log=lambda msg: normalizer_log.put(msg),
                        stop_flag=lambda: normalizer_status.get("stop", False)
                    )
                    normalizer_status.update({"running": False, "done": True, "result": result, "stop": False})
                except Exception as e:
                    normalizer_log.put(f"エラー: {e}")
                    normalizer_status.update({"running": False, "done": True, "result": {"status": "error"}, "stop": False})

            _threading.Thread(target=do_run, daemon=True).start()
            msg = json.dumps({"status": "started", "mode": "preview" if dry_run else "apply"}).encode()

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(msg)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(msg)

    def _bulk_tag_run(self, search_tag, add_tags, dry_run, log, stop_flag):
        import urllib.request, urllib.parse, json as _json
        base = f"http://localhost:{EAGLE_PORT}"

        def api_get(path, params=None):
            qs = ("?" + urllib.parse.urlencode(params)) if params else ""
            with urllib.request.urlopen(base + path + qs, timeout=30) as r:
                return _json.loads(r.read())

        def api_post(path, body):
            data = _json.dumps(body).encode()
            req = urllib.request.Request(base + path, data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                return _json.loads(r.read())

        log(f"検索タグ: {search_tag}")
        log(f"追加タグ: {add_tags}")

        # 複数orderByで全件取得
        orders = ["-CREATEDATE", "CREATEDATE", "-MODIFICATIONTIME", "MODIFICATIONTIME",
                  "NAME", "-NAME", "-FILESIZE", "FILESIZE", "-RESOLUTION", "RESOLUTION"]
        all_items = {}
        for order in orders:
            res = api_get("/api/item/list", {"limit": 200, "tags": search_tag, "orderBy": order})
            for item in (res.get("data") or []):
                all_items[item["id"]] = item
        log(f"対象アイテム: {len(all_items)}件")

        if not all_items:
            log("対象アイテムがありませんでした")
            return {"status": "done", "updated": 0, "errors": 0}

        if dry_run:
            log("プレビュー（上位10件）:")
            for item in list(all_items.values())[:10]:
                existing = item.get("tags") or []
                to_add = [t for t in add_tags if t not in existing]
                log(f"  {item.get('name','')[:50]}")
                log(f"    追加予定: {to_add}")
            return {"status": "preview", "count": len(all_items)}

        # 実際に更新
        done, errors = 0, 0
        for i, item in enumerate(all_items.values()):
            if stop_flag():
                log(f"⏹ 停止しました（{done}件更新済み）")
                return {"status": "stopped", "updated": done, "errors": errors}
            existing = list(item.get("tags") or [])
            new_tags = existing + [t for t in add_tags if t not in existing]
            if new_tags == existing:
                done += 1
                continue
            try:
                res = api_post("/api/item/update", {"id": item["id"], "tags": new_tags})
                if res.get("status") == "success":
                    done += 1
                else:
                    errors += 1
            except Exception as e:
                errors += 1
                log(f"エラー: {e}")
            if (i+1) % 50 == 0:
                log(f"  {i+1}/{len(all_items)}件処理済み...")
        log(f"完了: {done}件更新 / エラー: {errors}件")
        return {"status": "done", "updated": done, "errors": errors}

    def _stop_normalizer(self):
        global normalizer_status
        normalizer_status["stop"] = True
        normalizer_log.put("⏹ 停止リクエストを受信しました...")
        msg = json.dumps({"status": "stopping"}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(msg)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(msg)

    def _restart_proxy(self):
        import sys, os
        msg = json.dumps({"status": "restarting"}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(msg)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(msg)
        # 少し待ってから再起動
        def do_restart():
            import time
            time.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        _threading.Thread(target=do_restart, daemon=True).start()

    def _serve_file_by_name(self, filename):
        html_path = os.path.join(os.path.dirname(__file__), filename)
        try:
            with open(html_path, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404, f'{filename} not found')

    def _serve_html(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(script_dir, HTML_FILE)
        if not os.path.exists(html_path):
            self.send_error(404, f"{HTML_FILE} が見つかりません")
            return
        with open(html_path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', SERVE_PORT), ProxyHandler)
    print(f"Eagle Proxy 起動中 → ポート {SERVE_PORT}")
    print(f"WebP変換: {'有効 (quality=' + str(WEBP_QUALITY) + ')' if WEBP_ENABLED else '無効 (pip install Pillow で有効化)'}")
    print(f"AndroidのChromeで開く: http://[PCのIP]:{SERVE_PORT}/")
    print(f"停止: Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")
