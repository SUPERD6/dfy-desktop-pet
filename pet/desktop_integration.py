# -*- coding: utf-8 -*-
"""桌面原生整合：开机自启、系统托盘、鼠标穿透。

这些能力来自 dsh-pet-indesktop（MerZlin，MIT）的窗口/托盘设计，按本项目的
tkinter 架构重写，不搬它的 PySide6 代码。所有系统调用只影响本进程的窗口和
当前用户的 HKCU，不写系统级设置。
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE = "DafeiyuMingyueDesktopPet"
IS_WINDOWS = sys.platform == "win32"

# Windows 扩展窗口样式
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020


def autostart_command(app_path: Path, python_exe: Path | None = None) -> str:
    """生成 HKCU Run 里的自启命令；用 pythonw 避免黑框，路径全绝对。"""
    exe = Path(python_exe or sys.executable)
    if IS_WINDOWS and exe.name.lower() == "python.exe":
        exe = exe.with_name("pythonw.exe")
    return f'"{exe}" "{Path(app_path).resolve()}"'


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE)
        return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool, app_path: Path, python_exe: Path | None = None) -> bool:
    """开关开机自启；写后回读验证，防止被安全软件静默拦截。"""
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_VALUE, 0, winreg.REG_SZ,
                                  autostart_command(app_path, python_exe))
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_VALUE)
                except FileNotFoundError:
                    pass
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if not enabled:
                try:
                    winreg.QueryValueEx(key, AUTOSTART_VALUE)
                except FileNotFoundError:
                    return True
                return False
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE)
            return bool(value)
    except OSError:
        return False


def click_through(root, enabled: bool) -> bool:
    """给 tkinter 窗口加/去 WS_EX_TRANSPARENT（鼠标穿透）。失败返回 False。"""
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = int(root.winfo_id())
        # Tk 的 winfo_id 在有 wrapper 时可能是子窗口；顶层句柄优先用 GetParent。
        parent = user32.GetParent(hwnd)
        if parent:
            hwnd = parent
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        return True
    except Exception:
        return False


def tray_available() -> bool:
    try:
        import pystray  # noqa: F401
        return True
    except Exception:
        return False


class TrayService:
    """pystray 可选托盘；没有装 pystray 时整个服务静默不启动。"""

    def __init__(self, app):
        self.app = app
        self.icon = None
        self._thread = None

    def start(self) -> bool:
        if not tray_available() or not getattr(self.app, "root", None):
            return False
        try:
            import pystray
            from PIL import Image

            image = None
            try:
                states = getattr(getattr(self.app, "library", None), "states", {}) or {}
                frames = states.get("idle") or next(iter(states.values()))
                image = frames[0][0].convert("RGBA")
            except Exception:
                image = Image.new("RGBA", (64, 64), (74, 124, 240, 255))
            image = image.copy()
            image.thumbnail((64, 64))

            app = self.app

            def later(fn):
                try:
                    app.root.after(0, fn)
                except Exception:
                    pass

            def toggle_visible(icon, item):
                later(app.toggle_visible)

            def open_chat(icon, item):
                later(app.open_chat)

            def open_settings(icon, item):
                later(app.open_settings)

            def toggle_through(icon, item):
                later(lambda: app.set_click_through(not app.config.get("click_through", False)))

            def toggle_autostart(icon, item):
                later(lambda: app.set_autostart(not app.config.get("autostart", False)))

            def quit_app(icon, item):
                later(app.quit)

            menu = pystray.Menu(
                pystray.MenuItem("显示 / 隐藏小鲸鱼", toggle_visible, default=True),
                pystray.MenuItem("打开聊天", open_chat),
                pystray.MenuItem("桌宠设置", open_settings),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("鼠标穿透", toggle_through,
                                 checked=lambda item: bool(app.config.get("click_through", False))),
                pystray.MenuItem("开机自启", toggle_autostart,
                                 checked=lambda item: bool(app.config.get("autostart", False))),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", quit_app),
            )
            self.icon = pystray.Icon("mingyue-dafeiyu", image, "小鲸鱼 · 大肥鱼", menu)
            self._thread = threading.Thread(target=self.icon.run, daemon=True, name="mingyue-tray")
            self._thread.start()
            return True
        except Exception:
            self.icon = None
            return False

    def stop(self) -> None:
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:
            pass
        self.icon = None


def clamp_opacity(value) -> float:
    try:
        return max(0.1, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0
