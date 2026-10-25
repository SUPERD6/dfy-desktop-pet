# -*- coding: utf-8 -*-
"""不弹窗自检：大脑、记忆、预合成语音、edge-tts 现场合成、ASR 可用性。"""
import json
import sys
import time
from pathlib import Path

PET = Path(__file__).resolve().parents[1] / "pet"
sys.path.insert(0, str(PET))

import tkinter as tk

from asr import ASR
from brain import Brain
from voice import VoiceEngine


def main() -> int:
    base = PET
    config = json.loads((base / "config.json").read_text(encoding="utf-8"))
    config["mute"] = False
    logs = []
    log = lambda message: (logs.append(str(message)), print("[log]", message))
    root = tk.Tk()
    root.withdraw()

    print("== Brain ==")
    brain = Brain(base, config, log=log)
    for text in ["你好", "我叫小明", "我喜欢猫", "摸摸", "喂饭", "今天写代码好累", "晚安"]:
        reply, source = brain.reply(text)
        print(f"  {text} -> [{source}] {reply}")
    print("  user_name =", brain.user_name())
    print("  facts =", brain.memory.get("facts"))

    print("== ASR ==")
    asr = ASR(language="zh-CN", log=log)
    print("  available =", asr.ok, asr.reason)

    print("== Voice ==")
    voice = VoiceEngine(root, base / "assets", base / "cache", config, log=log,
                        on_speaking=lambda speaking: print("  speaking:", speaking))
    manifest = json.loads((base / "assets" / "manifest.json").read_text(encoding="utf-8"))
    voices = manifest.get("voices") or {}
    print("  preset lines =", len(voices))
    preset_text = voices.get("welcome0", {}).get("text") or "主人好～"
    print("  playing preset:", preset_text)
    voice.speak(preset_text)
    print("  synthesizing via edge-tts:", "你好呀，我是大肥鱼，测试现场合成。")
    voice.speak("你好呀，我是大肥鱼，测试现场合成。")

    deadline = time.time() + 12
    while time.time() < deadline and not voice._queue.empty():
        root.update()
        time.sleep(0.1)
    for _ in range(20):
        root.update()
        time.sleep(0.1)
    try:
        root.destroy()
    except Exception:
        pass
    print("== errors ==")
    errors = [line for line in logs if "error" in line or "失败" in line]
    print("  " + ("\n  ".join(errors) if errors else "无"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
