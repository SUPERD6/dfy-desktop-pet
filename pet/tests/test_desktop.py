# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import desktop  # noqa: E402
from desktop import Win, compute_platforms, guess_activity, landing_below, platform_at  # noqa: E402

WORK = (0, 0, 1920, 1040)


class PlatformTest(unittest.TestCase):
    def test_ground_always_exists(self):
        platforms = compute_platforms([], WORK)
        self.assertEqual(len(platforms), 1)
        self.assertEqual(platforms[0].y, 1040)

    def test_window_top_is_a_platform_minus_covered_part(self):
        top = Win(1, "上层", 300, 200, 700, 600)       # 盖住了下层窗口顶边的 300..700
        lower = Win(2, "下层", 100, 400, 1100, 900)
        platforms = compute_platforms([top, lower], WORK)
        on_lower = sorted((p.x1, p.x2) for p in platforms if p.hwnd == 2)
        self.assertEqual(on_lower, [(100, 300), (700, 1100)])
        self.assertTrue(any(p.hwnd == 1 and p.y == 200 for p in platforms))

    def test_maximized_and_tiny_windows_are_skipped(self):
        maxed = Win(1, "最大化", 0, 0, 1920, 1040)
        narrow = Win(2, "窄", 500, 300, 560, 600)
        self.assertEqual(len(compute_platforms([maxed, narrow], WORK)), 1)

    def test_standing_and_falling(self):
        win = Win(5, "编辑器", 200, 500, 900, 900)
        platforms = compute_platforms([win], WORK)
        self.assertEqual(platform_at(platforms, 400, 503).hwnd, 5)
        self.assertIsNone(platform_at(platforms, 1000, 503))
        self.assertEqual(landing_below(platforms, 400, 100).hwnd, 5)   # 先落到窗口上
        self.assertEqual(landing_below(platforms, 1000, 100).hwnd, 0)  # 窗口外直接落地

    def test_guess_activity(self):
        self.assertEqual(guess_activity("app.py - 大肥鱼 - Visual Studio Code"), "写代码")
        self.assertEqual(guess_activity("哔哩哔哩 (゜-゜)つロ 干杯~"), "看视频")
        self.assertEqual(guess_activity("随便什么"), "")

    def test_win32_helpers_do_not_crash(self):
        self.assertIsInstance(desktop.list_windows(), list)
        self.assertGreaterEqual(desktop.user_idle_seconds(), 0.0)
        self.assertIsInstance(desktop.foreground_title(), str)


if __name__ == "__main__":
    unittest.main()
