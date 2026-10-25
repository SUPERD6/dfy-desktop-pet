# -*- coding: utf-8 -*-
"""共用大脑的流式接口：OpenAI chat-completions SSE 与 Anthropic Messages SSE（DSH 0.2.x 用后者）。"""
import json
import shutil
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

PET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PET))

from api_server import SharedBrainServer, compact_summary  # noqa: E402
from brain import Brain  # noqa: E402

PORT = 8797


def post(path, body, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")


def sse_events(text):
    events = []
    for frame in text.strip().split("\n\n"):
        data = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
        kind = [line[7:] for line in frame.splitlines() if line.startswith("event: ")]
        if data:
            events.append((kind[0] if kind else None, data[0]))
    return events


class StreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = Path(tempfile.mkdtemp())
        shutil.copy(PET / "persona.md", folder)
        cls.brain = Brain(folder, {"llm": {"enabled": False}, "personal": {"enabled": False}}, log=lambda *_: None)
        cls.server = SharedBrainServer(cls.brain, {"server": {"port": PORT}}, log=lambda *_: None)
        ok, msg = cls.server.start()
        assert ok, msg

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_anthropic_messages_stream_order(self):
        ctype, text = post("/v1/messages", {"stream": True, "model": "dfy-companion", "messages": [
            {"role": "user", "content": [{"type": "text", "text": "在吗"}]}]},
            {"x-api-key": "sk-local", "anthropic-version": "2023-06-01"})
        self.assertIn("text/event-stream", ctype)
        events = sse_events(text)
        kinds = [k for k, _ in events]
        self.assertEqual(kinds[0], "message_start")
        self.assertEqual(kinds[1], "content_block_start")
        self.assertEqual(kinds[-3:], ["content_block_stop", "message_delta", "message_stop"])
        for kind, data in events:  # 事件名与 data.type 必须一致（DSH 会校验）
            self.assertEqual(json.loads(data)["type"], kind)
        reply = "".join(json.loads(d)["delta"]["text"] for k, d in events if k == "content_block_delta")
        self.assertTrue(reply)
        self.assertEqual(json.loads(events[-2][1])["delta"]["stop_reason"], "end_turn")

    def test_compaction_is_summary_and_not_remembered(self):
        before = len(self.brain.memory_store.history)
        _, text = post("/v1/messages", {"stream": True, "messages": [
            {"role": "user", "content": [{"type": "text", "text": "第一句"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "回答"}]},
            {"role": "user", "content": [{"type": "text", "text": "请总结"}]}]},
            {"x-deepseek-harness-compact": "1"})
        reply = "".join(json.loads(d)["delta"]["text"] for k, d in sse_events(text) if k == "content_block_delta")
        self.assertIn("主人：第一句", reply)
        self.assertEqual(len(self.brain.memory_store.history), before)

    def test_openai_stream_ends_with_done(self):
        _, text = post("/v1/chat/completions", {"stream": True, "messages": [{"role": "user", "content": "嗨"}]})
        frames = [d for _, d in sse_events(text)]
        self.assertEqual(frames[-1], "[DONE]")
        content = "".join(json.loads(f)["choices"][0]["delta"].get("content", "") for f in frames[:-1])
        self.assertTrue(content)

    def test_compact_summary_helper(self):
        self.assertEqual(compact_summary([]), "（之前没有可总结的对话）")


if __name__ == "__main__":
    unittest.main()
