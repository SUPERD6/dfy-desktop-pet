# -*- coding: utf-8 -*-
"""全局快捷键服务（Windows RegisterHotKey），纯 ctypes，不新增依赖。

在别的程序前台时也能把桌宠叫出来/藏起来、打开聊天、切换鼠标穿透。
注册失败（组合键被占用）会返回失败列表，不让桌宠启动失败。
"""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

# 动作 → (修饰键, 虚拟键)。默认 Ctrl+Alt；可在需要时改这里。
HOTKEYS: dict[str, tuple[int, int]] = {
    "show_hide": (MOD_CONTROL | MOD_ALT, 0x4D),   # M
    "chat": (MOD_CONTROL | MOD_ALT, 0x4A),       # J
    "passthrough": (MOD_CONTROL | MOD_ALT, 0x50),  # P
}


class HotkeyService:
    def __init__(self, on_action, log=print, hotkeys: dict[str, tuple[int, int]] | None = None):
        self.on_action = on_action
        self.log = log
        self.hotkeys = dict(hotkeys or HOTKEYS)
        self.registered: list[str] = []
        self.failed: list[str] = []
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()

    def start(self) -> bool:
        if not IS_WINDOWS:
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, daemon=True, name="mingyue-hotkeys")
        self._thread.start()
        self._ready.wait(timeout=1.0)
        return bool(self.registered)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        for index, (action, (mods, vk)) in enumerate(self.hotkeys.items(), start=1):
            if user32.RegisterHotKey(None, index, mods | MOD_NOREPEAT, vk):
                self.registered.append(action)
            else:
                self.failed.append(action)
        self._ready.set()
        if not self.registered:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                index = int(msg.wParam)
                action = list(self.hotkeys.keys())[index - 1] if 0 < index <= len(self.hotkeys) else ""
                if action in self.registered:
                    try:
                        self.on_action(action)
                    except Exception:
                        pass
        for index in range(1, len(self.hotkeys) + 1):
            user32.UnregisterHotKey(None, index)

    def stop(self) -> None:
        if not IS_WINDOWS or not self._thread_id:
            return
        try:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.6)
        self._thread = None


def hotkey_labels() -> list[str]:
    """给菜单/气泡用的中文说明，不改注册。"""
    return ["Ctrl+Alt+M 显示/隐藏", "Ctrl+Alt+J 打开聊天", "Ctrl+Alt+P 鼠标穿透"]
