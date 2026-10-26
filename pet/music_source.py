# -*- coding: utf-8 -*-
"""她自己的音乐素材来源：iTunes 当前曲目，或本机 Music/Downloads 里的音频。

只读本地文件，不上传；找不到就让主人先放一首或拖一个文件进来。
"""
from __future__ import annotations

import time
from pathlib import Path

AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma"}


def scan_audio(limit: int = 50) -> list[Path]:
    """扫用户 Music / Downloads / Desktop 下最近修改的音频。"""
    roots = [Path.home() / "Music", Path.home() / "Downloads", Path.home() / "Desktop"]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*"):
                if len(found) >= limit * 3:
                    break
                try:
                    if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
                        found.append(path)
                except OSError:
                    continue
        except OSError:
            continue
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return found[:limit]


def pick_source() -> Path | None:
    """优先 iTunes 当前曲目，再选本机最近的一首。"""
    try:
        from itunes_control import current_track_path

        path = current_track_path()
        if path and Path(path).is_file():
            return Path(path)
    except Exception:
        pass
    files = scan_audio(limit=20)
    return files[0] if files else None
