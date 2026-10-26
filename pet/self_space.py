# -*- coding: utf-8 -*-
"""她自己的文件夹（小屋）安全写入。

只允许写 ``pet/home`` 下的文本/图片；拒绝 ``..``、绝对路径、敏感文件。
Agent 的「写自己的小屋」自动放行也走这里的同一个根，不碰项目代码。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

TEXT_SUFFIXES = {".md", ".txt", ".json"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def home_root(base_dir: Path) -> Path:
    return (Path(base_dir) / "home").resolve()


def safe_target(root: Path, relative: str) -> Path | None:
    """把相对路径限制在 root 内；不合法返回 None。"""
    text = str(relative or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or any(part == ".." for part in text.split("/")):
        return None
    target = (Path(root) / text).resolve()
    try:
        target.relative_to(Path(root).resolve())
    except ValueError:
        return None
    if target.suffix.lower() not in (TEXT_SUFFIXES | IMAGE_SUFFIXES):
        return None
    return target


def write_home_text(root: Path, relative: str, text: str) -> Path | None:
    target = safe_target(root, relative)
    if target is None:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return target


def save_note(root: Path, folder: str, text: str, title: str = "") -> Path | None:
    """写一条带时间戳的笔记，如 home/notes/2026-10-25-1855-自省.md。"""
    stamp = time.strftime("%Y-%m-%d-%H%M")
    slug = "".join(ch for ch in (title or "note") if ch.isalnum() or ch in "-_")[:24] or "note"
    body = f"# {title or '笔记'}\n\n{time.strftime('%Y-%m-%d %H:%M')}\n\n{text.strip()}\n"
    return write_home_text(root, f"{folder}/{stamp}-{slug}.md", body)
