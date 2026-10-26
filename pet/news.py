# -*- coding: utf-8 -*-
"""看新闻：拉公开 RSS 标题，交给大脑挑一条聊；不抓正文、不登录。"""
from __future__ import annotations

import re
import time
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = (
    ("Hacker News", "https://hnrss.org/frontpage"),
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MingyuePet/1.0"}
_cache: dict = {"at": 0.0, "items": []}


def _clean(text: str) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def fetch_headlines(limit: int = 6, timeout: float = 8.0, force: bool = False) -> list[dict]:
    """返回 [{title, link, source}]；15 分钟内复用缓存；失败返回空列表。"""
    if not force and _cache["items"] and time.time() - _cache["at"] < 900:
        return _cache["items"][:limit]
    items: list[dict] = []
    for source, url in FEEDS:
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                root = ET.fromstring(response.read(512 * 1024))
            for node in root.findall(".//item")[:limit]:
                title = _clean(node.findtext("title") or "")
                link = _clean(node.findtext("link") or "")
                if title:
                    items.append({"title": title[:160], "link": link, "source": source})
                if len(items) >= limit:
                    break
        except Exception:
            continue
        if len(items) >= limit:
            break
    _cache["at"] = time.time()
    _cache["items"] = items
    return items[:limit]


def headlines_text(items: list[dict]) -> str:
    lines = []
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. [{item.get('source')}] {item.get('title')}")
    return "\n".join(lines)
