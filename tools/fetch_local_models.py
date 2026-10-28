# -*- coding: utf-8 -*-
"""下载桌宠用的本机神经网络模型到 pet/cache/models/（不进 Git，也不随公开库导出）。

    python tools/fetch_local_models.py            # 下载全部（约 200MB）
    python tools/fetch_local_models.py ears       # 只下耳朵：唤醒词 + VAD + SenseVoice
    python tools/fetch_local_models.py embed      # 只下中文向量模型（语义记忆）

模型来源：
- 唤醒词：k2-fsa/sherpa-onnx kws-zipformer-zh-en-3M（Apache-2.0）
- 说话检测：silero_vad（MIT）
- 听写 + 语气 + 声音事件：SenseVoice-Small int8（FunAudioLLM，模型许可见其仓库）
- 向量：BAAI/bge-small-zh-v1.5 的 ONNX 版（MIT）
"""
from __future__ import annotations

import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "pet" / "cache" / "models"
GH = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
HF = "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main"

EARS = [
    (f"{GH}/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2", "kws"),
    (f"{GH}/asr-models/silero_vad.onnx", "vad/silero_vad.onnx"),
    (f"{GH}/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2", "sensevoice"),
]
EMBED = [
    (f"{HF}/onnx/model_quantized.onnx", "bge-small-zh/model.onnx"),
    (f"{HF}/tokenizer.json", "bge-small-zh/tokenizer.json"),
]


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"已有：{dest.relative_to(ROOT)}")
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"下载：{url}")
    with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    tmp.replace(dest)
    return dest


def fetch(items) -> None:
    for url, target in items:
        if url.endswith(".tar.bz2"):
            folder = MODELS / target
            if folder.exists() and any(folder.iterdir()):
                print(f"已有：{folder.relative_to(ROOT)}")
                continue
            archive = _download(url, MODELS / "_downloads" / url.rsplit("/", 1)[1])
            folder.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():  # 去掉压缩包里的顶层目录
                    parts = Path(member.name).parts
                    if len(parts) < 2 or ".." in parts or member.issym() or member.islnk():
                        continue
                    member.name = str(Path(*parts[1:]))
                    tar.extract(member, folder)
            archive.unlink(missing_ok=True)
        else:
            _download(url, MODELS / target)


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "ears"):
        fetch(EARS)
    if which in ("all", "embed"):
        fetch(EMBED)
    print("完成：", MODELS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
