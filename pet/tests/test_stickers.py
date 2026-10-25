# -*- coding: utf-8 -*-
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stickers import StickerBook  # noqa: E402


def make_book(items):
    folder = Path(tempfile.mkdtemp(prefix="dfy_stk_"))
    for item in items:
        (folder / item["file"]).write_bytes(b"x" * 600)
    (folder / "index.json").write_text(json.dumps({"stickers": items}, ensure_ascii=False), encoding="utf-8")
    return StickerBook(folder, recent=2, rng=random.Random(0))


ITEMS = [
    {"file": "eat.webp", "alt": "大肥鱼抱着白饭碗腮帮鼓鼓", "tags": ["卖萌", "吃饭"]},
    {"file": "angry.webp", "alt": "大肥鱼抱臂皱眉拒绝接单", "tags": ["生气", "拒绝"]},
    {"file": "sleep.webp", "alt": "鲸鱼娘抱着枕头睡得香", "tags": ["睡觉"]},
    {"file": "happy.webp", "alt": "大肥鱼举拳露出笑脸", "tags": ["开心", "加油"]},
]


class StickerBookTest(unittest.TestCase):
    def test_emotion_maps_to_tag(self):
        book = make_book(ITEMS)
        self.assertEqual(book.pick("hungry")["file"], "eat.webp")
        self.assertEqual(book.pick("angry")["file"], "angry.webp")

    def test_text_overlap_breaks_ties(self):
        book = make_book(ITEMS)
        self.assertEqual(book.pick("", text="困了，想抱着枕头睡觉")["file"], "sleep.webp")

    def test_no_match_returns_none(self):
        book = make_book(ITEMS)
        self.assertIsNone(book.pick("", text="xyz"))

    def test_recent_ones_are_not_repeated(self):
        book = make_book(ITEMS + [{"file": "eat2.webp", "alt": "大肥鱼偷吃小鱼干", "tags": ["吃饭"]}])
        first = book.pick("hungry")["file"]
        second = book.pick("hungry")["file"]
        self.assertNotEqual(first, second)

    def test_missing_folder_is_empty(self):
        book = StickerBook(Path(tempfile.mkdtemp()) / "nope")
        self.assertEqual(len(book), 0)
        self.assertIsNone(book.pick("happy"))


if __name__ == "__main__":
    unittest.main()
