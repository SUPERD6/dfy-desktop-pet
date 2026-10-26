# -*- coding: utf-8 -*-
"""唱歌：把「她唱首歌」落到一个可扩展的歌本上。

现在用 `pet/assets/songs/` 里预先放好的短曲（默认是工具生成的哼唱占位曲）；
以后要接 GPT-SoVITS / ACE / DiffSinger，只要把渲染好的 mp3/wav 丢进这个目录即可，
不需要改桌宠代码。歌本信息可写在 songs.json（可选）。
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac"}


@dataclass
class Song:
    title: str
    path: Path
    tags: str = ""


class Songbook:
    def __init__(self, folder: Path, log=print):
        self.folder = Path(folder)
        self.log = log if callable(log) else print
        self.songs: list[Song] = []
        self.reload()

    def reload(self) -> None:
        self.songs = []
        if not self.folder.exists():
            return
        metadata = {}
        try:
            meta_path = self.folder / "songs.json"
            if meta_path.exists():
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    metadata = raw
        except Exception:
            metadata = {}
        for path in sorted(self.folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
                continue
            meta = metadata.get(path.name) if isinstance(metadata.get(path.name), dict) else {}
            title = str(meta.get("title") or path.stem).strip()
            tags = " ".join(str(item) for item in (meta.get("tags") or [])) if isinstance(meta.get("tags"), list) else str(meta.get("tags") or "")
            self.songs.append(Song(title=title, path=path, tags=tags))
        self.log(f"歌本：{len(self.songs)} 首（{self.folder}）")

    def pick(self, query: str = "") -> Song | None:
        if not self.songs:
            return None
        query = (query or "").strip()
        if query:
            hits = [song for song in self.songs
                    if query in song.title or any(token and token in song.tags for token in [query])]
            if hits:
                return random.choice(hits)
        return random.choice(self.songs)
