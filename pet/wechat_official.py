# -*- coding: utf-8 -*-
"""微信公众号（订阅号 / 测试号）接入：接收微信消息 → 桌宠回复。

微信服务器会把用户消息 POST 到你的公网 URL（本模块的 /wechat 路径），
本模块校验签名、解析 XML、交给 Brain 回复，再把 XML 文本回复塞回 HTTP 响应。
- 签名：sha1(sort(token, timestamp, nonce))，GET 验证 echostr；
- 只处理文本（图片/语音会回一句提示）；事件 subscribe 回欢迎语；
- 微信要求 5 秒内回复，所以这里同步回复；回复太长会截断。
- 默认只绑定 127.0.0.1，公网访问靠 cloudflared / frp / 服务器反代。
"""
from __future__ import annotations

import hashlib
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _cdata(text: str) -> str:
    return "<![CDATA[" + str(text).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _reply_xml(to_user: str, from_user: str, text: str) -> bytes:
    body = (f"<xml><ToUserName>{_cdata(to_user)}</ToUserName>"
            f"<FromUserName>{_cdata(from_user)}</FromUserName>"
            f"<CreateTime>{int(time.time())}</CreateTime>"
            f"<MsgType>{_cdata('text')}</MsgType>"
            f"<Content>{_cdata(text[:600])}</Content></xml>")
    return body.encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server_version = "DfyWechat/1.0"

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/wechat":
            self.send_response(404)
            self.end_headers()
            return
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        token = getattr(self.server, "wechat_token", "")
        signature = (query.get("signature") or [""])[0]
        timestamp = (query.get("timestamp") or [""])[0]
        nonce = (query.get("nonce") or [""])[0]
        echo = (query.get("echostr") or [""])[0]
        expected = hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode()).hexdigest()
        if signature and signature == expected:
            data = echo.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(403)
            self.end_headers()

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/wechat":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b""
            root = ET.fromstring(raw.decode("utf-8", errors="replace"))
            msg_type = (root.findtext("MsgType") or "").strip()
            from_user = (root.findtext("FromUserName") or "").strip()
            to_user = (root.findtext("ToUserName") or "").strip()
            content = (root.findtext("Content") or "").strip()
            log = getattr(self.server, "wechat_log", None)
            if callable(log):
                log(f"公众号消息：{msg_type} / {from_user[:12]}：{content[:60]}")
            reply = ""
            if msg_type == "event":
                event = (root.findtext("Event") or "").strip().lower()
                if event == "subscribe":
                    name = getattr(self.server, "display_name", "") or "大肥鱼"
                    reply = f"唔……主人来啦？本鲸是{name}。这里也能陪你说话。"
            elif msg_type == "text" and content:
                handler = getattr(self.server, "wechat_on_message", None)
                if callable(handler):
                    try:
                        reply = str(handler(content) or "")
                    except Exception as exc:
                        reply = "呜……本鲸刚刚走神了，再说一遍嘛。"
                        if callable(log):
                            log(f"公众号回复失败：{exc}")
            elif msg_type in ("image", "voice", "video"):
                reply = "这个本鲸还看不了……先打字跟本鲸说嘛。"
            if reply and from_user and to_user:
                data = _reply_xml(from_user, to_user, reply)
                self.send_response(200)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
        except Exception as exc:
            log = getattr(self.server, "wechat_log", None)
            if callable(log):
                log(f"公众号请求异常：{exc}")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class WeChatOfficialServer:
    def __init__(self, brain, config: dict, log=print):
        self.brain = brain
        self.config = config or {}
        cfg = self.config.get("wechat") or {}
        raw_port = cfg.get("port")
        self.port = int(raw_port) if raw_port is not None else 8800
        self.token = str(cfg.get("token") or "").strip()
        self._httpd = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> tuple[bool, str]:
        if self.running:
            return (True, f"公众号接口已在跑：/wechat（端口 {self.port}）")
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        except OSError as exc:
            return (False, f"端口 {self.port} 起不来：{exc}")
        httpd.wechat_token = self.token
        httpd.display_name = str(self.config.get("display_name") or self.config.get("pet_name") or "大肥鱼")
        httpd.wechat_on_message = self._on_message
        httpd.wechat_log = lambda message: self.brain.log(message) if self.brain else print(message)
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="wechat-official", daemon=True)
        self._thread.start()
        return (True, f"公众号接口已启动：http://127.0.0.1:{self.port}/wechat（Token 见配置）")

    def _on_message(self, text: str) -> str:
        try:
            reply, source = self.brain.reply(text)
        except Exception as exc:
            return "呜……本鲸刚刚走神了，再说一遍嘛。"
        return reply

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
