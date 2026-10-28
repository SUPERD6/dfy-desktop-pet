# -*- coding: utf-8 -*-
"""给小鲸鱼「设计」一副自己的嗓子：用 Qwen3-TTS VoiceDesign 按文字描述生成几段参考音频（每种语气一段）。

不克隆任何真人或商用音色（以前的 GPT-SoVITS 参考音频是 edge-tts 晓伊合成的，有授权隐患）；
生成结果放 pet/cache/voice/mingyue/（不进 Git），之后由 tools/qwen_tts_server.py 用 0.6B-Base 克隆，保证音色稳定。

在 qwen-tts 的 venv 里跑（GPU）：
    .tools/qwen-tts-venv/Scripts/python.exe tools/design_voice.py
    .tools/qwen-tts-venv/Scripts/python.exe tools/design_voice.py --voice "另一种声音描述" --force
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICES = ROOT / "pet" / "cache" / "voice"
os.environ.setdefault("HF_HOME", str(ROOT / ".tools" / "hf-cache"))

PRESETS = {
    # v1：第一版，按「元气傲娇鲸鱼娘」描述
    "mingyue": ("十七八岁的少女声音，音色清亮、甜软、有一点点奶音，语速稍快，咬字清楚，"
                "带一点傲娇和撒娇，像动画里元气又有点小骄傲的鲸鱼娘，不要播音腔"),
    # v2：按 pet/character/小鲸鱼-角色卡.md 的声线「软、懒、带鼻音；傲娇时上扬，炸毛时破音」
    "mingyue-v2": ("十七八岁的少女声音，声线软软的、有点慵懒、带一点鼻音，像刚睡醒；语速偏慢，尾音轻轻拖长，"
                   "傲娇时尾音上扬，着急时声音会小小地破音；咬字清楚，不要播音腔，不要成熟御姐音"),
}
# 各语气要按她的性格来（角色卡：软、懒、带鼻音；傲娇时上扬，炸毛时破音），但必须听得出是同一个人：
# 2026-10-28 实测旧「开心」参考音高 388Hz、平常 225Hz，开心时短句飙到 560Hz 像换了个人。
# 所以语气描述都强调「音高和平常差不多」，生成后再按音高检查（见 PITCH_BAND）。
CHARACTER_TONES = {
    "happy": ("开心，懒洋洋地带着笑意，尾音轻轻上扬，像吃到白饭时的小得意；音高和平常说话差不多，不要尖叫，不要变成另一个人",
              "哇，是白米饭！主人最好了，本鲸今天要吃三大碗，嘿嘿。"),
    "sad": ("委屈难过，软软的、慢一点、鼻音重一点，像小声嘟囔；音高和平常差不多",
            "主人一整天都不理我……本鲸一个人在任务栏上，等了好久好久。"),
    "angry": ("傲娇地炸毛，嘴硬，尾音上扬，偶尔小小地破音，但还是那个软软的声音，不凶、不吼；音高只比平常略高一点",
              "哼！说好的白饭呢？再不喂我，本鲸就要罢工了，真的会罢工哦！"),
    "shy": ("害羞，小声，有点结巴，鼻音更重，声音往下收；音高和平常差不多",
            "才、才没有很想你呢……只是刚好路过你的窗口而已啦。"),
}
PITCH_BAND = (0.85, 1.3)  # 情绪参考的中位音高 / 平常那段的中位音高，超出就重来
TONES = {
    "neutral": ("平常聊天的语气，轻松自然", "本鲸叫小鲸鱼，住在你的桌面上。今天也要记得好好吃饭，不许熬夜哦。"),
    "happy": ("很开心，声音上扬、带笑意", "哇，是白米饭！主人最好了，本鲸今天要吃三大碗，嘿嘿。"),
    "sad": ("有点委屈难过，声音低一点、慢一点", "主人一整天都不理我……本鲸一个人在任务栏上，等了好久好久。"),
    "angry": ("气鼓鼓的傲娇生气，但不凶", "哼！说好的白饭呢？再不喂我，本鲸就要罢工了，真的会罢工哦！"),
    "shy": ("害羞，小声，有点结巴", "才、才没有很想你呢……只是刚好路过你的窗口而已啦。"),
}


def _median_f0(audio, sr) -> float:
    import librosa
    import numpy as np

    f0, _voiced, _p = librosa.pyin(np.asarray(audio, dtype="float32"), fmin=120, fmax=700, sr=sr)
    f0 = f0[~np.isnan(f0)]
    return float(np.median(f0)) if len(f0) else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="mingyue", choices=sorted(PRESETS), help="音色名，输出到 pet/cache/voice/<name>/")
    parser.add_argument("--voice", default="", help="自定义声音描述（不填用预设）")
    parser.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--tones", default="", help="只重做这些语气（逗号分隔），其余保留，例如 happy,sad,angry,shy")
    parser.add_argument("--tries", type=int, default=5, help="每段最多生成几次，挑音高最接近平常的")
    args = parser.parse_args()

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    OUT = VOICES / args.name
    args.voice = args.voice or PRESETS[args.name]
    OUT.mkdir(parents=True, exist_ok=True)
    model = Qwen3TTSModel.from_pretrained(args.model, device_map="cuda:0", dtype=torch.bfloat16)
    old_meta = {}
    if (OUT / "voice.json").is_file():
        old_meta = json.loads((OUT / "voice.json").read_text(encoding="utf-8")).get("tones", {})
    redo = {t.strip() for t in args.tones.split(",") if t.strip()}
    meta = {"voice": args.voice, "model": args.model, "tones": {}}
    base_f0 = None
    for tone, (style, text) in TONES.items():
        if tone in CHARACTER_TONES and redo:
            style, text = CHARACTER_TONES[tone]
        path = OUT / f"{tone}.wav"
        if path.exists() and not args.force and tone not in redo:
            print("已有", path.name)
            if tone in old_meta:
                style, text = old_meta[tone].get("style", style), old_meta[tone].get("text", text)
        else:
            best = None
            for attempt in range(max(1, args.tries)):
                wavs, sr = model.generate_voice_design(text=text, instruct=f"{args.voice}；{style}", language="Chinese")
                f0 = _median_f0(wavs[0], sr)
                ratio = f0 / base_f0 if base_f0 else 1.0
                print(f"  {tone} 第{attempt + 1}次：音高 {f0:.0f}Hz（平常的 {ratio:.2f} 倍）")
                score = abs(ratio - 1.1) if tone != "neutral" else 0  # 情绪略高一点点最自然
                if best is None or score < best[0]:
                    best = (score, wavs[0], sr, f0)
                if PITCH_BAND[0] <= ratio <= PITCH_BAND[1]:
                    break
            _, audio, sr, f0 = best
            buf = io.BytesIO()  # libsndfile 在 Windows 上打不开中文路径：先写内存再落盘
            sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
            path.write_bytes(buf.getvalue())
            print("生成", path.name, f"{len(audio) / sr:.1f}s 音高 {f0:.0f}Hz")
        if tone == "neutral":
            audio, sr = sf.read(io.BytesIO(path.read_bytes()), dtype="float32")
            base_f0 = _median_f0(audio, sr)
        meta["tones"][tone] = {"file": path.name, "text": text, "style": style}
    (OUT / "voice.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("完成：", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
