# -*- coding: utf-8 -*-
"""记忆 v3：相似事实合并、按重要度淘汰、日记压缩（模型 / 兜底）、prompt 预算、Agent 也带记忆。"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PET))

import memory  # noqa: E402
from agent import AgentRunner  # noqa: E402
from brain import Brain  # noqa: E402
from memory import MemoryStore  # noqa: E402


def make_brain(folder: Path, budget: int = 1800, llm: bool = False) -> Brain:
    (folder / "persona.md").write_text("你是测试用的鲸鱼娘。", encoding="utf-8")
    config = {"llm": {"enabled": llm, "api_key": "sk-test-0000000000000000000000" if llm else ""},
              "personal": {"enabled": False}, "memory": {"prompt_budget_chars": budget}}
    return Brain(folder, config, log=lambda *_: None)


class FactTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(Path(tempfile.mkdtemp()) / "memory.json", log=lambda *_: None)

    def test_similar_fact_updates_instead_of_duplicating(self):
        self.assertTrue(self.store.add_fact("主人喜欢喝冰美式咖啡", "like"))
        self.assertFalse(self.store.add_fact("主人喜欢喝冰美式", "like"))
        self.assertEqual(len(self.store.facts), 1)
        self.assertEqual(self.store.facts[0]["text"], "主人喜欢喝冰美式")

    def test_different_likes_are_not_merged(self):
        self.store.add_fact("主人喜欢猫", "like")
        self.assertTrue(self.store.add_fact("主人喜欢狗", "like"))
        self.assertEqual(len(self.store.facts), 2)

    def test_eviction_keeps_important_and_used_facts(self):
        old = time.time() - 200 * 86400
        self.store.add_fact("主人的生日是三月五号", "event", when=old, importance=3)
        words = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
        for index in range(memory.FACTS_LIMIT + 5):  # 生成彼此不相似的事实
            a, b, c = words[index % 20], words[(index // 20) % 20], words[(index * 7) % 20]
            self.store.add_fact(f"{a}{b}{c}{a}{c}{b}{index}", "note")
        texts = [f["text"] for f in self.store.facts]
        self.assertEqual(len(texts), memory.FACTS_LIMIT)
        self.assertIn("主人的生日是三月五号", texts)  # 很旧但重要，没被挤掉

    def test_mark_used_counts_hits(self):
        self.store.add_fact("主人怕冷", "note")
        self.store.mark_used(["主人怕冷"])
        self.assertEqual(self.store.facts[0]["hits"], 1)


class ConsolidateTests(unittest.TestCase):
    def test_llm_json_becomes_diary_and_facts(self):
        folder = Path(tempfile.mkdtemp())
        brain = make_brain(folder, llm=True)
        for index in range(25):
            brain.memory_store.remember_exchange(f"今天写论文第{index}段", "加油")
        reply = json.dumps({"diary": "主人一整天在赶论文，有点累。",
                            "facts": [{"kind": "event", "text": "主人在赶论文", "importance": 3}]},
                           ensure_ascii=False)
        brain._request_chat_with_retries = lambda messages, options, key: "好的：" + reply
        diary = brain.consolidate()
        self.assertEqual(diary, "主人一整天在赶论文，有点累。")
        self.assertEqual(len(brain.memory_store.history), 50 - 20)
        self.assertEqual(brain.memory_store.facts[-1]["importance"], 3)

    def test_bad_llm_output_falls_back_to_quotes(self):
        folder = Path(tempfile.mkdtemp())
        brain = make_brain(folder, llm=True)
        for index in range(20):
            brain.memory_store.remember_exchange(f"第{index}句", "嗯")
        brain._request_chat_with_retries = lambda *a: "不是 JSON"
        diary = brain.consolidate()
        self.assertIn("第0句", diary)


class PromptTests(unittest.TestCase):
    def test_budget_drops_low_priority_blocks_first(self):
        folder = Path(tempfile.mkdtemp())
        brain = make_brain(folder, budget=300)
        brain.extra_context = "工作总览" * 200
        brain.memory_store.remember_exchange("我叫小明", "好的小明")
        block = brain.memory_block("小明")
        self.assertNotIn("工作总览工作总览", block)
        self.assertIn("最近的对话", block)

    def test_live_context_and_episode_in_prompt(self):
        folder = Path(tempfile.mkdtemp())
        brain = make_brain(folder)
        brain.live_context = "周六 15:00；你刚在打盹"
        brain.memory_store.add_episode("和主人聊了猫", 1.0, 2.0)
        prompt = brain.system_prompt("猫")
        self.assertIn("【此刻】周六 15:00", prompt)
        self.assertIn("和主人聊了猫", prompt)

    def test_agent_prompt_carries_memory(self):
        folder = Path(tempfile.mkdtemp())
        brain = make_brain(folder)
        brain.memory_store.add_fact("主人叫小明", "note", importance=3)
        runner = AgentRunner(folder, {"llm": {}, "agent": {}}, log=lambda *_: None)
        runner.memory = brain
        self.assertIn("主人叫小明", runner.system_prompt("你好"))

    def test_note_exchange_records_agent_chat(self):
        folder = Path(tempfile.mkdtemp())
        brain = make_brain(folder)
        brain.note_exchange("我喜欢吃草莓", "记住啦")
        self.assertEqual(brain.memory_store.history[-1]["content"], "记住啦")


if __name__ == "__main__":
    unittest.main()
