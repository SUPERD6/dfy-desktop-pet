# -*- coding: utf-8 -*-
"""大肥鱼眼里的「桌面世界」：哪些地方能站、主人在不在、主人在用什么。

- 平台 = 各个窗口露在外面的顶边 + 任务栏上沿（工作区底边）；
  被上层窗口挡住的那段顶边不算（不能站在别的窗口里面）。
- 平台计算是纯函数（输入窗口矩形列表），可以单测；Win32 读取部分失败就安静降级。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    x1: int
    x2: int
    y: int
    hwnd: int = 0          # 0 = 地面（任务栏上沿）
    title: str = ""

    @property
    def width(self) -> int:
        return self.x2 - self.x1


@dataclass(frozen=True)
class Win:
    hwnd: int
    title: str
    left: int
    top: int
    right: int
    bottom: int


def _subtract(segments: list[tuple[int, int]], cut: tuple[int, int]) -> list[tuple[int, int]]:
    out = []
    for a, b in segments:
        c, d = cut
        if d <= a or c >= b:
            out.append((a, b))
            continue
        if c > a:
            out.append((a, c))
        if d < b:
            out.append((d, b))
    return out


def compute_platforms(windows: list[Win], work_area: tuple[int, int, int, int],
                      min_width: int = 120) -> list[Platform]:
    """windows 按 Z 序从上到下排列。返回能站的平台（含地面），从上到下排序。"""
    left, top, right, bottom = work_area
    platforms = [Platform(left, right, bottom, 0, "地面")]
    for index, win in enumerate(windows):
        y = win.top
        if y <= top + 40 or y >= bottom - 40:  # 顶到屏幕上沿/贴着任务栏的窗口不当平台
            continue
        segments = [(max(left, win.left), min(right, win.right))]
        for above in windows[:index]:  # 被更上层窗口挡住的部分切掉
            if above.top <= y < above.bottom:
                segments = _subtract(segments, (above.left, above.right))
        for a, b in segments:
            if b - a >= min_width:
                platforms.append(Platform(a, b, y, win.hwnd, win.title))
    return sorted(platforms, key=lambda p: p.y)


def platform_at(platforms: list[Platform], foot_x: float, foot_y: float, tolerance: float = 6) -> Platform | None:
    """脚下踩着的平台（容差内）。"""
    for p in platforms:
        if p.x1 <= foot_x <= p.x2 and abs(p.y - foot_y) <= tolerance:
            return p
    return None


def landing_below(platforms: list[Platform], foot_x: float, foot_y: float) -> Platform | None:
    """从 foot_y 往下掉，最先接住她的平台。"""
    below = [p for p in platforms if p.x1 <= foot_x <= p.x2 and p.y >= foot_y - 2]
    return min(below, key=lambda p: p.y) if below else None


# ---------------------------------------------------------------- Win32
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _dwmapi = getattr(ctypes.windll, "dwmapi", None)
    _kernel32 = ctypes.windll.kernel32
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    DWMWA_CLOAKED = 14

    class _RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    def _title(hwnd: int) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def _cloaked(hwnd: int) -> bool:
        if _dwmapi is None:
            return False
        value = ctypes.c_int(0)
        try:
            _dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value),
                                          ctypes.sizeof(value))
        except Exception:
            return False
        return bool(value.value)

    def list_windows(exclude: set[int] | None = None) -> list[Win]:
        """可见、未最小化、有标题、不是工具窗的顶层窗口，按 Z 序从上到下。"""
        exclude = exclude or set()
        found: list[Win] = []
        own_pid = _kernel32.GetCurrentProcessId()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _lparam):
            hwnd = int(hwnd or 0)
            try:
                pid = wintypes.DWORD(0)
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == own_pid:  # 自己的窗口（桌宠、聊天窗、表情包卡片）不当平台
                    return True
                if (hwnd in exclude or not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd)
                        or _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _cloaked(hwnd)):
                    return True
                title = _title(hwnd)
                if not title or title in ("Program Manager",):
                    return True
                rect = _RECT()
                if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                if rect.right - rect.left < 200 or rect.bottom - rect.top < 120:
                    return True
                found.append(Win(hwnd, title, rect.left, rect.top, rect.right, rect.bottom))
            except Exception:
                pass
            return True

        try:
            _user32.EnumWindows(callback, 0)
        except Exception:
            return []
        return found

    def user_idle_seconds() -> float:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        try:
            if _user32.GetLastInputInfo(ctypes.byref(info)):
                return max(0.0, (_kernel32.GetTickCount() - info.dwTime) / 1000.0)
        except Exception:
            pass
        return 0.0

    def foreground_title() -> str:
        try:
            return _title(int(_user32.GetForegroundWindow() or 0))
        except Exception:
            return ""

    def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        rect = _RECT()
        try:
            if hwnd and _user32.IsWindowVisible(hwnd) and not _user32.IsIconic(hwnd) \
                    and _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        return None

else:  # 非 Windows：没有窗口平台，只有地面
    def list_windows(exclude=None) -> list[Win]:
        return []

    def user_idle_seconds() -> float:
        return 0.0

    def foreground_title() -> str:
        return ""

    def window_rect(hwnd: int):
        return None


# 主人在用什么 → 她怎么理解（只看窗口标题关键词，不读窗口内容）
ACTIVITY_HINTS = (
    (("Visual Studio Code", "PyCharm", "Cursor", "VSCodium", ".py", ".js", "IntelliJ"), "写代码"),
    (("Claude", "Codex", "ChatGPT", "DeepSeek"), "和别的 AI 干活"),
    (("Word", ".docx", "Overleaf", ".tex", "PDF", "Acrobat", "Zotero"), "看论文写东西"),
    (("bilibili", "哔哩哔哩", "YouTube", "Netflix", "爱奇艺"), "看视频"),
    (("Steam", "Genshin", "原神", "Minecraft"), "打游戏"),
    (("微信", "WeChat", "QQ", "Discord", "Slack", "Teams"), "聊天"),
    (("Chrome", "Edge", "Firefox"), "上网"),
)


def guess_activity(title: str) -> str:
    low = (title or "").lower()
    for keywords, label in ACTIVITY_HINTS:
        if any(k.lower() in low for k in keywords):
            return label
    return ""
