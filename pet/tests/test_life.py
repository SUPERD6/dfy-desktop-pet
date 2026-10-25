# -*- coding: utf-8 -*-
import random
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from life import ACTIVITIES, LifeState  # noqa: E402

NOON = time.mktime((2026, 10, 25, 12, 0, 0, 0, 0, -1))


def pick_many(state, now, hour, user_active, n=400):
    rng = random.Random(7)
    return Counter(state.choose(now, hour, user_active, rng) for _ in range(n))


class LifeStateTest(unittest.TestCase):
    def test_time_passes_and_is_capped(self):
        state = LifeState({"hunger": 10, "boredom": 0}, last_update=NOON)
        state.advance(NOON + 60 * 60, hour=14)
        self.assertGreater(state.needs["hunger"], 10)
        self.assertGreater(state.needs["boredom"], 0)
        state = LifeState(None, last_update=NOON)
        minutes = state.advance(NOON + 3 * 86400, hour=9)
        self.assertEqual(minutes, 12 * 60)

    def test_sleep_restores_energy(self):
        state = LifeState({"energy": 20}, last_update=NOON)
        state.advance(NOON + 3600, hour=2, asleep=True)
        self.assertGreater(state.needs["energy"], 50)

    def test_events(self):
        state = LifeState({"hunger": 80, "social": 80}, last_update=NOON)
        state.on_event("fed:rice")
        self.assertLess(state.needs["hunger"], 50)
        state.on_event("chat")
        self.assertLess(state.needs["social"], 60)

    def test_night_prefers_nap_and_hunger_prefers_snack(self):
        night = time.mktime((2026, 10, 25, 23, 30, 0, 0, 0, -1))
        tired = LifeState({"energy": 25, "hunger": 20}, last_update=night)
        self.assertEqual(pick_many(tired, night, 23, False).most_common(1)[0][0], "nap")
        hungry = LifeState({"hunger": 95, "energy": 90, "boredom": 10}, last_update=NOON)
        self.assertEqual(pick_many(hungry, NOON, 12, False).most_common(1)[0][0], "snack")

    def test_user_active_means_no_disturbing(self):
        state = LifeState({"social": 100, "boredom": 100, "energy": 90}, last_update=NOON)
        counts = pick_many(state, NOON, 15, True)
        for activity in ("call_master", "browse_file", "climb_window", "dance", "peek_edge"):
            self.assertEqual(counts[activity], 0, activity)
        idle_counts = pick_many(state, NOON, 15, False)
        self.assertGreater(idle_counts["call_master"] + idle_counts["climb_window"], 0)

    def test_diary_once_per_day_and_cooldowns(self):
        evening = time.mktime((2026, 10, 25, 21, 0, 0, 0, 0, -1))
        state = LifeState({"energy": 90}, last_update=evening)
        self.assertGreater(state.scores(evening, 21, False)["write_diary"], 0)
        state.mark_done("write_diary", evening)
        self.assertEqual(state.scores(evening + 60, 21, False)["write_diary"], 0)
        state.mark_done("call_master", evening)
        state.needs["social"] = 100
        self.assertEqual(state.scores(evening + 60, 21, False)["call_master"], 0)

    def test_choose_returns_known_activity(self):
        state = LifeState(None, last_update=NOON)
        self.assertIn(state.choose(NOON, 12, False, random.Random(1)), ACTIVITIES)

    def test_load_bad_file_and_roundtrip(self):
        folder = Path(tempfile.mkdtemp())
        bad = folder / "life.json"
        bad.write_text("{oops", encoding="utf-8")
        self.assertEqual(LifeState.load(bad, NOON).needs["hunger"], 30.0)
        state = LifeState({"hunger": 66}, last_update=NOON)
        state.mark_done("nap", NOON)
        state.save(bad)
        again = LifeState.load(bad, NOON)
        self.assertAlmostEqual(again.needs["hunger"], 66, places=1)
        self.assertTrue(again.asleep)

    def test_summary_is_short(self):
        state = LifeState({"hunger": 90, "energy": 10, "social": 90, "boredom": 90, "mood": 10})
        self.assertLessEqual(len(state.summary()), 40)
        self.assertEqual(state.mood_word(), "饿了")


if __name__ == "__main__":
    unittest.main()
