# -*- coding: utf-8 -*-
"""大肥鱼的语音输出。

- 预合成的 mp3（来自 dsh-whale-pet 的 49 条晓伊语音）命中时直接播放，离线可用；
- 其它文本用 edge-tts 现场合成（需要联网，默认音色 zh-CN-XiaoyiNeural）；
- 播放走 Windows 自带的 MCI（winmm），不依赖额外的播放器。
"""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import queue
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:  # edge-tts 是可选依赖：没装也能播放预合成语音
    import edge_tts  # type: ignore
    HAS_EDGE_TTS = True
except Exception:  # pragma: no cover
    edge_tts = None  # type: ignore
    HAS_EDGE_TTS = False

PUNCT_RE = re.compile(r"[\s，。！？、；：…—~～,.!?;:\"'“”‘’（）()\[\]【】《》<>]+")


def normalize(text: str) -> str:
    return PUNCT_RE.sub("", text or "")


# ---------- 念之前的清洗：动作描写、颜文字、符号都只给眼睛看，不给耳朵听 ----------
_CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MD_LINK_RE = re.compile(r"\[([^\]\n]*)\]\([^)\n]*\)")
# 括号/星号里的动作：（尾巴摇摇）(探头) *抱紧饭碗* 【小声】 [歪头]
_ACTION_RE = re.compile(r"（[^（）\n]{0,40}）|\([^()\n]{0,40}\)|\*{1,2}[^*\n]{1,40}\*{1,2}|【[^【】\n]{0,20}】|\[[^\[\]\n]{0,20}\]")
# 常见颜文字：(｡･ω･｡) ╮(╯▽╰)╭ owo QAQ T_T ^_^ >_<
_KAOMOJI_RE = re.compile(
    r"[╮╭╯╰ヽﾉノ┐┌┘└ʕʔ][^\s，。！？]{0,12}|"
    r"(?<![A-Za-z])(?:QAQ|QwQ|OwO|owo|TAT|T_T|T\.T|orz|Orz|xD|XD)(?![A-Za-z])|\^_\^|>_<|>w<|=w=|-_-|o\.o|\^\^"
)
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF⬀-⯿️‍⌀-⏿←-⇿■-◿]"
)
_MD_MARK_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|>\s*|\d+[.)]\s+)", re.M)
_SYMBOL_RE = re.compile(r"[*_#`|<>{}\\/=+^$@&♪♡♥★☆❤✨～~]+")


def clean_for_speech(text: str) -> str:
    """把聊天文本变成适合念出来的句子。

    动作描写（括号、星号）、颜文字、emoji、Markdown 符号、链接和代码都不念；
    「……」「~」这类拖音变成自然停顿，重复标点合并。气泡里仍显示原文。
    """
    s = text or ""
    s = _CODE_RE.sub("，", s)
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _URL_RE.sub("", s)
    for _ in range(2):  # 嵌套括号剥两层就够了
        s = _ACTION_RE.sub("，", s)
    s = _KAOMOJI_RE.sub("", s)
    s = _EMOJI_RE.sub("", s)
    s = _MD_MARK_RE.sub("", s)
    s = re.sub(r"[…]+|\.{3,}|。{2,}|—{2,}|-{2,}", "，", s)
    s = _SYMBOL_RE.sub("，", s)
    s = re.sub(r"[\r\n\t]+", "，", s)
    s = re.sub(r"[ 　]{2,}", " ", s)
    # 合并相邻标点：保留最「重」的那个（！？ > 。 > ，）
    def _merge(match: re.Match) -> str:
        chunk = match.group(0)
        for mark in ("？", "?", "！", "!", "。", "，", ",", "、", "；", "：", " "):
            if mark in chunk:
                return {"?": "？", "!": "！", ",": "，"}.get(mark, mark)
        return chunk[0]
    s = re.sub(r"[，,。！!？?、；：\s]{2,}", _merge, s)
    s = s.strip(" ，,、；：")
    return s if re.search(r"[\w一-鿿]", s) else ""


def trim_for_speech(text: str, limit: int = 90) -> str:
    """太长的回复只念前几句（按句号/问号/感叹号截断），全文在气泡里看。"""
    if len(text) <= limit:
        return text
    cut = max(text.rfind(m, 0, limit) for m in "。！？!?")
    if cut >= limit // 3:
        return text[: cut + 1]
    cut = text.rfind("，", 0, limit)
    return (text[:cut] if cut > 0 else text[:limit]) + "。"


class VoiceEngine:
    """单线程播放队列：一次说一句，不会互相打断。"""

    def __init__(self, root, assets_dir, cache_dir, config, log=print, on_speaking=None):
        self.root = root
        self.assets = Path(assets_dir)
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.log = log
        self.on_speaking = on_speaking

        self._queue: queue.Queue = queue.Queue()
        self._mci = ctypes.windll.winmm.mciSendStringW
        self._alias = 0
        self._busy = threading.Event()
        self._cut = threading.Event()
        self._preset_by_norm: dict[str, Path] = {}
        self._preset_by_text: dict[str, Path] = {}
        self._load_manifest()

        self._thread = threading.Thread(target=self._worker, name="voice", daemon=True)
        self._thread.start()

    # ---------- 预合成语音索引 ----------
    def _load_manifest(self) -> None:
        manifest_path = self.assets / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log(f"voice: 读不到 manifest.json（{exc}），只能现场合成")
            return
        voices = manifest.get("voices") or {}
        for _vid, info in voices.items():
            if not isinstance(info, dict):
                continue
            text = (info.get("text") or "").strip()
            file = info.get("file") or ""
            if not text or not file:
                continue
            path = (self.assets / file)
            if not path.exists():
                continue
            self._preset_by_text[text] = path
            self._preset_by_norm[normalize(text)] = path
        self.log(f"voice: 预合成语音 {len(self._preset_by_text)} 条")

    # ---------- 对外接口 ----------
    def speak(self, text: str = "", preset: str | Path | None = None, interrupt: bool = False) -> None:
        """把一句话加入播放队列。preset 指定时直接播该文件。

        interrupt=True：先清掉还没念的旧句子并掐断正在念的那句（新回复不用等旧回复念完）。
        """
        if self.config.get("mute"):
            return
        if interrupt:
            self.stop()
        if preset is not None:
            self._queue.put(("play", Path(preset)))
            return
        raw = (text or "").strip()
        if not raw:
            return
        # 原文先试一次预合成（台词本身可能带「~」），没命中再念清洗后的文本
        # GPT-SoVITS 模式下跳过旧晓伊预合成，否则常说的话仍是旧声音
        use_presets = str(self.config.get("voice_backend") or "edge").lower() != "gptsovits"
        if use_presets and (raw in self._preset_by_text or normalize(raw) in self._preset_by_norm):
            self._queue.put(("text", raw))
            return
        spoken = trim_for_speech(clean_for_speech(raw), int(self.config.get("voice_max_chars") or 90))
        if spoken:
            self._queue.put(("text", spoken))

    def busy(self) -> bool:
        """是否正在播放/合成一句话（给主动搭话、打断逻辑用）。"""
        return self._busy.is_set() or not self._queue.empty()

    def stop(self) -> None:
        """丢掉排队中的句子，并让正在播的那句尽快停下。"""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            self._queue.task_done()
        self._cut.set()

    def shutdown(self) -> None:
        self._queue.put(None)

    # ---------- 内部 ----------
    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            kind, payload = item
            self._cut.clear()
            try:
                if kind == "play":
                    self._play(Path(payload))
                elif kind == "text":
                    text = str(payload)
                    preset = None
                    if str(self.config.get("voice_backend") or "edge").lower() != "gptsovits":
                        preset = self._preset_by_text.get(text) or self._preset_by_norm.get(normalize(text))
                    if preset is not None:
                        self._play(preset)
                    else:
                        path = self._synthesize(text)
                        if path is not None:
                            self._play(path)
                        else:
                            self.log("voice: 这一句没有合成成功，只能显示文字")
                elif kind == "stop":
                    pass  # 播放是同步的，队列里的 stop 只影响后续
            except Exception as exc:  # 播放失败不能把桌宠弄崩
                self.log(f"voice error: {exc}")
            finally:
                self._queue.task_done()

    def _synthesize(self, text: str) -> Path | None:
        backend = str(self.config.get("voice_backend") or "edge").strip().lower()
        if backend in ("gptsovits", "gpt-sovits", "so-vits"):
            path = self._synthesize_gptsovits(text)
            if path is not None:
                return path
            self.log("voice: GPT-SoVITS 不可用，回退 edge-tts")
        return self._synthesize_edge(text)

    def _synthesize_gptsovits(self, text: str) -> Path | None:
        cfg = self.config.get("gptsovits") or {}
        if not isinstance(cfg, dict):
            return None
        base_url = str(cfg.get("base_url") or "http://127.0.0.1:9880").rstrip("/")
        ref = str(cfg.get("ref_audio_path") or "").strip()
        if not ref:
            return None
        ref_path = Path(ref)
        if not ref_path.is_absolute():
            ref_path = (self.assets.parent / ref_path).resolve()
        if not ref_path.is_file():
            self.log(f"voice: GPT-SoVITS 参考音频不存在：{ref_path}")
            return None
        prompt = str(cfg.get("prompt_text") or "")
        if not prompt:
            self.log("voice: GPT-SoVITS 缺少参考文字 prompt_text")
            return None
        ref_key = hashlib.sha1(ref_path.read_bytes()[:4096]).hexdigest()[:10]
        key = hashlib.sha1(f"gs|{ref_key}|{prompt}|{text}".encode("utf-8")).hexdigest()
        out = self.cache / f"tts_gs_{key}.wav"
        if out.exists() and out.stat().st_size > 1024:
            return out
        params = {
            "text": text,
            "text_lang": str(cfg.get("text_lang") or "zh"),
            "ref_audio_path": str(ref_path),
            "prompt_text": prompt,
            "prompt_lang": str(cfg.get("prompt_lang") or "zh"),
            "text_split_method": str(cfg.get("text_split_method") or "cut5"),
            "batch_size": "1",
            "media_type": "wav",
            "streaming_mode": "false",
        }
        url = base_url + "/tts?" + urllib.parse.urlencode(params)
        tmp = out.with_suffix(".part")
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                body = response.read()
            if len(body) > 1024:
                tmp.write_bytes(body)
                tmp.replace(out)
                return out
        except Exception as exc:
            self.log(f"voice: GPT-SoVITS 合成失败：{exc}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    def _synthesize_edge(self, text: str) -> Path | None:
        if not HAS_EDGE_TTS:
            return None
        voice = self.config.get("voice") or "zh-CN-XiaoyiNeural"
        rate = str(self.config.get("voice_rate") or "-4%")
        pitch = str(self.config.get("voice_pitch") or "+6Hz")
        key = hashlib.sha1(f"{voice}|{rate}|{pitch}|{text}".encode("utf-8")).hexdigest()
        out = self.cache / f"tts_{key}.mp3"
        if out.exists() and out.stat().st_size > 1024:
            return out
        tmp = out.with_suffix(".part")

        async def run() -> None:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            await communicate.save(str(tmp))

        try:
            asyncio.run(run())
        except Exception as exc:
            self.log(f"edge-tts 合成失败：{exc}")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        if tmp.exists() and tmp.stat().st_size > 512:
            tmp.replace(out)
            return out
        return None

    def _send_mci(self, command: str) -> str:
        buf = ctypes.create_unicode_buffer(512)
        err = self._mci(command, buf, 512, None)
        if err != 0:
            raise RuntimeError(f"MCI({err}): {buf.value} / {command}")
        return buf.value

    def _play(self, path: Path) -> None:
        if not path.exists():
            return
        self._busy.set()
        try:
            self._play_unlocked(path)
        finally:
            self._busy.clear()

    def _play_unlocked(self, path: Path) -> None:
        self._alias += 1
        alias = f"dfyvoice{self._alias}"
        play_path = path
        try:
            self._open_and_play(alias, play_path)
        except Exception as exc:
            # 中文路径有时会让 MCI 犯脾气：复制到纯 ASCII 临时路径再试一次
            self.log(f"voice: 直接播放失败（{exc}），改用临时副本")
            tmp = Path(tempfile.gettempdir()) / f"dfy_voice_{self._alias}.mp3"
            try:
                shutil.copyfile(path, tmp)
                self._open_and_play(alias, tmp)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

    def _open_and_play(self, alias: str, path: Path) -> None:
        mci_type = "waveaudio" if path.suffix.lower() == ".wav" else "mpegvideo"
        self._send_mci(f'open "{path}" type {mci_type} alias {alias}')
        try:
            try:
                vol = int(self.config.get("volume", 800))
                self._send_mci(f"setaudio {alias} volume to {max(0, min(1000, vol))}")
            except Exception:
                pass
            self._speaking(True)
            self._send_mci(f"play {alias}")
            deadline = time.time() + 120
            while time.time() < deadline:
                try:
                    mode = self._send_mci(f"status {alias} mode")
                except Exception:
                    break
                if mode != "playing" or self._cut.is_set():
                    break
                time.sleep(0.08)
        finally:
            try:
                self._send_mci(f"close {alias}")
            except Exception:
                pass
            self._speaking(False)

    def _speaking(self, speaking: bool) -> None:
        callback = self.on_speaking
        if callback is None:
            return
        try:
            self.root.after(0, callback, speaking)
        except Exception:
            try:
                callback(speaking)
            except Exception:
                pass
