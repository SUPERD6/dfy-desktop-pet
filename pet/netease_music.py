# -*- coding: utf-8 -*-
"""网易云音乐：公开搜索 + 外链播放（无登录、无私人歌单）。

只读取公开搜索接口；播放地址来自网易云公开 outer 链接，能不能播由版权决定。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0 Safari/537.36"
SEARCH_URL = "https://music.163.com/api/cloudsearch/pc"


def search(query: str, limit: int = 5, timeout: float = 12.0) -> list[dict]:
    query = str(query or "").strip()
    if not query:
        return []
    params = {"csrf_token": "", "s": query, "type": "1", "offset": "0", "limit": str(max(1, min(10, limit)))}
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://music.163.com/"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return []
    songs = ((data.get("result") or {}) if isinstance(data.get("result"), dict) else {}).get("songs") or []
    out = []
    for song in songs:
        try:
            artists = "、".join(str(a.get("name") or "") for a in (song.get("ar") or song.get("artists") or []))
            out.append({"id": int(song.get("id") or 0), "name": str(song.get("name") or ""),
                        "artists": artists, "album": str((song.get("al") or {}).get("name") or "")})
        except Exception:
            continue
    return out


def play_url(song_id: int) -> str:
    return f"https://music.163.com/song/media/outer/url?id={int(song_id)}.mp3"


def download(song_id: int, out_dir: Path, timeout: float = 60.0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"netease-{int(song_id)}.mp3"
    req = urllib.request.Request(play_url(song_id), headers={"User-Agent": UA, "Referer": "https://music.163.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read()
    if len(data) < 10_000:
        raise RuntimeError("网易云没有返回可播放的音频（可能受版权限制）")
    target.write_bytes(data)
    return target
