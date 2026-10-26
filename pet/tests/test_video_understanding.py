# -*- coding: utf-8 -*-
"""视频 storyboard 与截图压缩的纯函数测试；不调用模型。"""
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screen_awareness import vision_copy  # noqa: E402
from video_understanding import make_storyboard, sample_frames  # noqa: E402


class VideoUnderstandingTest(unittest.TestCase):
    def test_storyboard_combines_frames(self):
        frames = [(0.0, Image.new("RGB", (640, 360), "red")),
                  (1.0, Image.new("RGB", (640, 360), "green")),
                  (2.0, Image.new("RGB", (640, 360), "blue"))]
        with tempfile.TemporaryDirectory() as tmp:
            path = make_storyboard(frames, Path(tmp), max_width=960)
            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 1000)

    def test_missing_video_returns_no_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sample_frames(Path(tmp) / "none.mp4"), [])

    def test_vision_copy_downscales(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "screen.png"
            Image.new("RGB", (2000, 1000), "white").save(source)
            target = vision_copy(source, Path(tmp), max_width=960, quality=60)
            self.assertIsNotNone(target)
            self.assertTrue(target.exists())
            with Image.open(target) as image:
                self.assertLessEqual(image.width, 960)


if __name__ == "__main__":
    unittest.main()
