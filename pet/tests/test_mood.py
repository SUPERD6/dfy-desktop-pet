# -*- coding: utf-8 -*-
"""心情：回复末尾的〔心情 ±N 情绪〕拆得干净、会回稳、只说一次；压缩前先读日记。"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain import Brain, split_mood  # noqa: E402
from life import LifeState  # noqa: E402


class SplitMoodTest(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(split_mood("嘿嘿，本鲸厉害吧\n〔心情 +2 得意〕"), ("嘿嘿，本鲸厉害吧", (2, "得意")))
        self.assertEqual(split_mood("呜……\n【心情 -1 担心】"), ("呜……", (-1, "担心")))
        self.assertEqual(split_mood("好呀 (心情＋3开心)"), ("好呀", (3, "开心")))
        self.assertEqual(split_mood("没有标记"), ("没有标记", None))

    def test_clamped_and_not_left_in_text(self):
        clean, mood = split_mood("行吧〔心情 +9 开心〕")
        self.assertEqual(mood, (3, "开心"))
        self.assertNotIn("心情", clean)


class FeelTest(unittest.TestCase):
    def test_feel_decays_and_unsaid_once(self):
        now = time.time()
        state = LifeState({"mood": 50}, last_update=now)
        state.feel(+2, "得意", "主人夸我", now=now)
        self.assertGreater(state.needs["mood"], 50)
        self.assertEqual(state.top_feeling(), "得意")
        self.assertIn("得意", state.pop_unsaid())
        self.assertEqual(state.pop_unsaid(), "")          # 只说一次
        state.advance(now + 9 * 3600, hour=15)            # 9 小时后基本回稳
        self.assertEqual(state.top_feeling(), "")
        self.assertEqual(len(state.mood_log), 1)

    def test_no_change_no_log(self):
        state = LifeState(None)
        state.feel(0, "平静")
        self.assertEqual(state.mood_log, [])


class DiaryBeforeCompressTest(unittest.TestCase):
    def test_consolidate_prompt_includes_previous_diary(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "persona.md").write_text("测试人设", encoding="utf-8")
        (folder / "home" / "diary").mkdir(parents=True)
        (folder / "home" / "diary" / "2026-10-24.md").write_text("今天主人陪我看了雨。", encoding="utf-8")
        brain = Brain(folder, {"llm": {"enabled": True, "api_key": "sk-test-0000000000000000000000"},
                               "personal": {"enabled": False}}, log=lambda *_: None)
        brain.memory_store.add_episode("昨天聊了考试", 1.0, 2.0)
        for index in range(20):
            brain.memory_store.remember_exchange(f"第{index}句", "嗯")
        seen = {}

        def fake(messages, options, key):
            seen["prompt"] = messages[-1]["content"]
            return '{"diary": "接着昨天，今天又聊了考试。", "facts": []}'

        brain._request_chat_with_retries = fake
        brain.consolidate()
        self.assertIn("昨天聊了考试", seen["prompt"])
        self.assertIn("今天主人陪我看了雨", seen["prompt"])


if __name__ == "__main__":
    unittest.main()
