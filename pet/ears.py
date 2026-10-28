# -*- coding: utf-8 -*-
"""本机的耳朵：唤醒词 + 说话检测 + 听写，全部在本机跑，不上传录音（nn-1 / nn-2）。

- 唤醒词：sherpa-onnx 关键词检测（kws-zipformer-zh-en-3M），唤醒词直接写汉字，自动转拼音，不用训练；
- 说话检测：Silero VAD，把唤醒后的那句话切出来；
- 听写：SenseVoice-Small（int8），顺带给出说话人的情绪（开心/难过/生气…）和声音事件（笑声/咳嗽…）。

模型由 tools/fetch_local_models.py 下载到 pet/cache/models/；缺模型或缺 sherpa-onnx 时 available=False，
调用方退回旧做法。这里不碰 Tk：回调在工作线程里触发，界面更新由调用方 root.after 转回主线程。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000
# 两个字的唤醒词（如「肥鱼」）和「飞鱼」这类近音词分不开：2026-10-28 用她的合成语音实测，
# 默认门槛下「飞鱼」误唤醒 1/3，门槛提到 0.4 后误唤醒没少、「肥鱼」漏听反而多了（9/12 → 3/8）。
# 所以默认只用三个字以上的词（「大肥鱼」「小肥鱼」实测 16/16 唤醒、0 误触发），两字词要 wake.allow_short 才用。
MIN_PHRASE_CHARS = 3
MODELS = Path(__file__).resolve().parent / "cache" / "models"

# SenseVoice 情绪标签 → 给她看的中文（EMO_UNKNOWN / NEUTRAL 不算有情绪）
EMOTIONS = {"HAPPY": "开心", "SAD": "难过", "ANGRY": "生气", "FEARFUL": "害怕",
            "DISGUSTED": "嫌弃", "SURPRISED": "惊讶"}
# 声音事件 → 中文（Speech 是普通说话，不算事件）
EVENTS = {"Laughter": "笑", "Cough": "咳嗽", "Sneeze": "打喷嚏", "Cry": "哭", "Applause": "鼓掌",
          "BGM": "在放音乐", "Breath": "叹气"}


@dataclass
class Heard:
    text: str = ""
    emotion: str = ""   # 中文情绪；平静/未知为空
    event: str = ""     # 中文事件；普通说话为空
    seconds: float = 0.0

    @property
    def tone(self) -> str:
        """给【此刻】用的一句话，比如「语气难过」「在笑」。"""
        parts = []
        if self.emotion:
            parts.append("语气" + self.emotion)
        if self.event:
            parts.append(self.event)
        return "，".join(parts)


def _tag(raw) -> str:
    return str(raw or "").strip().strip("<|>").strip()


def keyword_line(phrase: str) -> str:
    """「大肥鱼」→「d à f éi y ú @大肥鱼」（KWS 模型要的「声母 韵母带调」格式）。"""
    from pypinyin import Style, pinyin

    phrase = str(phrase).strip()
    initials = [x[0] for x in pinyin(phrase, style=Style.INITIALS, strict=False)]
    finals = [x[0] for x in pinyin(phrase, style=Style.FINALS_TONE, strict=False)]
    tokens = [t for a, b in zip(initials, finals) for t in (a, b) if t]
    return " ".join(tokens) + f" @{phrase}"


class Ears:
    """模型懒加载、线程安全的听写；KWS / VAD 按需创建。"""

    def __init__(self, models_dir: Path | None = None, log=print, threads: int = 2):
        self.dir = Path(models_dir or MODELS)
        self.log = log if callable(log) else print
        self.threads = threads
        self._asr = None
        self._lock = threading.Lock()
        self.reason = ""
        try:
            import sherpa_onnx  # noqa: F401
            import pypinyin  # noqa: F401
        except Exception as exc:
            self.reason = f"缺少 sherpa-onnx / pypinyin：{exc}"

    # ---------- 模型文件 ----------
    def _kws_files(self) -> dict:
        d = self.dir / "kws"
        return {"tokens": d / "tokens.txt",
                "encoder": d / "encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx",
                "decoder": d / "decoder-epoch-13-avg-2-chunk-16-left-64.onnx",
                "joiner": d / "joiner-epoch-13-avg-2-chunk-16-left-64.int8.onnx"}

    def _asr_files(self) -> dict:
        return {"model": self.dir / "sensevoice" / "model.int8.onnx", "tokens": self.dir / "sensevoice" / "tokens.txt"}

    @property
    def vad_file(self) -> Path:
        return self.dir / "vad" / "silero_vad.onnx"

    @property
    def available(self) -> bool:
        if self.reason:
            return False
        files = [*self._kws_files().values(), *self._asr_files().values(), self.vad_file]
        missing = [f.name for f in files if not f.is_file()]
        if missing:
            self.reason = "缺本机模型（先运行 python tools/fetch_local_models.py ears）：" + "、".join(missing[:3])
            return False
        return True

    @property
    def can_transcribe(self) -> bool:
        return not self.reason and all(f.is_file() for f in self._asr_files().values())

    # ---------- 听写 ----------
    def _recognizer(self):
        if self._asr is None:
            import sherpa_onnx as so

            files = self._asr_files()
            self._asr = so.OfflineRecognizer.from_sense_voice(
                model=str(files["model"]), tokens=str(files["tokens"]), use_itn=True,
                language="zh", num_threads=self.threads)
            self.log("耳朵：SenseVoice 已加载（本机听写）")
        return self._asr

    def transcribe(self, samples, sample_rate: int = SAMPLE_RATE) -> Heard:
        """samples：float32 单声道 [-1, 1]。返回文字 + 情绪 + 声音事件。"""
        import numpy as np

        samples = np.asarray(samples, dtype=np.float32)
        with self._lock:
            rec = self._recognizer()
            stream = rec.create_stream()
            stream.accept_waveform(sample_rate, samples)
            rec.decode_stream(stream)
            result = stream.result
        return Heard(text=str(result.text or "").strip(),
                     emotion=EMOTIONS.get(_tag(getattr(result, "emotion", "")), ""),
                     event=EVENTS.get(_tag(getattr(result, "event", "")), ""),
                     seconds=len(samples) / float(sample_rate))

    def transcribe_pcm16(self, raw: bytes, sample_rate: int = SAMPLE_RATE) -> Heard:
        import numpy as np

        return self.transcribe(np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, sample_rate)

    # ---------- 唤醒与切句 ----------
    def keyword_spotter(self, phrases, keywords_path: Path):
        import sherpa_onnx as so

        lines = []
        for phrase in phrases:
            try:
                lines.append(keyword_line(phrase))
            except Exception as exc:
                self.log(f"耳朵：唤醒词「{phrase}」转拼音失败：{exc}")
        keywords_path.parent.mkdir(parents=True, exist_ok=True)
        keywords_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        files = self._kws_files()
        return so.KeywordSpotter(
            tokens=str(files["tokens"]), encoder=str(files["encoder"]), decoder=str(files["decoder"]),
            joiner=str(files["joiner"]), keywords_file=str(keywords_path), num_threads=1,
            keywords_score=1.5, keywords_threshold=0.25, max_active_paths=4, provider="cpu")

    def vad(self, min_silence: float = 0.6):
        import sherpa_onnx as so

        config = so.VadModelConfig()
        config.silero_vad.model = str(self.vad_file)
        config.silero_vad.min_silence_duration = min_silence
        config.silero_vad.min_speech_duration = 0.25
        config.silero_vad.max_speech_duration = 12
        config.sample_rate = SAMPLE_RATE
        return so.VoiceActivityDetector(config, buffer_size_in_seconds=30)


class EarsWakeListener:
    """和 wake.WakeListener 同样的 start/stop/running 接口，但全程本机。

    on_wake(command, heard)：唤醒后那句话的文字（可能为空）和 Heard（含语气）。
    is_busy()：她在说话/合成时返回 True，这段时间不听（免得把自己的声音当成指令）。
    """

    COMMAND_WAIT_S = 4.0     # 唤醒后最多等多久开口
    COMMAND_MAX_S = 10.0     # 一句话最长

    def __init__(self, ears: Ears, phrases, on_wake=None, log=print, is_busy=None, device_index=None,
                 allow_short: bool = False):
        self.ears = ears
        phrases = [str(p).strip() for p in (phrases or ["大肥鱼"]) if str(p).strip()]
        if not allow_short:
            dropped = [p for p in phrases if len(p) < MIN_PHRASE_CHARS]
            phrases = [p for p in phrases if len(p) >= MIN_PHRASE_CHARS] or ["大肥鱼"]
            if dropped:
                (log if callable(log) else print)(f"耳朵：唤醒词「{'、'.join(dropped)}」太短，容易被近音词误触发，先不用")
        self.phrases = phrases
        self.on_wake = on_wake
        self.log = log if callable(log) else print
        self.is_busy = is_busy if callable(is_busy) else (lambda: False)
        self.device_index = device_index
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def start(self) -> tuple[bool, str]:
        if not self.ears.available:
            return (False, self.ears.reason)
        if self.running:
            return (True, "已经在听了")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ears-wake", daemon=True)
        self._thread.start()
        return (True, "开始听名字了（本机识别，不上传录音）")

    def stop(self) -> None:
        self._stop.set()

    # ---------- 内部 ----------
    def _loop(self) -> None:
        import numpy as np

        try:
            import pyaudio
        except Exception as exc:
            self.log(f"耳朵：缺少 pyaudio，没法开麦：{exc}")
            return
        try:
            kws = self.ears.keyword_spotter(self.phrases, self.ears.dir / "kws" / "dafeiyu_keywords.txt")
        except Exception as exc:
            self.log(f"耳朵：唤醒词模型加载失败：{exc}")
            return
        pa = pyaudio.PyAudio()
        chunk = SAMPLE_RATE // 10  # 0.1 秒
        try:
            mic = pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE, input=True,
                          frames_per_buffer=chunk, input_device_index=self.device_index)
        except Exception as exc:
            self.log(f"耳朵：麦克风打不开：{exc}")
            pa.terminate()
            return
        self.log(f"耳朵：本机唤醒已开（{'、'.join(self.phrases)}）")
        stream = kws.create_stream()
        try:
            while not self._stop.is_set():
                raw = mic.read(chunk, exception_on_overflow=False)
                if self.is_busy():  # 她自己在说话：不听，也清掉半截的唤醒状态
                    stream = kws.create_stream()
                    continue
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                stream.accept_waveform(SAMPLE_RATE, samples)
                hit = ""
                while kws.is_ready(stream):
                    kws.decode_stream(stream)
                    result = kws.get_result(stream)
                    if result:
                        hit = result
                        kws.reset_stream(stream)
                if hit:
                    self.log(f"耳朵：听到唤醒词「{hit}」")
                    heard = self._capture_command(mic, chunk)
                    stream = kws.create_stream()
                    if callable(self.on_wake):
                        try:
                            self.on_wake(heard.text if heard else "", heard)
                        except Exception as exc:
                            self.log(f"耳朵：唤醒回调失败：{exc}")
        except Exception as exc:
            if not self._stop.is_set():
                self.log(f"耳朵：监听异常，已停下：{exc}")
        finally:
            try:
                mic.stop_stream()
                mic.close()
            except Exception:
                pass
            pa.terminate()

    def _capture_command(self, mic, chunk: int):
        """唤醒后用 VAD 切出接下来那句话，交给 SenseVoice。没开口就返回 None。"""
        import numpy as np

        vad = self.ears.vad()
        window = 512
        buffer = np.zeros(0, dtype=np.float32)
        started = time.time()
        speech_seen = False
        while not self._stop.is_set():
            elapsed = time.time() - started
            if (not speech_seen and elapsed > self.COMMAND_WAIT_S) or elapsed > self.COMMAND_MAX_S:
                break
            raw = mic.read(chunk, exception_on_overflow=False)
            buffer = np.concatenate([buffer, np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0])
            while len(buffer) >= window:
                vad.accept_waveform(buffer[:window])
                buffer = buffer[window:]
            if vad.is_speech_detected():
                speech_seen = True
            if not vad.empty():  # 一句话说完（后面跟了足够的静音）
                segment = np.array(vad.front.samples, dtype=np.float32)
                vad.pop()
                return self.ears.transcribe(segment)
        vad.flush()
        if not vad.empty():
            segment = np.array(vad.front.samples, dtype=np.float32)
            return self.ears.transcribe(segment)
        return None
