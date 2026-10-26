# -*- coding: utf-8 -*-
"""表情包：按情绪/回复内容在本地挑一张「蓝色大肥鱼档案馆」的表情包。

不调模型、不花 token：情绪 → 标签打分，回复文本和表情描述做 2-gram 重合加分，
最近发过的不重复，再在前几名里随机，避免每次都是同一张。
素材由 tools/sync_stickers.py 缓存到 assets/stickers/（CC BY-NC-SA 4.0）。
"""
from __future__ import annotations

import json
import random
import re
from collections import deque
from pathlib import Path

# 桌宠情绪 → 表情包标签（越靠前权重越高）
EMOTION_TAGS: dict[str, tuple[str, ...]] = {
    "happy": ("开心", "卖萌", "庆祝", "比心"),
    "proud": ("得意", "点赞", "开心"),
    "shy": ("害羞", "卖萌"),
    "angry": ("生气", "拒绝", "无语"),
    "sad": ("委屈", "难过"),
    "surprised": ("震惊", "疑惑"),
    "confused": ("疑惑", "无语"),
    "sleepy": ("睡觉", "摸鱼"),
    "hungry": ("吃饭",),
    "eat": ("吃饭",),
    "talking": ("卖萌", "搞笑", "开心"),
    "thinking": ("工作", "学习", "疑惑"),
    "wave": ("打招呼", "开心"),
    "work": ("工作", "加油"),
}

_CJK_RE = re.compile(r"[一-鿿]+")


def _grams(text: str) -> set[str]:
    grams: set[str] = set()
    for run in _CJK_RE.findall(text or ""):
        grams.update(run[i:i + 2] for i in range(len(run) - 1))
    return grams


class StickerBook:
    def __init__(self, folder: Path | str, recent: int = 12, rng: random.Random | None = None):
        self.folder = Path(folder)
        self.items: list[dict] = []
        self._recent: deque[str] = deque(maxlen=recent)
        self._rng = rng or random.Random()
        self.license_note = ""
        self.reload()

    def reload(self) -> None:
        try:
            data = json.loads((self.folder / "index.json").read_text(encoding="utf-8"))
        except Exception:
            self.items = []
            return
        lic = data.get("license") or {}
        self.license_note = f"{data.get('name', '')} · {lic.get('name', '')}".strip(" ·")
        items = []
        for item in data.get("stickers") or []:
            path = self.folder / str(item.get("file") or "")
            if path.is_file():
                items.append({**item, "path": path, "_grams": _grams(item.get("alt", ""))})
        self.items = items

    def __len__(self) -> int:
        return len(self.items)

    def score(self, item: dict, emotion: str = "", text: str = "", tags: tuple[str, ...] = ()) -> float:
        wanted = tuple(tags) or EMOTION_TAGS.get(emotion, ())
        item_tags = item.get("tags") or []
        score = 0.0
        for rank, tag in enumerate(wanted):
            if tag in item_tags:
                score += 3.0 - 0.5 * rank
        if text:
            score += 0.6 * len(_grams(text) & item["_grams"])
        return score

    def pick(self, emotion: str = "", text: str = "", tags: tuple[str, ...] = (), top: int = 6) -> dict | None:
        """返回 {file, alt, tags, path, ...}；没有素材或完全不相关时返回 None。"""
        pool = [item for item in self.items if item["file"] not in self._recent] or self.items
        if not pool:
            return None
        scored = sorted(((self.score(item, emotion, text, tags), self._rng.random(), item) for item in pool),
                        key=lambda row: (row[0], row[1]), reverse=True)
        if not scored or scored[0][0] <= 0:
            return None
        floor = scored[0][0] * 0.7  # 只在接近最高分的几张里随机，别让沾边的抢了主角
        best = [row for row in scored[:top] if row[0] >= floor]
        choice = self._rng.choice(best)[2]
        self._recent.append(choice["file"])
        return choice
