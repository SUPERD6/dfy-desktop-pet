# -*- coding: utf-8 -*-
"""wechat_official 的离线测试：本机 HTTP 模拟微信服务器，不联网。"""
import hashlib
import sys
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_official import WeChatOfficialServer


class _FakeBrain:
    log = staticmethod(lambda *a: None)

    def reply(self, text):
        return f"大肥鱼收到：{text}", "llm"


class WeChatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = {"wechat": {"port": 0, "token": "test-token-123"}}
        cls.server = WeChatOfficialServer(_FakeBrain(), cls.config, log=lambda *a: None)
        ok, msg = cls.server.start()
        assert ok, msg
        cls.port = cls.server._httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}/wechat"

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_verify_signature(self):
        timestamp, nonce = "12345", "abcde"
        signature = hashlib.sha1("".join(sorted(["test-token-123", timestamp, nonce])).encode()).hexdigest()
        query = urllib.parse.urlencode({"signature": signature, "timestamp": timestamp,
                                        "nonce": nonce, "echostr": "hello"})
        with urllib.request.urlopen(f"{self.base}?{query}", timeout=5) as resp:
            self.assertEqual(resp.read().decode(), "hello")

    def test_reply_text_message(self):
        xml = ("<xml><ToUserName><![CDATA[to]]></ToUserName>"
               "<FromUserName><![CDATA[from]]></FromUserName>"
               "<CreateTime>1</CreateTime><MsgType><![CDATA[text]]></MsgType>"
               "<Content><![CDATA[大肥鱼在吗]]></Content><MsgId>1</MsgId></xml>")
        request = urllib.request.Request(self.base, data=xml.encode(), method="POST",
                                         headers={"Content-Type": "application/xml"})
        with urllib.request.urlopen(request, timeout=10) as resp:
            body = resp.read().decode()
        self.assertIn("大肥鱼收到", body)
        self.assertIn("to", body)

    def test_bad_signature(self):
        with self.assertRaises(Exception):
            urllib.request.urlopen(f"{self.base}?signature=bad&timestamp=1&nonce=2&echostr=x", timeout=5)


if __name__ == "__main__":
    unittest.main()
