# -*- coding: utf-8 -*-
"""Seedance key 解析的纯函数测试；不联网、不生成视频。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_gen import _model, resolve_key  # noqa: E402


class VideoGenTest(unittest.TestCase):
    def test_key_from_secret_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".secret_ark_key").write_text("test-key", encoding="utf-8")
            self.assertEqual(resolve_key(Path(tmp), {}), "test-key")

    def test_model_default_and_override(self):
        self.assertTrue(_model({}))
        self.assertEqual(_model({"video_gen": {"model": "custom-model"}}), "custom-model")


if __name__ == "__main__":
    unittest.main()
