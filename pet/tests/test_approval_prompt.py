# -*- coding: utf-8 -*-
"""确认气泡（app-interact-1 / agent-8）：回车不放行、刚弹出时允许按不动、长预览不截断、太长只能拒绝。"""
import sys
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ApprovalPrompt  # noqa: E402


class ApprovalPromptTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # 没有桌面环境
            self.skipTest(f"没有 Tk：{exc}")
        self.root.withdraw()
        self.app = SimpleNamespace(root=self.root, display_name="小鲸鱼", W=300)
        self.results = []

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        # Tk 对象必须在主线程里回收：留给 GC 在别的线程收，Tcl 会直接崩进程（0x80000003）
        self.root = self.app = None
        import gc
        gc.collect()

    def make(self, preview="准备执行 PowerShell\n命令（完整）：\ndir", force=False):
        return ApprovalPrompt(self.app, "run_powershell", preview, self.results.append, force=force)

    def pump(self, ms):
        done = []
        self.root.after(ms, lambda: done.append(1))
        while not done:
            self.root.update()

    def test_return_key_does_not_approve(self):
        prompt = self.make()
        self.pump(700)
        prompt.focus_force()  # 气泡自己不抢焦点；这里模拟主人点了它一下，再按回车
        self.pump(50)
        prompt.event_generate("<Return>")
        self.pump(50)
        self.assertEqual(self.results, [])
        prompt.event_generate("<Escape>")
        self.pump(50)
        self.assertEqual(self.results, [False])

    def test_allow_disabled_right_after_popup(self):
        prompt = self.make()
        prompt.allow_button.invoke()  # 刚弹出：按钮还是 disabled，invoke 无效
        self.assertEqual(self.results, [])
        self.pump(700)
        prompt.allow_button.invoke()
        self.assertEqual(self.results, [True])

    def test_full_preview_and_too_long_only_reject(self):
        tail = "最后一个字是尾"
        prompt = self.make("x\n" * 50 + tail)
        texts = [w for w in prompt.winfo_children()[0].winfo_children()]
        self.assertTrue(prompt.allow_button is not None)
        prompt.destroy()
        long_prompt = self.make("行\n" * 400)
        self.assertIsNone(long_prompt.allow_button)  # 超过 300 行只给拒绝
        self.assertTrue(texts)


if __name__ == "__main__":
    unittest.main()
