# -*- coding: utf-8 -*-
"""app-interact-8：QQ 来访者只拿到「来访者版」此刻；不碰 Tk、不改共享的 live_context。"""
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

PET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PET))

from app import PetApp  # noqa: E402
from brain import Brain  # noqa: E402


class VisitorContextTest(unittest.TestCase):
    def test_qq_reply_uses_visitor_context_off_main_thread(self):
        seen = {}

        class FakeBrain:
            def reply(self, text, live_context=None):
                seen["context"] = live_context
                return "嗯", "llm"

        class Exploding:
            def __getattr__(self, name):  # 任何碰 Tk / 生活状态的调用都算失败
                raise AssertionError(f"QQ 线程不该碰 {name}")

        fake = SimpleNamespace(brain=FakeBrain(), _visitor_context="周三 10:00；你在桌面上溜达",
                               root=Exploding(), living=Exploding(), reminders=Exploding())
        result = {}
        worker = threading.Thread(target=lambda: result.setdefault("r", PetApp._qq_reply(fake, "你在干嘛")))
        worker.start()
        worker.join(5)
        self.assertEqual(result.get("r"), "嗯")
        self.assertEqual(seen["context"], "周三 10:00；你在桌面上溜达")
        self.assertNotIn("主人好像在", seen["context"])

    def test_reply_override_does_not_touch_shared_context(self):
        folder = Path(tempfile.mkdtemp())
        try:
            shutil.copy(PET / "persona.md", folder)
            brain = Brain(folder, {"llm": {"enabled": False}, "personal": {"enabled": False}}, log=lambda *a: None)
            brain.live_context = "主人好像在写论文"
            captured = []
            original = brain.memory_block
            brain.memory_block = lambda q="", include_personal=True: captured.append(original(q, include_personal)) or ""
            brain._llm_ready = lambda: True
            brain._call_llm = lambda text: brain._build_messages(text) and "好"
            brain.reply("在吗", live_context="周三 10:00")
            self.assertIn("周三 10:00", captured[-1])
            self.assertNotIn("写论文", captured[-1])
            self.assertEqual(brain.live_context, "主人好像在写论文")
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
