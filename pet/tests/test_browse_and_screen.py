# -*- coding: utf-8 -*-
"""living-4 / senses-2：翻文件只翻白名单、排除个人数据；看屏幕前查所有可见窗口。"""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import living  # noqa: E402
import safety  # noqa: E402


class BrowseTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_personal_and_state_files_never_picked(self):
        names = ["config.json", "life.json", "appetite.json", "reminders.json", "memory.json", "overview.json",
                 "learned_notes.md", "PARTNER.md", "工作总览.html", "memory.json.bak-20261027", ".secret_x",
                 "0123456789abcdef0123456789abcdef.json", "token_notes.md"]
        for name in names:
            (self.dir / name).write_text("x", encoding="utf-8")
        (self.dir / "notes.md").write_text("可以翻", encoding="utf-8")
        found = [p.name for _, p in living.browse_candidates([(self.dir, 1)], time.time() - 3600)]
        self.assertEqual(found, ["notes.md"])

    def test_depth_limit(self):
        deep = self.dir / "a" / "b"
        deep.mkdir(parents=True)
        (deep / "deep.md").write_text("x", encoding="utf-8")
        self.assertEqual(living.browse_candidates([(self.dir, 0)], 0), [])
        self.assertEqual(len(living.browse_candidates([(self.dir, 2)], 0)), 1)


class ScreenPrivacyTest(unittest.TestCase):
    def test_any_visible_sensitive_window_blocks(self):
        self.assertEqual(safety.screen_has_sensitive(["VS Code", "招商银行 - Edge"]), "招商银行 - Edge")
        self.assertEqual(safety.screen_has_sensitive(["VS Code", "Claude"]), "")
        self.assertEqual(safety.screen_has_sensitive(["我的日记"], ["日记"]), "我的日记")


if __name__ == "__main__":
    unittest.main()
