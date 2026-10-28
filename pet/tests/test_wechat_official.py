# -*- coding: utf-8 -*-
"""wechat_official 的离线测试：本机 HTTP 模拟微信服务器，不联网。"""
import hashlib
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_official import WeChatOfficialServer, verify_signature

TOKEN = "test-token-123"


class _FakeBrain:
    log = staticmethod(lambda *a: None)

    def __init__(self):
        self.calls = 0

    def reply(self, text):
        self.calls += 1
        return f"大肥鱼收到：{text}", "llm"


def signed_query(token=TOKEN, timestamp=None, **extra):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    nonce = "abcde"
    signature = hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode()).hexdigest()
    return urllib.parse.urlencode({"signature": signature, "timestamp": timestamp, "nonce": nonce, **extra})


def text_xml(content="大肥鱼在吗", sender="from"):
    return ("<xml><ToUserName><![CDATA[to]]></ToUserName>"
            f"<FromUserName><![CDATA[{sender}]]></FromUserName>"
            "<CreateTime>1</CreateTime><MsgType><![CDATA[text]]></MsgType>"
            f"<Content><![CDATA[{content}]]></Content><MsgId>1</MsgId></xml>").encode()


class WeChatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brain = _FakeBrain()
        cls.config = {"wechat": {"port": 0, "token": TOKEN}}
        cls.server = WeChatOfficialServer(cls.brain, cls.config, log=lambda *a: None)
        ok, msg = cls.server.start()
        assert ok, msg
        cls.port = cls.server._httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}/wechat"

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def post(self, query, body):
        url = f"{self.base}?{query}" if query else self.base
        request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/xml"})
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.read().decode()

    def test_verify_signature(self):
        with urllib.request.urlopen(f"{self.base}?{signed_query(echostr='hello')}", timeout=5) as resp:
            self.assertEqual(resp.read().decode(), "hello")

    def test_reply_text_message(self):
        body = self.post(signed_query(), text_xml())
        self.assertIn("大肥鱼收到", body)
        self.assertIn("to", body)

    def test_bad_signature(self):
        with self.assertRaises(Exception):
            urllib.request.urlopen(f"{self.base}?signature=bad&timestamp=1&nonce=2&echostr=x", timeout=5)

    def test_unsigned_post_rejected_without_calling_brain(self):
        """以前 POST 不验签：通过隧道暴露后任何人都能让她花钱、套记忆。"""
        before = self.brain.calls
        for query in ("", "signature=bad&timestamp=1&nonce=2", signed_query(token="wrong")):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.post(query, text_xml())
            self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(self.brain.calls, before)

    def test_replayed_old_timestamp_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post(signed_query(timestamp=int(time.time()) - 3600), text_xml())
        self.assertEqual(ctx.exception.code, 403)

    def test_verify_signature_helper(self):
        now = 1_800_000_000
        sig = hashlib.sha1("".join(sorted([TOKEN, str(now), "n"])).encode()).hexdigest()
        self.assertTrue(verify_signature(TOKEN, sig, str(now), "n", now=now + 10))
        self.assertFalse(verify_signature("", sig, str(now), "n", now=now))
        self.assertFalse(verify_signature(TOKEN, sig, str(now), "n", now=now + 3600))


class WeChatSetupTest(unittest.TestCase):
    def test_no_token_refuses_to_start(self):
        server = WeChatOfficialServer(_FakeBrain(), {"wechat": {"port": 0, "token": ""}}, log=lambda *a: None)
        ok, msg = server.start()
        self.assertFalse(ok)
        self.assertIn("Token", msg)

    def test_allowlist_keeps_strangers_away_from_brain(self):
        brain = _FakeBrain()
        server = WeChatOfficialServer(brain, {"wechat": {"port": 0, "token": TOKEN, "allowed_openids": ["me"]}},
                                      log=lambda *a: None)
        ok, msg = server.start()
        self.assertTrue(ok, msg)
        try:
            base = f"http://127.0.0.1:{server._httpd.server_address[1]}/wechat"

            def post(sender):
                request = urllib.request.Request(f"{base}?{signed_query()}", data=text_xml(sender=sender),
                                                 method="POST")
                with urllib.request.urlopen(request, timeout=10) as resp:
                    return resp.read().decode()

            self.assertIn("只陪主人", post("stranger"))
            self.assertEqual(brain.calls, 0)
            self.assertIn("大肥鱼收到", post("me"))
            self.assertEqual(brain.calls, 1)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
