# -*- coding: utf-8 -*-
"""公开上网、截图清理和关系层的纯函数测试。"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from life import LifeState  # noqa: E402
from screen_awareness import cleanup_screens  # noqa: E402
from web_tools import _strip_html, validate_url  # noqa: E402


class WebToolsTest(unittest.TestCase):
    def test_rejects_local_and_private_urls(self):
        with self.assertRaises(ValueError):
            validate_url("http://127.0.0.1:8765/chat")
        with self.assertRaises(ValueError):
            validate_url("http://localhost/x")
        self.assertIn("example.com", validate_url("https://example.com/a"))

    def test_strips_html(self):
        text = _strip_html("<html><style>x</style><p>你好 <b>世界</b></p></html>")
        self.assertIn("你好 世界", text)
        self.assertNotIn("<", text)


class ScreenCleanupTest(unittest.TestCase):
    def test_keeps_only_recent_screens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(6):
                path = root / f"screen-{index}.png"
                path.write_bytes(b"x")
                old = time.time() - (index * 3600)
                import os
                os.utime(path, (old, old))
            removed = cleanup_screens(root, keep=2, max_age_hours=100)
            self.assertGreaterEqual(removed, 4)
            self.assertLessEqual(len(list(root.glob("screen-*.png"))), 2)


class RelationStateTest(unittest.TestCase):
    def test_trust_and_closeness_grow_and_persist(self):
        state = LifeState()
        before = state.closeness
        state.on_event("pat")
        self.assertGreater(state.closeness, before)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "life.json"
            state.save(path)
            loaded = LifeState.load(path, time.time())
            self.assertAlmostEqual(loaded.closeness, state.closeness, places=1)
            self.assertAlmostEqual(loaded.trust, state.trust, places=1)


if __name__ == "__main__":
    unittest.main()
