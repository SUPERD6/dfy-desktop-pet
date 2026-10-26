# -*- coding: utf-8 -*-
"""点她时的小音效：默认是「挤压小黄鸭」的啾啾/滴滴声，连续点会一声接一声。

原来的「啾 / 滴滴」保留为 classic 风格（config: click_sound_style）。
所有音效都由代码合成（扫频 + 谐波 + 包络），不引入外部音频素材；
播放走 winsound 异步，不经过语音用的 MCI，不会打断她说话。
"""
from __future__ import annotations

import math
import random
import struct
import sys
import wave
from pathlib import Path

RATE = 22050
VARIANTS = 4  # 每种音效几个音高略不同的版本，连点不单调


def _sweep(f0: float, f1: float, ms: float, volume: float = 0.5,
           harmonic: float = 0.35, squeal: float = 0.0, vibrato_hz: float = 34.0) -> list[float]:
    """一段扫频音，带快速起音/衰减；squeal>0 时叠一点高频气声，像橡皮鸭挤压。"""
    n = int(RATE * ms / 1000)
    out, phase = [], 0.0
    for i in range(n):
        t = i / max(1, n - 1)
        freq = f0 + (f1 - f0) * t
        phase += 2 * math.pi * freq / RATE
        wobble = 1.0 + 0.022 * math.sin(2 * math.pi * vibrato_hz * t)
        env = min(1.0, t / 0.06) * (1 - t) ** 1.2
        value = (math.sin(phase * wobble)
                 + harmonic * math.sin(2 * phase)
                 + 0.18 * math.sin(3 * phase))
        if squeal:
            value += squeal * math.sin(phase * 1.9 + 0.8)
        out.append(volume * env * value / (1 + harmonic + 0.18))
    return out


def _silence(ms: float) -> list[float]:
    return [0.0] * int(RATE * ms / 1000)


def _duck_press(pitch: float = 1.0) -> list[float]:
    """挤压小黄鸭：先「叽」一下往上翘，再快速回落。"""
    return _sweep(680 * pitch, 1180 * pitch, 140, 0.55, harmonic=0.55,
                  squeal=0.10, vibrato_hz=36)


def _duck_release(pitch: float = 1.0) -> list[float]:
    """松手回弹：三声短促的「滴滴滴」，音高略微上走。"""
    return (_sweep(1050 * pitch, 1350 * pitch, 55, 0.42, harmonic=0.5, squeal=0.08)
            + _silence(22)
            + _sweep(1200 * pitch, 1550 * pitch, 55, 0.42, harmonic=0.5, squeal=0.08)
            + _silence(18)
            + _sweep(1380 * pitch, 1780 * pitch, 60, 0.42, harmonic=0.5, squeal=0.08))


def synth(kind: str, pitch: float = 1.0, style: str = "duck") -> list[float]:
    if style == "classic":
        if kind == "press":
            return _sweep(1500 * pitch, 900 * pitch, 95, 0.45, harmonic=0.35)
        if kind == "release":
            return (_sweep(1900 * pitch, 2200 * pitch, 45, 0.35, harmonic=0.0)
                    + _silence(30)
                    + _sweep(2300 * pitch, 2700 * pitch, 50, 0.35, harmonic=0.0))
        raise ValueError(kind)
    if kind == "press":
        return _duck_press(pitch)
    if kind == "release":
        return _duck_release(pitch)
    raise ValueError(kind)


def write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))


class Sfx:
    def __init__(self, folder: Path, enabled=lambda: True, style=lambda: "duck"):
        self.folder = Path(folder)
        self.enabled = enabled
        self.style = style

    def _style(self) -> str:
        try:
            return str(self.style() or "duck")
        except Exception:
            return "duck"

    def path(self, kind: str, variant: int) -> Path:
        style = self._style()
        if style == "widget-duck":
            # 从 dsh-whale-widget 提取的小黄鸭音效（仅本机缓存，不随仓库分发）
            widget = self.folder / "widget-duck" / f"{kind}.wav"
            if widget.exists():
                return widget
            style = "duck"  # 没提取到就退回内置合成鸭
        path = self.folder / f"{kind}-{style}-{variant}.wav"
        if not path.exists():
            write_wav(path, synth(kind, (0.92, 1.0, 1.08, 1.16)[variant % VARIANTS], style))
        return path

    def play(self, kind: str) -> None:
        if sys.platform != "win32" or not self.enabled():
            return
        try:
            import winsound
            path = self.path(kind, random.randrange(VARIANTS))
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            pass
