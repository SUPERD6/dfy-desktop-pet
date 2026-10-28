# -*- coding: utf-8 -*-
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from appetite import Appetite, usage_tokens  # noqa: E402


def fresh(daily=4000):
    return Appetite(Path(tempfile.mkdtemp()) / "appetite.json", daily_snack=daily, log=lambda *_: None)


class AppetiteTest(unittest.TestCase):
    def test_usage_parsing(self):
        self.assertEqual(usage_tokens({"prompt_tokens": 900, "completion_tokens": 100,
                                       "prompt_cache_hit_tokens": 600}), (1000, 600))
        self.assertEqual(usage_tokens({"total_tokens": 50, "prompt_tokens_details": {"cached_tokens": 20}}), (50, 20))
        self.assertEqual(usage_tokens(None), (0, 0))

    def test_chat_is_free_of_allowance_but_counts_as_eating(self):
        a = fresh()
        a.eat({"total_tokens": 1200}, "chat")
        self.assertEqual(a.eaten_today(), 1200)
        self.assertEqual(a.allowance, 4000)

    def test_autonomous_spends_allowance_until_hungry(self):
        a = fresh(daily=1000)
        self.assertTrue(a.can_afford("diary", 600))
        a.eat({"total_tokens": 700}, "diary")
        self.assertFalse(a.can_afford("diary", 600))
        self.assertTrue(a.can_afford("chat", 600))  # 主人找她不拦
        self.assertTrue(a.hungry_for_tokens())

    def test_feeding_adds_allowance(self):
        a = fresh(daily=0)
        self.assertEqual(a.feed("diamond"), 8000)
        self.assertEqual(a.allowance, 8000)

    def test_token_budget_can_be_set_and_custom_fed(self):
        a = fresh(daily=1000)
        self.assertEqual(a.set_allowance(2500), 2500)
        self.assertEqual(a.allowance, 2500)
        self.assertEqual(a.add_allowance(500), 500)
        self.assertEqual(a.allowance, 3000)
        self.assertEqual(a.set_daily_snack(6000), 6000)
        a.eat({"total_tokens": 700}, "self_improve")
        self.assertEqual(a.eaten_by("self_improve"), 700)

    def test_new_foods_have_distinct_token_values(self):
        a = fresh(daily=0)
        for food in ("carrot", "apple", "seaweed", "plant"):
            self.assertGreater(a.feed(food), 0)
        self.assertGreater(a.allowance, 0)

    def test_listener_and_persistence(self):
        a = fresh()
        got = []
        a.listeners.append(lambda tokens, purpose: got.append((tokens, purpose)))
        a.eat({"total_tokens": 300}, "remark")
        self.assertEqual(got, [(300, "remark")])
        again = Appetite(a.path, log=lambda *_: None)
        self.assertEqual(again.eaten_today(), 300)

    def test_new_day_tops_up_and_keeps_history(self):
        a = fresh(daily=2000)
        a.eat({"total_tokens": 1900}, "diary")
        a.data["day"] = "2000-01-01"
        a.rollover(time.time())
        self.assertEqual(a.allowance, 2000)
        self.assertEqual(a.data["history"][-1]["eaten"], 1900)
        self.assertIn("今天吃了", a.summary())

    def test_add_allowance_increases_allowance_and_fed_total(self):
        a = fresh(daily=0)
        before_allowance = a.allowance
        before_fed = int(a.data.get("fed_total", 0))
        self.assertEqual(a.add_allowance(500), 500)
        self.assertEqual(a.allowance, before_allowance + 500)
        self.assertEqual(int(a.data.get("fed_total", 0)), before_fed + 500)

    def test_set_allowance_sets_exactly(self):
        a = fresh(daily=0)
        self.assertEqual(a.set_allowance(321), 321)
        self.assertEqual(a.allowance, 321)


if __name__ == "__main__":
    unittest.main()
