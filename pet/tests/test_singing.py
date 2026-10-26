# -*- coding: utf-8 -*-
"""歌本测试：不依赖网络/音频解码，只测文件扫描与挑歌。"""
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from singing import Songbook


def _tone(path: Path) -> None:
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)


class SongbookTest(unittest.TestCase):
    def test_scan_metadata_and_pick(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _tone(folder / "a.wav")
            _tone(folder / "b.wav")
            (folder / "songs.json").write_text(
                json.dumps({"a.wav": {"title": "小夜曲", "tags": ["安静", "夜晚"]},
                            "b.wav": {"title": "开心摸鱼", "tags": ["开心"]}},
                           ensure_ascii=False), encoding="utf-8")
            book = Songbook(folder, log=lambda *a: None)
            self.assertEqual(len(book.songs), 2)
            hit = book.pick("夜晚")
            self.assertIsNotNone(hit)
            self.assertEqual(hit.title, "小夜曲")
            self.assertIn(book.pick("").title, {"小夜曲", "开心摸鱼"})

    def test_empty_book_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Songbook(Path(tmp), log=lambda *a: None)
            self.assertEqual(book.songs, [])
            self.assertIsNone(book.pick("随便"))


if __name__ == "__main__":
    unittest.main()
