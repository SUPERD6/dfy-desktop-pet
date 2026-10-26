# -*- coding: utf-8 -*-
"""她自己的小屋和新闻标题的纯函数测试；不联网、不写真实 home。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from news import headlines_text  # noqa: E402
from self_space import home_root, safe_target, save_note  # noqa: E402


class SelfSpaceTest(unittest.TestCase):
    def test_safe_target_stays_inside_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = home_root(Path(tmp))
            self.assertIsNotNone(safe_target(root, "notes/today.md"))
            self.assertIsNone(safe_target(root, "../config.json"))
            self.assertIsNone(safe_target(root, "/etc/passwd"))
            self.assertIsNone(safe_target(root, "code.py"))

    def test_save_note_writes_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save_note(home_root(Path(tmp)), "notes", "今天想被摸头", title="自省")
            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            self.assertIn("今天想被摸头", path.read_text(encoding="utf-8"))

    def test_news_headlines_text_is_numbered(self):
        text = headlines_text([{"source": "HN", "title": "A"}, {"source": "BBC", "title": "B"}])
        self.assertIn("1. [HN] A", text)
        self.assertIn("2. [BBC] B", text)


if __name__ == "__main__":
    unittest.main()
