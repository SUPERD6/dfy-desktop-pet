# -*- coding: utf-8 -*-
"""语音输入：按键说话 -> speech_recognition（Google 在线识别，中文）。

离线/没麦克风/没网时会返回原因，不会让桌宠崩掉。
"""
from __future__ import annotations

import threading


class ASR:
    def __init__(self, language: str = "zh-CN", device_index: int | None = None, log=print):
        self.language = language
        self.device_index = device_index
        self.log = log
        self.ok = False
        self.reason = ""
        try:
            import speech_recognition as sr  # type: ignore
            self.sr = sr
            self.ok = True
        except Exception as exc:
            self.sr = None
            self.reason = f"缺少 SpeechRecognition：{exc}"

    def listen(self, on_done, timeout: float = 8.0, phrase_limit: float = 8.0) -> None:
        """后台录音并识别；on_done(text_or_None, error_or_None) 会在工作线程里被调用。"""
        if not self.ok:
            on_done(None, self.reason)
            return
        threading.Thread(
            target=self._run, args=(on_done, timeout, phrase_limit), name="asr", daemon=True
        ).start()

    def _run(self, on_done, timeout: float, phrase_limit: float) -> None:
        sr = self.sr
        try:
            recognizer = sr.Recognizer()
            recognizer.dynamic_energy_threshold = True
            kwargs = {}
            if self.device_index is not None:
                kwargs["device_index"] = self.device_index
            with sr.Microphone(**kwargs) as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.35)
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            try:
                text = recognizer.recognize_google(audio, language=self.language)
            except sr.UnknownValueError:
                on_done(None, "没听清，再说一遍嘛。")
                return
            except sr.RequestError as exc:
                on_done(None, f"在线识别服务不可用：{exc}")
                return
            on_done((text or "").strip(), None)
        except sr.WaitTimeoutError:
            on_done(None, "等太久啦，没听到声音。")
        except Exception as exc:  # 麦克风被占用等
            self.log(f"asr error: {exc}")
            on_done(None, f"录音失败：{exc}")
