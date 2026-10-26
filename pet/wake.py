# -*- coding: utf-8 -*-
"""喊名字唤醒：听到「大肥鱼 / 肥鱼 / 小肥鱼」后回调，让桌宠不用点按钮就能回应。

实现说明（诚实的边界）：
- 用 SpeechRecognition 的连续分段录音 + 在线识别（Google），所以麦克风音频会发到在线识别服务；
  不需要额外安装模型，但需要联网，隐私敏感时请在设置里关掉。
- 能量阈值 + 每段最多 5 秒，只有检测到说话才会调用识别，不做全天候云端流式上传。
- 将来可以换成离线 Vosk/Whisper：只要实现同样的 `start/stop` + 回调即可。
"""
from __future__ import annotations

import threading
import time


class WakeListener:
    def __init__(self, phrases, language="zh-CN", on_wake=None, log=print):
        self.phrases = [str(p).strip() for p in (phrases or ["大肥鱼", "肥鱼"]) if str(p).strip()]
        self.language = language or "zh-CN"
        self.on_wake = on_wake
        self.log = log if callable(log) else print
        self.available = False
        self.reason = ""
        self._stop = threading.Event()
        self._thread = None
        self._sr = None
        try:
            import speech_recognition as sr  # type: ignore
            self._sr = sr
            self.available = True
        except Exception as exc:
            self.reason = f"缺少 SpeechRecognition：{exc}"

    # ---------- 对外 ----------
    def start(self) -> tuple[bool, str]:
        if not self.available:
            return (False, self.reason)
        if self._thread is not None and self._thread.is_alive():
            return (True, "已经在听了")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="wake-listener", daemon=True)
        self._thread.start()
        return (True, "开始听名字了")

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    # ---------- 内部 ----------
    def _loop(self) -> None:
        sr = self._sr
        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        recognizer.energy_threshold = 300
        failures = 0
        while not self._stop.is_set():
            try:
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.4)
                    audio = recognizer.listen(source, timeout=6, phrase_time_limit=5)
                if self._stop.is_set():
                    return
                try:
                    text = recognizer.recognize_google(audio, language=self.language)
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as exc:
                    failures += 1
                    self.log(f"唤醒识别服务不可用（{failures}）：{exc}")
                    if failures >= 3:
                        self.log("唤醒识别连续失败，先停下；网络恢复后可在菜单里重新开。")
                        return
                    time.sleep(2.0)
                    continue
                failures = 0
                text = (text or "").strip()
                if not text:
                    continue
                for phrase in self.phrases:
                    if phrase and phrase in text:
                        command = text.replace(phrase, " ", 1).strip("，。！？,.!? ")
                        self.log(f"听到唤醒词：{phrase}（剩余：{command or '无'}）")
                        if callable(self.on_wake):
                            try:
                                self.on_wake(command)
                            except Exception as exc:
                                self.log(f"唤醒回调失败：{exc}")
                        break
            except sr.WaitTimeoutError:
                continue
            except Exception as exc:
                if self._stop.is_set():
                    return
                self.log(f"唤醒监听异常：{exc}")
                time.sleep(2.0)
