# -*- coding: utf-8 -*-
"""身上区域互动的纯函数测试。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from touch_zones import touch_emotion, touch_event, touch_fallbacks, zone_for  # noqa: E402


class TouchZonesTest(unittest.TestCase):
    def test_zone_detection(self):
        # 100x300 人物框：头、耳朵、脸、肚子、尾巴、手
        self.assertEqual(zone_for(50, 20, 0, 0, 100, 300), "head")
        self.assertEqual(zone_for(5, 60, 0, 0, 100, 300), "ear")
        self.assertEqual(zone_for(50, 80, 0, 0, 100, 300), "face")
        self.assertEqual(zone_for(50, 160, 0, 0, 100, 300), "belly")
        self.assertEqual(zone_for(90, 200, 0, 0, 100, 300), "tail")
        self.assertEqual(zone_for(50, 280, 0, 0, 100, 300), "hand")
        self.assertEqual(zone_for(-20, 20, 0, 0, 100, 300), "body")

    def test_each_zone_has_event_and_fallback(self):
        for zone in ("head", "face", "ear", "belly", "tail", "hand", "body"):
            self.assertTrue(touch_event(zone, 3))
            self.assertTrue(touch_fallbacks(zone, 3))
            self.assertIn(touch_emotion(zone), ("shy", "surprised", "angry", "happy"))


if __name__ == "__main__":
    unittest.main()
