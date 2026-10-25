# -*- coding: utf-8 -*-
"""从「蓝色大肥鱼档案馆」同步表情包到 pet/assets/stickers/（本机缓存，不进 Git）。

来源：https://fisharchive.cc/stickers/index.json（站点 robots 允许抓取图片）。
许可：CC BY-NC-SA 4.0（非商业、署名、相同方式共享）；单张作品权利归各自创作者。
只下 preview（webp，约 50KB/张）；已存在的文件跳过，可反复运行增量更新。

用法：python tools/sync_stickers.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

INDEX_URL = "https://fisharchive.cc/stickers/index.json"
OUT = Path(__file__).resolve().parents[1] / "pet" / "assets" / "stickers"
UA = {"User-Agent": "dfy-desktop-pet/1.0 (personal, non-commercial)"}


def fetch(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as resp:
        return resp.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只下前 N 张（调试用）")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    index = json.loads(fetch(INDEX_URL).decode("utf-8"))
    stickers = index.get("stickers") or []
    if args.limit:
        stickers = stickers[: args.limit]

    entries, fetched, failed = [], 0, 0
    for item in stickers:
        url = item.get("preview") or item.get("large") or ""
        name = url.rsplit("/", 1)[-1]
        if not name:
            continue
        path = OUT / name
        if not path.exists() or path.stat().st_size < 512:
            try:
                path.write_bytes(fetch(url))
                fetched += 1
                time.sleep(0.15)  # 对静态站客气一点
            except Exception as exc:
                failed += 1
                print(f"下载失败 {name}: {exc}")
                continue
        entries.append({
            "file": name,
            "alt": item.get("alt", ""),
            "tags": item.get("tags") or [],
            "width": item.get("width"),
            "height": item.get("height"),
            "source": item.get("original") or url,
        })

    manifest = {
        "name": index.get("name", "蓝色大肥鱼档案馆"),
        "site": index.get("site", "https://fisharchive.cc/"),
        "license": index.get("license"),
        "synced": time.strftime("%Y-%m-%d %H:%M"),
        "stickers": entries,
    }
    (OUT / "index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (OUT / "LICENSE.txt").write_text(
        "表情包来自「蓝色大肥鱼档案馆」https://fisharchive.cc/ 的社区同人整理。\n"
        "角色二次设计依 CC BY-NC-SA 4.0（https://creativecommons.org/licenses/by-nc-sa/4.0/）：\n"
        "仅限非商业使用、需署名、二创须以相同协议共享；单张作品的权利归各自创作者。\n"
        "本目录是本机缓存，不随仓库分发；需要时运行 tools/sync_stickers.py 重新下载。\n",
        encoding="utf-8",
    )
    print(f"表情包 {len(entries)} 张（新下载 {fetched}，失败 {failed}）→ {OUT}")
    return 0 if entries else 1


if __name__ == "__main__":
    raise SystemExit(main())
