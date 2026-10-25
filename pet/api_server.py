# -*- coding: utf-8 -*-
"""共用大脑：把桌宠的 Brain 暴露成一个本机 OpenAI 兼容接口。

用途（对应「分离部署 + 共用大脑」方案）：
- 桌面上的大肥鱼照常运行；
- AstrBot / 其他 QQ 机器人把它当成一个 OpenAI 兼容模型（base_url = http://127.0.0.1:<port>/v1，
  model = dfy-companion）；
- 这样 QQ 里和桌面上的「大肥鱼」共用同一份人设、长期记忆、个人库与相处笔记；
- 只走 Brain.reply（聊天），**不暴露 Agent 工具**，QQ 侧不能操作电脑。

安全边界：默认只绑定 127.0.0.1；可选 token（config["server"]["token"]）；不做公网监听。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CHAT_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{NAME}} · 聊天</title>
<style>
  :root { --ink:#1f2a44; --blue:#4a7cf0; --soft:#eaf2ff; --cream:#fff8e6; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Microsoft YaHei","PingFang SC",sans-serif;
         background:linear-gradient(160deg,#eaf2ff,#f7fbff 55%,#fff7ea);
         min-height:100vh; display:flex; justify-content:center; align-items:center; color:var(--ink); }
  .app { width:min(760px,94vw); height:min(720px,90vh); background:#fff; border-radius:22px;
         box-shadow:0 18px 60px rgba(60,90,150,.18); display:flex; flex-direction:column; overflow:hidden; }
  header { padding:16px 22px; background:linear-gradient(120deg,#eaf2ff,#fff6e6);
           display:flex; align-items:center; gap:10px; border-bottom:1px solid #e6eefc; }
  header .name { font-weight:700; font-size:17px; }
  header .tag { color:#6c7fa5; font-size:12px; }
  #log { flex:1; overflow-y:auto; padding:20px 20px 8px; display:flex; flex-direction:column; gap:12px; }
  .msg { max-width:82%; display:flex; flex-direction:column; gap:4px; }
  .msg .who { font-size:12px; color:#8a97b5; }
  .msg .bubble { padding:10px 14px; border-radius:16px; line-height:1.6; font-size:14px;
                 white-space:pre-wrap; word-break:break-word; }
  .me { align-self:flex-end; align-items:flex-end; }
  .me .bubble { background:var(--soft); border-bottom-right-radius:5px; }
  .her .bubble { background:var(--cream); border-bottom-left-radius:5px; }
  .her .who { color:#b07a2a; }
  .typing .bubble { color:#9aa5c0; }
  form { display:flex; gap:9px; padding:14px 16px 18px; background:#fbfdff; border-top:1px solid #eef3fb; }
  input { flex:1; border:1px solid #d8e4f7; border-radius:14px; padding:12px 15px; font-size:14px;
          outline:none; background:#fff; }
  input:focus { border-color:var(--blue); box-shadow:0 0 0 3px rgba(74,124,240,.12); }
  button { border:0; border-radius:14px; padding:0 20px; background:var(--blue); color:#fff;
           font-weight:700; font-size:14px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .hint { text-align:center; color:#9aa8c4; font-size:11px; padding:0 0 8px; }
</style>
</head>
<body>
<div class="app">
  <header><span style="font-size:22px">🐋</span><span class="name">{{NAME}}</span><span class="tag">大肥鱼 · 共用大脑 · 只在本机</span></header>
  <div id="log">
    <div class="msg her"><div class="who">{{NAME}}</div><div class="bubble">唔……主人来啦。本鲸刚在工位上趴着，尾巴还搭在椅腿上。要聊什么？</div></div>
  </div>
  <div class="hint">这里和桌面上的她共用同一份人设、记忆和个人库；不会动电脑。</div>
  <form id="form"><input id="q" placeholder="和{{NAME}}说句话…" autocomplete="off" autofocus><button id="send">发送</button></form>
</div>
<script>
const log = document.getElementById('log');
const form = document.getElementById('form');
const input = document.getElementById('q');
const button = document.getElementById('send');
function add(who, text, cls) {
  const row = document.createElement('div');
  row.className = 'msg ' + cls;
  const name = document.createElement('div');
  name.className = 'who'; name.textContent = who;
  const bubble = document.createElement('div');
  bubble.className = 'bubble'; bubble.textContent = text;
  row.appendChild(name); row.appendChild(bubble); log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return row;
}
async function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = ''; button.disabled = true;
  add('你', text, 'me');
  const typing = add('{{NAME}}', '唔……本鲸想想。', 'her typing');
  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: 'dfy-companion', messages: [{role: 'user', content: text}]})
    });
    const data = await resp.json();
    const reply = data && data.choices && data.choices[0] && data.choices[0].message
      ? data.choices[0].message.content : '呜……本鲸刚刚走神了。';
    typing.remove(); add('{{NAME}}', reply, 'her');
  } catch (err) {
    typing.remove(); add('{{NAME}}', '呜……接口没连上：' + err, 'her');
  } finally {
    button.disabled = false; input.focus();
  }
}
form.addEventListener('submit', (e) => { e.preventDefault(); send(); });
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "DfySharedBrain/1.0"

    # ---------- 基础工具 ----------
    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _authorized(self) -> bool:
        token = getattr(self.server, "brain_token", "") or ""
        if not token:
            return True
        auth = self.headers.get("Authorization", "")
        key = auth.replace("Bearer ", "", 1).strip() if auth.startswith("Bearer ") else auth.strip()
        return key == token

    # ---------- 路由 ----------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/chat"):
            name = getattr(self.server, "display_name", "") or "大肥鱼"
            self._send_html(200, CHAT_HTML.replace("{{NAME}}", name))
        elif path in ("/health", "/v1/health"):
            self._send_json(200, {"ok": True, "service": "dfy-shared-brain",
                                  "time": int(time.time())})
        elif path == "/v1/models":
            self._send_json(200, {"object": "list", "data": [
                {"id": "dfy-companion", "object": "model", "owned_by": "dafeiyu-pet"},
            ]})
        else:
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})

    def _send_html(self, code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"error": {"message": "invalid token", "type": "auth_error"}})
            return
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            self._send_json(400, {"error": {"message": f"bad json: {exc}", "type": "bad_request"}})
            return
        messages = payload.get("messages") or []
        user_text = ""
        for item in reversed(messages if isinstance(messages, list) else []):
            if isinstance(item, dict) and item.get("role") == "user":
                content = item.get("content")
                if isinstance(content, list):
                    content = " ".join(str(part.get("text", "")) for part in content
                                       if isinstance(part, dict))
                if content:
                    user_text = str(content).strip()
                    break
        brain = getattr(self.server, "brain", None)
        if not user_text or brain is None:
            reply, source = "（没有收到要回答的内容）", "error"
        else:
            try:
                reply, source = brain.reply(user_text)
            except Exception as exc:
                reply, source = f"她这边出了点问题：{exc}", "error"
        log = getattr(self.server, "brain_log", None)
        if callable(log):
            log(f"共享大脑请求：{user_text[:60]} → {source}（{len(reply)} 字）")
        self._send_json(200, {
            "id": f"chatcmpl-dfy-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "dfy-companion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "dfy_source": source,
        })

    def log_message(self, fmt, *args) -> None:  # 不往 stderr 刷每条请求
        pass


class SharedBrainServer:
    """独立线程里跑一个 OpenAI 兼容的只读聊天服务。"""

    def __init__(self, brain, config, log=print, port: int | None = None):
        self.brain = brain
        self.config = config or {}
        self.log = log if callable(log) else print
        server_cfg = self.config.get("server") or {}
        self.port = int(port or server_cfg.get("port") or 8765)
        self.token = str(server_cfg.get("token") or "")
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._httpd is not None)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> tuple[bool, str]:
        if self.running:
            return (True, self.base_url)
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        except OSError as exc:
            return (False, f"端口 {self.port} 起不来：{exc}")
        httpd.brain = self.brain
        httpd.brain_token = self.token
        httpd.brain_log = self.log
        httpd.display_name = str(self.config.get("display_name") or self.config.get("pet_name") or "大肥鱼")
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="shared-brain", daemon=True)
        self._thread.start()
        self.log(f"共用大脑已启动：{self.base_url}（model=dfy-companion，仅本机，"
                 f"token={'有' if self.token else '无'}，不暴露工具）")
        return (True, self.base_url)

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
            self.log("共用大脑已停止")
