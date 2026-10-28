# -*- coding: utf-8 -*-
"""小鲸鱼的本机嗓子：Qwen3-TTS 0.6B-Base（GPU）克隆 tools/design_voice.py 设计出的音色，按语气换参考音频。

优先用 faster-qwen3-tts（CUDA graph 加速，比原版快数倍）；没装就退回原版 qwen-tts。两者不能装在同一个环境，
所以各有一个 venv。用主 python 加 venv 的包目录来跑（venv 里复制出的 python.exe 会被 Windows 受控文件夹拦写入）：
    PYTHONPATH=.tools/fastqwen-venv/Lib/site-packages python tools/qwen_tts_server.py [--port 9881]

接口（只监听 127.0.0.1，拒绝带 Origin 的浏览器请求）：
    GET  /health                                  → {"ok": true, "tones": [...], "backend": "..."}
    POST /tts  {"text": "...", "tone": "happy"}   → audio/wav（16-bit PCM）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICES = ROOT / "pet" / "cache" / "voice"
VOICE_DIR = VOICES / "mingyue"  # main() 按 --voice 改
TEMPERATURE = 0.7  # 默认 0.9 时同一句话每次音色起伏明显，主人听着「声音变了」（2026-10-28 反馈）
os.environ.setdefault("HF_HOME", str(ROOT / ".tools" / "hf-cache"))

STATE: dict = {"model": None, "refs": {}, "prompts": {}, "backend": "", "voice": "", "lock": threading.Lock()}


def _ascii_copy(src: Path) -> str:
    """参考音频复制到纯英文路径：底层音频库在 Windows 上打不开中文路径。"""
    folder = Path(tempfile.gettempdir()) / "dafeiyu-voice"
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / src.name
    shutil.copyfile(src, dst)
    return str(dst)


def load(model_name: str, mix_ref: str = "", mix_weight: float = 0.0) -> None:
    meta = json.loads((VOICE_DIR / "voice.json").read_text(encoding="utf-8"))
    refs = {tone: (_ascii_copy(VOICE_DIR / info["file"]), info["text"])
            for tone, info in meta["tones"].items() if (VOICE_DIR / info["file"]).is_file()}
    if "neutral" not in refs:
        raise SystemExit("缺 neutral 参考音频：先跑 tools/design_voice.py")
    prompts = {}
    try:
        from faster_qwen3_tts import FasterQwen3TTS

        model = FasterQwen3TTS.from_pretrained(model_name)
        backend = "faster-qwen3-tts"
        # 音色锁定：各语气的参考音频是分别「设计」出来的，音色本来就有差别；
        # 统一用 neutral 的说话人向量，只借各语气参考音频的口吻（ICL），换心情时不再「换了个人」。
        core = model.model
        anchor = None
        for tone in ["neutral"] + [t for t in refs if t != "neutral"]:
            path, text = refs[tone]
            vcp = core._prompt_items_to_voice_clone_prompt(core.create_voice_clone_prompt(ref_audio=path, ref_text=text))
            if anchor is None:
                anchor = vcp["ref_spk_embedding"][0]
                if mix_ref and mix_weight > 0:  # 音色混合：(1-w)·设计音色 + w·另一段声音（tools/voice_blend_lab.py 试听过）
                    other = ROOT / mix_ref if not Path(mix_ref).is_absolute() else Path(mix_ref)
                    emb = core.create_voice_clone_prompt(ref_audio=_ascii_copy(other), ref_text="",
                                                         x_vector_only_mode=True)[0].ref_spk_embedding
                    anchor = (1 - mix_weight) * anchor + mix_weight * emb
                    print(f"音色混合：{other.name} {int(mix_weight * 100)}%", flush=True)
            vcp["ref_spk_embedding"] = [anchor]
            prompts[tone] = vcp
    except ImportError:
        import torch
        from qwen_tts import Qwen3TTSModel

        model = Qwen3TTSModel.from_pretrained(model_name, device_map="cuda:0", dtype=torch.bfloat16)
        backend = "qwen-tts"
    import soundfile as sf

    base, base_sr = sf.read(refs["neutral"][0], dtype="float32")
    STATE.update(model=model, refs=refs, prompts=prompts, backend=backend, voice=VOICE_DIR.name,
                 base_f0=_median_f0(base, base_sr))
    synth("嗯。", "neutral")  # 预热：第一次调用要捕获 CUDA graph，别让主人第一句话等太久
    print(f"qwen-tts 就绪（{backend}）：{model_name}，语气 {sorted(refs)}", flush=True)


def _median_f0(audio, sr) -> float:
    try:
        import librosa
        import numpy as np

        f0, _v, _p = librosa.pyin(np.asarray(audio, dtype="float32"), fmin=120, fmax=700, sr=sr)
        f0 = f0[~np.isnan(f0)]
        return float(np.median(f0)) if len(f0) else 0.0
    except Exception:
        return 0.0


def _generate(text: str, tone: str, temperature: float, max_tokens: int):
    import numpy as np

    ref_audio, ref_text = STATE["refs"][tone]
    kwargs = dict(text=text, language="Chinese", ref_text=ref_text, max_new_tokens=max_tokens)
    if STATE["prompts"]:
        kwargs.update(voice_clone_prompt=STATE["prompts"][tone], temperature=temperature)
    else:
        kwargs.update(ref_audio=ref_audio)
    with STATE["lock"]:
        wavs, sr = STATE["model"].generate_voice_clone(**kwargs)
    return np.concatenate([np.asarray(w, dtype=np.float32).reshape(-1) for w in wavs]), sr


def synth(text: str, tone: str) -> bytes:
    import numpy as np
    import soundfile as sf

    tone = tone if tone in STATE["refs"] else "neutral"
    chars = sum(1 for ch in text if ch.isalnum())
    short = chars <= 4
    if short:
        # 「嘿咻」「哼哼」这种两三个字的词最不稳：实测同一句音高能差 ±60Hz、时长忽长忽短（长句约 ±20Hz）。
        # 一律用平常语气、低温度、严格时长。
        tone = "neutral"
    # 中文约 4 字/秒，12Hz 编码：给足余量但封顶，防止生成失控（实验里出现过一句话拖到 156 秒）
    max_tokens = int(12 * (len(text) * 0.35 + (1.5 if short else 4)))
    limit_s = chars * 0.35 + 1.2 if short else len(text) * 0.4 + 3
    audio, sr = None, 24000
    if short:
        # 短词：生成 3 个，挑时长不超限、音高最接近她平常声音的（客户端会把结果永久缓存，之后每次都一样）
        best = None
        base = STATE.get("base_f0") or 0.0
        for _ in range(3):
            cand, sr = _generate(text, tone, 0.5, max_tokens)
            dur = len(cand) / sr
            f0 = _median_f0(cand, sr)
            score = (abs(f0 - base) / base if base and f0 else 1.0) + (2.0 if dur > limit_s else 0.0)
            if best is None or score < best[0]:
                best = (score, cand)
        audio = best[1]
    else:
        for temperature in (TEMPERATURE, 0.5):
            audio, sr = _generate(text, tone, temperature, max_tokens)
            if len(audio) / sr <= limit_s:
                break
            print(f"tts 生成过长（{len(audio) / sr:.1f}s），降温重来", flush=True)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def _ok_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost") or self.headers.get("Origin"):
            self.send_error(403)
            return False
        return True

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if not self._ok_host():
            return
        if self.path.rstrip("/") == "/health":
            self._json(200, {"ok": STATE["model"] is not None, "tones": sorted(STATE["refs"]),
                             "backend": STATE["backend"], "voice": STATE["voice"]})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        if not self._ok_host():
            return
        if self.path.rstrip("/") != "/tts":
            self.send_error(404)
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 16384)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            text = str(body.get("text") or "").strip()[:300]
            tone = str(body.get("tone") or "neutral")
            if not text:
                self._json(400, {"error": "empty text"})
                return
            started = time.time()
            data = synth(text, tone)
            print(f"tts {tone} {len(text)}字 {time.time() - started:.2f}s", flush=True)
        except Exception as exc:
            self._json(500, {"error": str(exc)[:300]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9881)
    parser.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-0.6B-Base")
    parser.add_argument("--voice", default=os.environ.get("DFY_VOICE") or "mingyue",
                        help="用哪套参考音频：pet/cache/voice/<voice>/")
    parser.add_argument("--mix-ref", default="", help="混进来的另一段声音（相对项目根），如 docs/seedvc-sample.wav")
    parser.add_argument("--mix-weight", type=float, default=0.0, help="混合比例 0~1")
    args = parser.parse_args()
    global VOICE_DIR
    VOICE_DIR = VOICES / args.voice
    load(args.model, args.mix_ref, max(0.0, min(1.0, args.mix_weight)))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"listening http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
