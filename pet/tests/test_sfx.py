# -*- coding: utf-8 -*-
"""点她时的合成音效：能生成合法的短 WAV。"""
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sfx import RATE, Sfx, synth  # noqa: E402


class SfxTest(unittest.TestCase):
    def test_synth_is_short_and_in_range(self):
        for kind in ("press", "release"):
            samples = synth(kind)
            self.assertTrue(0.05 < len(samples) / RATE < 0.3, kind)
            self.assertLessEqual(max(abs(x) for x in samples), 1.0)

    def test_wav_written_once(self):
        sfx = Sfx(Path(tempfile.mkdtemp()))
        path = sfx.path("release", 1)
        with wave.open(str(path)) as w:
            self.assertEqual(w.getframerate(), RATE)
            self.assertGreater(w.getnframes(), 0)
        self.assertEqual(sfx.path("release", 1), path)

    def test_disabled_does_nothing(self):
        folder = Path(tempfile.mkdtemp())
        Sfx(folder, enabled=lambda: False).play("press")
        self.assertEqual(list(folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
