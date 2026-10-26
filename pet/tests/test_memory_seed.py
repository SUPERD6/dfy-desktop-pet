# -*- coding: utf-8 -*-
"""记忆打底：个人库有内容时，空的 memory.json 不应一直是空的。"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brain import Brain  # noqa: E402


class MemorySeedTest(unittest.TestCase):
    def test_seed_from_personal_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(Path(tmp), {"personal": {"enabled": False}}, log=lambda *a: None)
            self.assertEqual(len(brain.memory_store.facts), 0)
            brain.personal = SimpleNamespace(
                fields={"name": "测试人", "school": "测试大学", "direction": "构建 Agent"},
                profile_lines=["喜欢安静的环境", "晚上效率高"],
                preferences=[("回答方式", "短一点")],
            )
            added = brain.seed_memory_from_personal()
            self.assertGreaterEqual(added, 3)
            texts = [item["text"] for item in brain.memory_store.facts]
            self.assertTrue(any("测试人" in text for text in texts))
            self.assertTrue(any("短一点" in text for text in texts))

    def test_seed_is_skipped_when_facts_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(Path(tmp), {"personal": {"enabled": False}}, log=lambda *a: None)
            brain.memory_store.add_fact("已经记住的一件事", importance=2)
            brain.personal = SimpleNamespace(fields={"name": "不会写入"}, profile_lines=[], preferences=[])
            self.assertEqual(brain.seed_memory_from_personal(), 0)


if __name__ == "__main__":
    unittest.main()
