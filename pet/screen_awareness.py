# -*- coding: utf-8 -*-
"""看看屏幕：截一张本机桌面图，并取前台窗口标题/活动。

默认只在本机保存最近一张截图，不自动上传。聊天里只会用前台窗口标题和
活动判断来回答；没有配置视觉模型时，不假装看见了画面细节。
"""
from __future__ import annotations

import time
from pathlib import Path


def cleanup_screens(out_dir: Path, keep: int = 5, max_age_hours: float = 24.0) -> int:
    """定期清理旧截图：只留最近 keep 张，且超过 max_age_hours 的删掉。返回删除数。"""
    removed = 0
    try:
        files = sorted(out_dir.glob("screen-*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        now = time.time()
        for index, path in enumerate(files):
            too_old = now - path.stat().st_mtime > max_age_hours * 3600
            if index >= keep or too_old:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    except Exception:
        pass
    return removed


def capture_screen(out_dir: Path, all_screens: bool = True, keep: int = 5,
                   max_age_hours: float = 24.0, bbox: tuple | None = None) -> Path | None:
    """截取桌面，返回 PNG 路径；失败返回 None。每次顺手清理旧图。

    bbox 给出时只截这一块（默认调用方传前台窗口所在的显示器，不把别的屏幕拍进去）。
    """
    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(bbox=bbox, all_screens=True) if bbox else ImageGrab.grab(all_screens=all_screens)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"screen-{stamp}.png"
        image.save(path, "PNG")
        try:
            latest = out_dir / "screen-last.png"
            image.save(latest, "PNG")
        except Exception:
            pass
        cleanup_screens(out_dir, keep=keep, max_age_hours=max_age_hours)
        return path
    except Exception:
        return None


def screen_context() -> dict:
    """前台窗口标题、活动判断、空闲秒数、鼠标位置；取不到给空值。"""
    title = ""
    activity = ""
    idle = 0.0
    cursor = None
    try:
        import desktop

        title = str(desktop.foreground_title() or "").strip()
        activity = str(desktop.guess_activity(title) or "").strip()
        idle = float(desktop.user_idle_seconds() or 0.0)
        cursor = desktop.cursor_pos()
    except Exception:
        pass
    return {"title": title, "activity": activity, "idle_seconds": round(idle, 1),
            "cursor": cursor, "captured_at": int(time.time())}


def vision_copy(path: Path, out_dir: Path, max_width: int = 960, quality: int = 65) -> Path | None:
    """把截图压成适合视觉模型的 JPG，减少上传体积/像素计费；失败返回原图。"""
    try:
        from PIL import Image

        image = Image.open(path).convert("RGB")
        if image.width > max_width:
            height = max(1, round(image.height * max_width / image.width))
            image = image.resize((max_width, height), Image.LANCZOS)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "screen-vision.jpg"
        image.save(target, "JPEG", quality=quality, optimize=True)
        return target
    except Exception:
        return path


def context_line(ctx: dict) -> str:
    title = str(ctx.get("title") or "").strip()
    activity = str(ctx.get("activity") or "").strip()
    idle = float(ctx.get("idle_seconds") or 0)
    if not title:
        return "屏幕这边没读到前台窗口（可能桌面或权限受限）。"
    line = f"主人当前前台窗口是「{title[:120]}」"
    if activity:
        line += f"，看起来在{activity}"
    if idle >= 60:
        line += f"，已经 {int(idle // 60)} 分钟没动键鼠"
    cursor = ctx.get("cursor")
    if isinstance(cursor, (tuple, list)) and len(cursor) == 2:
        line += f"；鼠标在屏幕 ({int(cursor[0])}, {int(cursor[1])}) 附近"
    return line
