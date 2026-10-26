import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from interactions import (DRAG_LINES, FOOD_EMOJI, FEED_LINES, INNER_VOICE_LINES, actions_in,
                          pat_event, pat_fallbacks, render_food, state_for_actions, step_flight,
                          visible_ratio)


class InteractionHelpersTest(unittest.TestCase):
    def test_persona_lines_cover_all_interactions(self):
        self.assertIn("起飞咯——", DRAG_LINES)
        self.assertEqual(set(FEED_LINES), {"rice", "fish", "cake", "tangyuan", "diamond"})
        self.assertTrue(all(FEED_LINES.values()))
        self.assertTrue(INNER_VOICE_LINES)
        self.assertFalse(any("操" in line or "骂" in line or "爹" in line
                             for line in INNER_VOICE_LINES))

    def test_flight_applies_gravity_friction_and_advances_position(self):
        x, y, vx, vy, landed = step_flight(100, 100, 100, 0, 0.016, 0, 0, 500, 500)
        self.assertGreater(x, 100)
        self.assertGreater(y, 100)
        self.assertLess(vx, 100)
        self.assertGreater(vy, 0)
        self.assertFalse(landed)

    def test_rapid_pats_are_merged_into_one_event(self):
        self.assertIn("一下", pat_event(1))
        self.assertIn("5", pat_event(5))
        self.assertIn("12", pat_event(12))
        self.assertTrue(pat_fallbacks(10))
        self.assertNotEqual(pat_event(1), pat_event(10))

    def test_flight_bounces_off_edge_and_stops_on_floor(self):
        x, y, vx, vy, landed = step_flight(495, 100, 500, 0, 0.05, 0, 0, 500, 480)
        self.assertEqual(x, 500)
        self.assertLess(vx, 0)

        x, y, vx, vy, landed = 100, 470, 0, 500, False
        for _ in range(60):
            x, y, vx, vy, landed = step_flight(x, y, vx, vy, 0.016, 0, 0, 500, 480)
            if landed:
                break
        self.assertEqual(y, 480)
        self.assertTrue(landed)
        self.assertEqual(vy, 0)

    def test_toss_slides_on_ground_then_stops(self):
        x, y, vx, vy, landed = 100, 480, 600, 0, False
        steps = 0
        while not landed and steps < 600:
            x, y, vx, vy, landed = step_flight(x, y, vx, vy, 0.016, 0, 0, 5000, 480)
            steps += 1
        self.assertTrue(landed)
        self.assertGreater(x, 150)   # 落地后还滑了一段
        self.assertLess(steps, 120)  # 两秒内停下

    def test_every_food_has_a_prop_and_bites_shrink_it(self):
        self.assertEqual(set(FOOD_EMOJI), set(FEED_LINES))
        for food in FOOD_EMOJI:
            whole = visible_ratio(render_food(food, 64, bites=0))
            bitten = visible_ratio(render_food(food, 64, bites=3))
            self.assertGreater(whole, 0.15, food)
            self.assertLess(bitten, whole * 0.9, food)

    def test_actions_in_reply_become_states(self):
        self.assertEqual(actions_in("（尾巴一甩）好呀 *捂脸*"), ["尾巴一甩", "捂脸"])
        self.assertEqual(state_for_actions("（尾巴一甩）好呀"), "happy")
        self.assertEqual(state_for_actions("哼(叉腰)才不要"), "angry")
        self.assertEqual(state_for_actions("*歪头* 这是啥"), "confused")
        self.assertEqual(state_for_actions("（扒两口饭）香"), "eat")
        self.assertIsNone(state_for_actions("没有动作的一句话"))
        self.assertIsNone(state_for_actions("（某某大学）"))  # 认不出来的括号不乱做动作


if __name__ == "__main__":
    unittest.main()
