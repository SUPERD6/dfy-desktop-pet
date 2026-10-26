# -*- coding: utf-8 -*-
"""桌面原生整合的纯函数测试；不写真实注册表、不点系统托盘。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_integration import autostart_command, clamp_opacity  # noqa: E402
from hotkeys import hotkey_labels  # noqa: E402
from screen_awareness import context_line  # noqa: E402


class DesktopIntegrationTest(unittest.TestCase):
    def test_screen_context_reads_window_and_activity(self):
        line = context_line({"title": "Visual Studio Code - 大肥鱼", "activity": "写代码",
                             "idle_seconds": 120})
        self.assertIn("Visual Studio Code", line)
        self.assertIn("写代码", line)
        self.assertIn("2 分钟", line)

    def test_screen_context_handles_missing_window(self):
        self.assertIn("没读到前台窗口", context_line({}))


    def test_hotkey_labels_are_stable(self):
        labels = hotkey_labels()
        self.assertEqual(len(labels), 3)
        self.assertTrue(any("显示/隐藏" in item for item in labels))
        self.assertTrue(any("聊天" in item for item in labels))
    def test_autostart_command_uses_pythonw_and_absolute_app_path(self):
        cmd = autostart_command(Path(r"D:\pet\app.py"), Path(r"C:\Python311\python.exe"))
        self.assertIn("pythonw.exe", cmd)
        self.assertIn("app.py", cmd)
        self.assertTrue(cmd.startswith('"'))

    def test_opacity_is_clamped(self):
        self.assertEqual(clamp_opacity(1.5), 1.0)
        self.assertEqual(clamp_opacity(0.0), 0.1)
        self.assertEqual(clamp_opacity("0.6"), 0.6)
        self.assertEqual(clamp_opacity(None), 1.0)


if __name__ == "__main__":
    unittest.main()
