# -*- coding: utf-8 -*-
"""qq_bridge 的离线测试：用本地假 OneBot HTTP 服务，不碰真实 QQ。"""
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qq_bridge import QQBridge, extract_text


class _FakeOneBot(BaseHTTPRequestHandler):
    history = [
        {"message_id": "old-1", "user_id": 20000, "sender": {"user_id": 20000}, "message": "旧消息"},
    ]
    sent = []
    new_message = {"message_id": "new-1", "user_id": 20000,
                   "sender": {"user_id": 20000},
                   "message": [{"type": "text", "data": {"text": "大肥鱼，在吗？"}}]}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
        action = self.path.strip("/")
        self.server.calls.append(action)
        if action == "get_login_info":
            data = {"user_id": 10000, "nickname": "fake-bot"}
        elif action == "get_friend_msg_history":
            messages = list(type(self).history)
            if len(self.server.calls) > 2:  # 第一轮之后才出现新消息
                messages.append(type(self).new_message)
            data = {"messages": messages}
        elif action == "send_private_msg":
            type(self).sent.append(payload)
            data = {"message_id": "sent-1"}
        else:
            data = {}
        body = json.dumps({"status": "ok", "retcode": 0, "data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class QQBridgeTest(unittest.TestCase):
    def setUp(self):
        _FakeOneBot.sent = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOneBot)
        self.server.calls = []
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.config = {"qq": {"enabled": True,
                              "base_url": f"http://127.0.0.1:{self.port}",
                              "private_user": "20000",
                              "group_id": "",
                              "whitelist": [],
                              "poll_seconds": 2}}
        self.bridge = QQBridge(self.config, on_message=lambda text: f"收到：{text}",
                               log=lambda *a: None)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.bridge.stop)

    def test_extract_text_segments_and_cq(self):
        self.assertEqual(extract_text([{"type": "text", "data": {"text": "你好"}},
                                       {"type": "image", "data": {}}]), "你好[图片]")
        self.assertEqual(extract_text("你好[CQ:image,file=a.png]"), "你好[图片]")

    def test_start_and_reply(self):
        ok, message = self.bridge.start()
        self.assertTrue(ok, message)
        deadline = time.time() + 10
        while time.time() < deadline and not _FakeOneBot.sent:
            time.sleep(0.1)
        self.assertTrue(_FakeOneBot.sent, "没有把回复发回私聊")
        self.assertEqual(_FakeOneBot.sent[0]["user_id"], 20000)
        self.assertIn("收到", _FakeOneBot.sent[0]["message"])

    def test_whitelist_blocks_other_senders(self):
        self.config["qq"]["whitelist"] = ["99999"]
        self.config["qq"]["private_user"] = "20000"
        bridge = QQBridge(self.config, on_message=lambda text: "不该回", log=lambda *a: None)
        self.addCleanup(bridge.stop)
        ok, _ = bridge.start()
        self.assertTrue(ok)
        time.sleep(3)
        self.assertEqual(_FakeOneBot.sent, [])


if __name__ == "__main__":
    unittest.main()
