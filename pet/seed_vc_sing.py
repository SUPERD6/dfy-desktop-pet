# -*- coding: utf-8 -*-
"""用 Seed-VC 把一段源唱/音频转换成她的音色（唱歌变声）。

依赖本机 .tools/SeedVC 环境与 checkpoints；首次调用会加载约 2GB 模型。
只处理用户主动选择的本地音频；输出写到 pet/home/songs/。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
TOOL = PROJECT / ".tools" / "SeedVC"
SEEDVC = TOOL / "seed-vc"
VENV_PY = TOOL / ".venv" / "Scripts" / "python.exe"
CHECKPOINT = SEEDVC / "checkpoints" / "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"
CONFIG = SEEDVC / "configs" / "presets" / "config_dit_mel_seed_uvit_whisper_base_f0_44k.local.yml"
REFERENCE = PROJECT / "pet" / "cache" / "gptsovits" / "ref.wav"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def available() -> bool:
    return VENV_PY.is_file() and (SEEDVC / "inference.py").is_file() and CHECKPOINT.is_file() and CONFIG.is_file()


def _to_wav(source: Path, work: Path) -> Path:
    if source.suffix.lower() == ".wav":
        return source
    try:
        import imageio_ffmpeg

        ff = imageio_ffmpeg.get_ffmpeg_exe()
        target = work / "source.wav"
        subprocess.run([ff, "-y", "-i", str(source), "-ac", "1", "-ar", "44100", str(target)],
                       capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
        if target.is_file():
            return target
    except Exception:
        pass
    return source


def convert(source: Path, out_dir: Path, diffusion_steps: int = 20,
            log=print) -> Path:
    """转换一段音频，返回生成的 wav；失败抛 RuntimeError。"""
    if not available():
        raise RuntimeError("Seed-VC 环境/模型未就绪")
    source = Path(source)
    if not source.is_file():
        raise RuntimeError("源音频不存在")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    wav = _to_wav(source, work)
    cmd = [
        str(VENV_PY), str(SEEDVC / "inference.py"),
        "--source", str(wav),
        "--target", str(REFERENCE),
        "--output", str(out_dir),
        "--checkpoint", str(CHECKPOINT),
        "--config", str(CONFIG),
        "--diffusion-steps", str(max(6, min(40, int(diffusion_steps)))),
        "--f0-condition", "True",
        "--fp16", "True",
    ]
    env = dict(os.environ)
    env["HF_HOME"] = str(TOOL / ".cache")
    env["TRANSFORMERS_CACHE"] = str(TOOL / ".cache")
    try:
        proc = subprocess.run(cmd, cwd=str(SEEDVC), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900,
                              env=env, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Seed-VC 转换超时（>15 分钟）")
    candidates = sorted(out_dir.glob("vc_*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        detail = (proc.stderr or proc.stdout or "")[-500:]
        raise RuntimeError("Seed-VC 没有输出音频：" + detail.replace("\n", " ")[:300])
    return candidates[0]
