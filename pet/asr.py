# -*- coding: utf-8 -*-
"""语音输入：麦克风录音 -> 本地 faster-whisper（失败回退 Google 在线识别）。

本地模型默认 .tools/faster-whisper-small；没有麦克风/没网时返回原因，不会让桌宠崩掉。
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path


class ASR:
    def __init__(self, language: str = "zh-CN", device_index: int | None = None, log=print,
                 backend: str = "whisper", model_dir: str | Path | None = None):
        self.language = language
        self.device_index = device_index
        self.log = log
        self.backend = str(backend or "whisper").lower()
        self.model_dir = Path(model_dir).expanduser() if model_dir else None
        self.ok = False
        self.reason = ""
        self._whisper = None
        self._whisper_lock = threading.Lock()
        try:
            import speech_recognition as sr  # type: ignore
            self.sr = sr
            self.ok = True
        except Exception as exc:
            self.sr = None
            self.reason = f"缺少 SpeechRecognition：{exc}"

    def _get_whisper(self):
        if self._whisper is not None:
            return self._whisper
        if not self.model_dir or not self.model_dir.is_dir():
            return None
        with self._whisper_lock:
            if self._whisper is not None:
                return self._whisper
            try:
                from faster_whisper import WhisperModel

                self.log(f"asr: 加载本地 Whisper（{self.model_dir.name}）")
                self._whisper = WhisperModel(str(self.model_dir), device="cpu", compute_type="int8")
            except Exception as exc:
                self.log(f"asr: 本地 Whisper 不可用：{exc}")
                self._whisper = None
        return self._whisper

    def _transcribe_whisper(self, audio) -> str | None:
        model = self._get_whisper()
        if model is None:
            return None
        try:
            raw = audio.get_wav_data(convert_rate=16000, convert_width=2)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                path = handle.name
                handle.write(raw)
            try:
                segments, _info = model.transcribe(path, language="zh", vad_filter=True,
                                                    beam_size=3, condition_on_previous_text=False)
                text = "".join(segment.text for segment in segments).strip()
                return text or None
            finally:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception as exc:
            self.log(f"asr: Whisper 识别失败：{exc}")
            return None

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
                recognizer.adjust_for_ambient_noise(source, duration=0.25)
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            if self.backend in ("whisper", "local", "faster-whisper"):
                text = self._transcribe_whisper(audio)
                if text:
                    on_done(text, None)
                    return
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
