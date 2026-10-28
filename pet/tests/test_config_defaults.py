# -*- coding: utf-8 -*-
"""app-window-1：默认值集中在 config_defaults；只把主人改过的值写回 config.json。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PET))

import config_defaults as cd  # noqa: E402
from interactions import FEED_LINES  # noqa: E402

PROJECT = PET.parent
SNAPSHOT = PET / "tests" / "fixtures" / "config_defaults_snapshot.json"


def normalized(config: dict) -> dict:
    text = json.dumps(config, ensure_ascii=False).replace(
        json.dumps(str(PROJECT), ensure_ascii=False)[1:-1], "<PROJECT>")
    return json.loads(text)


class ConfigDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_same_as_old_setdefault_block(self):
        """改动前用空配置跑旧代码得到的快照：搬家后必须逐键相等。"""
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(normalized(cd.default_config(PROJECT, FEED_LINES)), snapshot)

    def test_save_only_changed_and_stable(self):
        defaults = cd.default_config(PROJECT, FEED_LINES)
        config = cd.apply_defaults({"mute": True, "proactive": {"daily_limit": 3}, "custom": 1}, PROJECT, FEED_LINES)
        path = self.dir / "config.json"
        cd.save_config(path, config, defaults)
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved, {"mute": True, "proactive": {"daily_limit": 3}, "custom": 1})
        before = path.read_bytes()
        cd.save_config(path, cd.apply_defaults(json.loads(before), PROJECT, FEED_LINES), defaults)
        self.assertEqual(path.read_bytes(), before)  # 什么都没改，再存一遍字节不变

    def test_new_default_reaches_old_config(self):
        """老配置没覆盖的键，跟着代码里的新默认值走。"""
        old_saved = {"mute": True}
        config = cd.apply_defaults(dict(old_saved), PROJECT, FEED_LINES)
        self.assertEqual(config["voice_backend"], cd.default_config(PROJECT, FEED_LINES)["voice_backend"])

    def test_first_slim_keeps_backup(self):
        path = self.dir / "config.json"
        full = cd.default_config(PROJECT, FEED_LINES)
        path.write_text(json.dumps(full, ensure_ascii=False), encoding="utf-8")
        cd.save_config(path, full, full)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {})
        self.assertTrue((self.dir / "config.json.bak-full").exists())


if __name__ == "__main__":
    unittest.main()
