# -*- coding: utf-8 -*-
"""大肥鱼 · 桌面 AI 伙伴（本机原型）

- 透明、无边框、置顶的小窗口，住在桌面右下角；
- 桌面窗口/立绘：优先使用 dsh-dfy 的 1280² 分层立绘预渲染的高清动画（dsh-dfy 角色素材 CC BY-NC-SA 4.0，
  仅个人非商业体验），没有该素材时回退到 dsh-whale-pet 的小尺寸帧动画；
- 点她：摸摸头；拖动：搬家；右键：菜单；双击：打开聊天窗；
- 聊天窗支持打字、语音输入（麦克风）、语音输出（晓伊音色，离线预合成 / edge-tts 现场合成）；
- 大脑默认离线台词模式；在右键 → 设置里填 OpenAI 兼容接口（如 DeepSeek API Key）即可变成真聊天。

只依赖 Python 3 + Pillow；语音输入需要 SpeechRecognition + PyAudio，语音输出需要 edge-tts（都已随本机安装）。
"""
from __future__ import annotations

import json
import math
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk
from interactions import (DRAG_LINES, FEED_LINES, INNER_VOICE_LINES, pat_event,
                          pat_fallbacks, render_food, state_for_actions, step_flight)
from touch_zones import (BODY_MAP_HINT, touch_emotion, touch_event, touch_fallbacks,
                         touch_point_event, zone_for)
from desktop_integration import (TrayService, autostart_enabled, clamp_opacity,
                                 click_through, set_autostart, tray_available)
from hotkeys import HotkeyService, hotkey_labels
from screen_awareness import (capture_screen, cleanup_screens, context_line,
                              screen_context, vision_copy)
from self_space import home_root, save_note, write_home_text
from news import fetch_headlines, headlines_text
from vision_model import describe_image as describe_screen_image, resolve_vision
from claude_vision import claude_see
from video_gen import create_task as create_video_task, download as download_video
from video_gen import resolve_key as resolve_ark_key, wait_task as wait_video_task
from video_understanding import describe_video
try:
    from netease_music import download as netease_download, search as netease_search
except Exception:
    netease_download = netease_search = None
try:
    from itunes_control import now_playing as itunes_now_playing
    from itunes_control import next_track as itunes_next, play_pause as itunes_play_pause
    from itunes_control import play_first as itunes_play_first, previous as itunes_previous
    from itunes_control import search_play as itunes_search_play, stop as itunes_stop
except Exception:  # iTunes 不是必需依赖
    itunes_now_playing = itunes_next = itunes_play_pause = None
    itunes_play_first = itunes_previous = itunes_search_play = itunes_stop = None
try:
    from seed_vc_sing import available as seedvc_available, convert as seedvc_convert
except Exception:
    seedvc_available = lambda: False
    seedvc_convert = None
try:
    from music_source import pick_source as pick_music_source
except Exception:
    pick_music_source = None
from memory import format_time
from sfx import Sfx
from stickers import StickerBook
from singing import Songbook
try:
    from douyin_bridge import DouyinBridge
except Exception:  # 桥可选：缺文件时不影响桌宠
    DouyinBridge = None

CHOMP_SOUNDS = ("啊呜～咔嚓咔嚓。", "嗷呜，吧唧吧唧……", "咔嚓咔嚓，好吃！")
# 心情标记里的情绪 → 动画状态
MOOD_STATES = {"开心": "happy", "害羞": "shy", "得意": "proud", "想念": "shy",
               "委屈": "sad", "难过": "sad", "担心": "confused"}

BASE = Path(__file__).resolve().parent
ASSETS = BASE / "assets"
CACHE = BASE / "cache"
LOG_DIR = BASE / "logs"
CONFIG_PATH = BASE / "config.json"
# 她的个人生活数据（饭量账本、生活状态、小窝）放哪；冒烟测试指到临时目录，别写坏真数据
DATA_DIR = Path(os.environ.get("DFY_DATA_DIR") or BASE)
KEY_COLOR = "#0b0e14"          # 透明色键
DFY_TARGET_WIDTH = 430          # dsh-dfy 高清立绘（640² 预渲染）的显示宽度
LEGACY_TARGET_WIDTH = 200       # 旧版 192×208 帧动画的显示宽度
BUBBLE_ZONE = 120               # 旧素材头顶气泡预留高度
DFY_BUBBLE_ZONE = 112           # 高清立绘头顶气泡预留高度
GREETING_FALLBACK = "咕噜…主人回来啦！今天也要一起加油呀～"
NO_KEY_HINT = "（我现在还只是离线台词版；右键 → 设置，填上 DeepSeek API Key 就能真的聊天啦。）"

# 高清立绘的情绪状态（与 dsh-dfy 对齐，由预渲染素材提供）
DFY_EMOTIONS = ("happy", "shy", "angry", "sad", "surprised", "confused", "sleepy", "proud", "hungry")
EMOTION_LABELS = {
    "happy": "开心", "shy": "害羞", "angry": "生气", "sad": "难过",
    "surprised": "惊讶", "confused": "困惑", "sleepy": "困倦", "proud": "得意",
    "hungry": "馋嘴", "idle": "待机", "thinking": "思考", "wave": "打招呼", "jump": "蹦一下",
}
# 旧素材没有情绪帧，用现有 7 组动画近似映射
LEGACY_EMOTION_STATE = {
    "idle": "idle", "thinking": "running", "talking": "waving", "wave": "waving", "jump": "jumping",
    "happy": "waving", "shy": "waving", "angry": "failed", "sad": "failed", "surprised": "jumping",
    "confused": "waiting", "sleepy": "idle", "proud": "jumping", "hungry": "jumping",
}
# 情绪 → 头顶冒的小符号（只用 Windows 字体常见的 BMP 字符，避免 emoji 花屏）
PARTICLE_SPECS = {
    "happy": (("✦", "♪"), "#e8b447"),
    "shy": (("♥",), "#ff8fa3"),
    "angry": (("✖",), "#e05a5a"),
    "sad": (("•",), "#7fb4ea"),
    "surprised": (("!",), "#e8933a"),
    "confused": (("?",), "#7a8fd8"),
    "sleepy": (("Z",), "#8aa8c9"),
    "proud": (("✦",), "#e8b447"),
    "hungry": (("○",), "#d79a5b"),
    "jump": (("✦", "♥"), "#e8b447"),
    "wave": (("♪",), "#8fb4ea"),
    "bubbles": (("○", "◦", "°"), "#8fc3f0"),  # 入场时冒的泡泡
}
# 让她表演时的台词
EMOTION_LINES = {
    "happy": ("今天心情超好！", "嘿嘿，快夸我～", "感觉什么都能做到！"),
    "shy": ("别、别一直看人家啦…", "脸有点热…", "再这样我要躲起来了。"),
    "angry": ("哼！不理你了。", "我生气了，真的。", "白饭才能哄好我。"),
    "sad": ("呜…有点难过。", "让我缓一会儿…", "抱抱我嘛。"),
    "surprised": ("欸？！真的吗？", "吓我一跳！", "你刚才说什么？"),
    "confused": ("唔…我有点糊涂了。", "这个和那个，到底哪个？", "让我想想…"),
    "sleepy": ("哈啊…好困。", "就眯一小会儿…", "Zzz…"),
    "proud": ("看，我超厉害的！", "这题我会！", "快记我一功。"),
    "hungry": ("白饭…白饭…", "闻到香味了！", "肚子在叫，你听见了吗？"),
}

# 没人和她说话时，她自己冒出来的小动作台词
AUTONOMY_LINES = (
    "嗯……先伸个懒腰。",
    "（偷偷看了一眼你的屏幕）",
    "这题有点费脑，让我再嚼一嚼。",
    "歇一会儿？我想喝口水。",
    "我不是大肥鱼……就一口。",
    "你先忙，有事再叫我。",
    "嘿嘿，这次还不错吧。",
    "尾巴自己摇起来了，不怪我。",
    "我醒着呢……刚才只是闭了一下眼睛。",
)

HOVER_LINES = (
    "唔？主人路过啦……本鲸没在偷看，真的。",
    "尾巴自己摇的，不关本鲸的事。",
    "主人要不要摸一下头？就一下，本鲸不贪心。",
    "（假装看窗外，尾巴尖偷偷晃）",
    "本鲸在呢，有事就叫……没事也可以叫。",
)

# Coopanion 风格的「她对你的了解」档案（用户可编辑，建议用 git 留版本）
PARTNER_TEMPLATE = """# PARTNER.md · 她对主人的了解

> 这个文件是「她怎么理解你」的档案（参考 Coopanion 的做法）。你可以直接改；每次改动建议 `git commit` 留一个版本。
> 她会在每次聊天时参考这里（PARTNER.md > 相处笔记 > 个人库摘要）。

## 基本信息
- 称呼：
- 现在的目标：

## 你希望她怎么陪你
- 

## 雷区 / 不要做的事
- 

## 最近在忙的事（可随手记）
- 
"""


# --------------------------------------------------------------------------- 基础工具
def log(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "app.log", "a", encoding="utf-8") as handle:
            handle.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(message) + "\n")
    except Exception:
        pass


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def config_token(cfg: dict) -> str:
    return str((cfg or {}).get("token") or "").strip()


def save_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log(f"保存 {path.name} 失败：{exc}")


def harden_alpha(image: Image.Image, threshold: int = 40) -> Image.Image:
    """把半透明像素变成全透明/不透明，避免透明色键在边缘产生深色描边。"""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A").point(lambda value: 255 if value >= threshold else 0)
    rgba.putalpha(alpha)
    return rgba


# --------------------------------------------------------------------------- 动画素材
def placeholder_frames() -> list[tuple[Image.Image, int]]:
    """没有素材时的兜底形象：一个会上下浮动的小鲸鱼娘（纯代码画的）。"""
    frames = []
    for index in range(16):
        width, height = 240, 260
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        bob = int(3 * math.sin(index / 16 * 2 * math.pi))
        cx, top = width / 2, 26 + bob
        # 尾巴
        draw.ellipse([cx + 52, top + 150, cx + 100, top + 188], fill=(30, 70, 150, 255))
        draw.ellipse([cx + 78, top + 168, cx + 104, top + 190], fill=(46, 96, 186, 255))
        # 后发
        draw.ellipse([cx - 72, top + 36, cx + 72, top + 186], fill=(26, 60, 132, 255))
        # 脸
        draw.ellipse([cx - 52, top + 60, cx + 52, top + 160], fill=(255, 229, 214, 255))
        # 前发
        draw.ellipse([cx - 62, top + 26, cx + 62, top + 92], fill=(36, 84, 172, 255))
        draw.rectangle([cx - 62, top + 56, cx - 40, top + 122], fill=(36, 84, 172, 255))
        draw.rectangle([cx + 40, top + 56, cx + 62, top + 122], fill=(36, 84, 172, 255))
        # 鲸鱼鳍耳朵
        draw.polygon([(cx - 58, top + 96), (cx - 96, top + 80), (cx - 68, top + 126)], fill=(28, 66, 144, 255))
        draw.polygon([(cx + 58, top + 96), (cx + 96, top + 80), (cx + 68, top + 126)], fill=(28, 66, 144, 255))
        # 女仆发箍
        draw.rectangle([cx - 46, top + 24, cx + 46, top + 35], fill=(250, 250, 250, 255))
        # 眼睛
        for dx in (-21, 21):
            draw.ellipse([cx + dx - 8, top + 92, cx + dx + 8, top + 116], fill=(22, 44, 96, 255))
            draw.ellipse([cx + dx - 3, top + 96, cx + dx + 2, top + 102], fill=(255, 255, 255, 255))
        # 腮红 + 嘴
        draw.ellipse([cx - 42, top + 118, cx - 22, top + 131], fill=(255, 178, 190, 255))
        draw.ellipse([cx + 22, top + 118, cx + 42, top + 131], fill=(255, 178, 190, 255))
        draw.arc([cx - 9, top + 120, cx + 9, top + 136], 0, 180, fill=(178, 78, 92, 255), width=2)
        # 裙子
        draw.polygon([(cx - 56, top + 158), (cx + 56, top + 158), (cx + 84, top + 236), (cx - 84, top + 236)],
                     fill=(30, 68, 148, 255))
        draw.rectangle([cx - 34, top + 158, cx + 34, top + 174], fill=(255, 255, 255, 255))
        frames.append((image, 90))
    return frames


class AnimationLibrary:
    """角色包加载器：dsh-dfy 立绘包 / 外部 characters/<id> 包 / 旧版帧动画回退。"""

    def __init__(self, assets_dir: Path, log_func=log, character: str = "dfy"):
        self.assets = Path(assets_dir)
        self.log = log_func
        self.character = str(character or "dfy")
        self.display_name = "大肥鱼"
        self.manifest = load_json(self.assets / "manifest.json", {})
        self.dfy: dict = {}
        self.is_dfy = False
        self.emotions: list[str] = []
        self.states: dict[str, list[tuple[Image.Image, int]]] = {}
        if self.character == "dfy":
            self._load_dfy()
        else:
            self._load_pack(self.assets / "characters" / self.character)
        if not self.states:
            self.log(f"角色包 {self.character} 没加载出来，回退旧版帧动画")
            self._load_legacy()

    def _load_pack(self, pack_dir: Path) -> None:
        manifest = load_json(pack_dir / "manifest.json", {})
        states = manifest.get("states") if isinstance(manifest, dict) else None
        if not isinstance(states, dict) or not states:
            return
        loaded: dict[str, list[tuple[Image.Image, int]]] = {}
        for name, meta in states.items():
            if not isinstance(meta, dict):
                continue
            file = meta.get("file")
            if not file:
                continue
            frames = self._read_frames(pack_dir / str(file), harden=False)
            if not frames:
                continue
            fallback_ms = int(meta.get("duration_ms") or 90)
            loaded[str(name)] = [(img, dur if dur >= 40 else fallback_ms) for img, dur in frames]
        if not loaded:
            return
        self.states = loaded
        self.dfy = manifest
        self.is_dfy = True
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        self.display_name = str(manifest.get("display_name") or source.get("name")
                                or manifest.get("name") or self.character)
        self.emotions = [str(item) for item in (manifest.get("emotions") or []) if str(item)]
        self.log(f"角色包 {self.character}（{self.display_name}）：{len(self.states)} 个状态")

    def _load_dfy(self) -> None:
        manifest = load_json(self.assets / "dfy" / "manifest.json", {})
        states = manifest.get("states") if isinstance(manifest, dict) else None
        if not isinstance(states, dict) or not states:
            return
        loaded: dict[str, list[tuple[Image.Image, int]]] = {}
        for name, meta in states.items():
            if not isinstance(meta, dict):
                continue
            file = meta.get("file")
            if not file:
                continue
            frames = self._read_frames(self.assets / "dfy" / str(file), harden=False)
            if not frames:
                continue
            fallback_ms = int(meta.get("duration_ms") or 90)
            loaded[str(name)] = [(img, dur if dur >= 40 else fallback_ms) for img, dur in frames]
        if not loaded:
            return
        self.states = loaded
        self.dfy = manifest
        self.is_dfy = True
        self.display_name = "鲸鱼娘 · dsh-fy 立绘"
        emotions = manifest.get("emotions") or []
        self.emotions = [str(item) for item in emotions if str(item)]
        self.log(f"dsh-dfy 立绘：{len(self.states)} 个状态（{', '.join(sorted(self.states))}）")

    def _load_legacy(self) -> None:
        sources: dict[str, Path] = {}
        animations = self.manifest.get("animations") or {}
        if isinstance(animations, dict):
            for name, meta in animations.items():
                if not isinstance(meta, dict):
                    continue
                file = meta.get("file") or meta.get("path")
                if file:
                    sources[str(name)] = self.assets / file
        if not sources:
            anim_dir = self.assets / "anim"
            if anim_dir.exists():
                for path in sorted(anim_dir.glob("*.webp")):
                    sources[path.stem] = path
                for path in sorted(anim_dir.glob("*.png")):
                    if not path.stem.endswith("_frame0"):
                        sources.setdefault(path.stem, path)
        for name, path in sources.items():
            frames = self._read_frames(path)
            if frames:
                self.states[name] = frames
        if not self.states:
            self.log("没有找到动画素材，使用代码画的兜底形象")
            self.states = {"idle": placeholder_frames()}
        self.log(f"动画状态：{list(self.states)}")

    def _read_frames(self, path: Path, harden: bool = True) -> list[tuple[Image.Image, int]]:
        frames: list[tuple[Image.Image, int]] = []
        try:
            image = Image.open(path)
            index = 0
            while True:
                try:
                    image.seek(index)
                except EOFError:
                    break
                duration = image.info.get("duration") or 90
                try:
                    duration = int(duration)
                except Exception:
                    duration = 90
                frame = harden_alpha(image) if harden else image.convert("RGBA").copy()
                frames.append((frame, max(50, min(400, duration))))
                index += 1
                if not getattr(image, "is_animated", False):
                    break
        except Exception as exc:
            self.log(f"读取动画 {path.name} 失败：{exc}")
        return frames

    def best(self, *names: str) -> str:
        for name in names:
            if name in self.states:
                return name
        for name in ("idle", "normal", "stand", "default"):
            if name in self.states:
                return name
        return next(iter(self.states))

    def first_size(self) -> tuple[int, int]:
        state = self.best("idle")
        try:
            return self.states[state][0][0].size
        except Exception:
            return (240, 260)


class Animator:
    """把帧序列画到 Canvas 上，支持切换状态/一次性动作。"""

    def __init__(self, root, canvas: tk.Canvas, library: AnimationLibrary, target_width: int,
                 canvas_width: int, canvas_height: int):
        self.root = root
        self.canvas = canvas
        self.library = library
        self.target_width = target_width
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self._cache: dict[str, list[tuple[ImageTk.PhotoImage, int]]] = {}
        self._pil: dict[str, list[Image.Image]] = {}  # 缩放好的原图，压扁/回弹时现做变形帧
        self._squash = (1.0, 1.0)
        self._squash_photo = None
        self._spring_after = None
        self._state: str | None = None
        self._index = 0
        self._after_id = None
        self._once_then: str | None = None
        self.item = canvas.create_image(canvas_width / 2, canvas_height - 10, anchor="s")

        # 说话动画：素材没有单独嘴型帧，用「嘴部小椭圆 + 轻微点头」模拟口型
        self._talking = False
        self._talk_after = None
        self._talk_phase = 0
        self._base_xy = (canvas_width / 2, canvas_height - 10)
        self.mouth_item = canvas.create_oval(0, 0, 0, 0, fill="#7a2f3a",
                                             outline="", state="hidden")

    def play(self, state: str, once: bool = False, then: str | None = None) -> None:
        if state not in self.library.states:
            state = self.library.best(state)
        self._cancel()
        self._state = state
        self._index = 0
        self._once_then = then if once else None
        self._tick()

    def stop(self) -> None:
        self._cancel()
        self._state = None

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    # 侧身的动作素材是脸朝右的，往左走时镜像；正面的动作不翻
    SIDE_STATES = ("walk", "sneak")

    def set_facing_left(self, left: bool) -> None:
        self.facing_left = bool(left)

    def _frames(self, state: str) -> list[tuple[ImageTk.PhotoImage, int]]:
        flip = bool(getattr(self, "facing_left", False)) and state in self.SIDE_STATES
        key = f"{state}|flip" if flip else state
        if key in self._cache:
            return self._cache[key]
        out: list[tuple[ImageTk.PhotoImage, int]] = []
        pils: list[Image.Image] = []
        for image, duration in self.library.states[state]:
            width, height = image.size
            scale = self.target_width / float(width) if width else 1.0
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            if getattr(self.library, "is_dfy", False):
                # 预乘 alpha 缩放，避免半透明边缘的 RGB 泛白，在色键透明窗上形成浅色毛边
                resized = image.convert("RGBa").resize(new_size, Image.LANCZOS).convert("RGBA")
            else:
                resized = image.resize(new_size, Image.LANCZOS)
            if flip:
                resized = resized.transpose(Image.FLIP_LEFT_RIGHT)
            out.append((ImageTk.PhotoImage(resized), duration))
            pils.append(resized)
        self._cache[key] = out
        self._pil[key] = pils
        return out

    def _tick(self) -> None:
        if not self._state:
            return
        frames = self._frames(self._state)
        if not frames:
            return
        image, duration = frames[self._index % len(frames)]
        self.canvas.itemconfigure(self.item, image=self._squashed(self._index) or image)
        self.canvas.tag_raise(self.item)
        self._index += 1
        finished = self._index >= len(frames)
        if finished and self._once_then:
            follow = self._once_then
            self._once_then = None
            self.play(follow)
            return
        self._after_id = self.root.after(duration, self._tick)

    # ---------- 按压 Q 弹（借鉴 DSH 小鲸鱼挂件：按下去压扁，松手弹回，脚底不动） ----------
    def _squashed(self, index: int):
        sx, sy = self._squash
        if abs(sx - 1) < 0.005 and abs(sy - 1) < 0.005 or not self._state:
            return None
        flip = bool(getattr(self, "facing_left", False)) and self._state in self.SIDE_STATES
        pils = self._pil.get(f"{self._state}|flip" if flip else self._state) or []
        if not pils:
            return None
        base = pils[index % len(pils)]
        size = (max(1, int(base.width * sx)), max(1, int(base.height * sy)))
        self._squash_photo = ImageTk.PhotoImage(base.resize(size, Image.BILINEAR))
        return self._squash_photo

    def _redraw(self) -> None:
        if not self._state:
            return
        frames = self._frames(self._state)
        if frames:
            index = max(0, self._index - 1)
            self.canvas.itemconfigure(self.item, image=self._squashed(index) or frames[index % len(frames)][0])

    def press(self) -> None:
        self._cancel_spring()
        self._squash = (1.1, 0.86)
        self._redraw()

    def release(self) -> None:
        """松手：沿 1.08 → 0.95 → 1.03 → 1 回弹两下。"""
        self._cancel_spring()
        steps = [(0.95, 1.08), (1.04, 0.95), (0.99, 1.03), (1.0, 1.0)]

        def step(i: int) -> None:
            self._squash = steps[i]
            self._redraw()
            self._spring_after = self.root.after(60, step, i + 1) if i + 1 < len(steps) else None

        step(0)

    def _cancel_spring(self) -> None:
        if self._spring_after is not None:
            try:
                self.root.after_cancel(self._spring_after)
            except Exception:
                pass
            self._spring_after = None

    # ---------- 说话动画（模拟口型） ----------
    def set_talking(self, talking: bool) -> None:
        """TTS 播放期间张合小嘴 + 轻微点头；结束时恢复。"""
        if talking == self._talking:
            return
        self._talking = talking
        if not talking:
            self._cancel_talk()
            try:
                self.canvas.coords(self.item, *self._base_xy)
                self.canvas.itemconfigure(self.mouth_item, state="hidden")
            except Exception:
                pass
            return
        self._talk_phase = 0
        self._talk_tick()

    def _cancel_talk(self) -> None:
        if self._talk_after is not None:
            try:
                self.root.after_cancel(self._talk_after)
            except Exception:
                pass
            self._talk_after = None

    def _talk_tick(self) -> None:
        if not self._talking:
            return
        phase = self._talk_phase
        self._talk_phase += 1
        try:
            offset = 1 if phase % 2 else 0  # 轻微点头
            bottom_x, bottom_y = self._base_xy[0], self._base_xy[1] - offset
            self.canvas.coords(self.item, bottom_x, bottom_y)
            src_w, src_h = self.library.first_size()
            scale = float(self.target_width) / float(src_w) if src_w else 1.0
            disp_w, disp_h = self.target_width, float(src_h) * scale
            left, top = bottom_x - disp_w / 2.0, bottom_y - disp_h
            # 嘴部在源图 192×208 里的位置约 (88, 88)，按比例映射
            cx = left + disp_w * (88.0 / 192.0)
            cy = top + disp_h * (88.0 / 208.0)
            sizes = ((0.0, 0.0), (2.2, 1.7), (3.4, 2.7), (2.0, 1.5))
            half_w, half_h = sizes[phase % len(sizes)]
            if half_w <= 0:
                self.canvas.itemconfigure(self.mouth_item, state="hidden")
            else:
                self.canvas.coords(self.mouth_item, cx - half_w, cy - half_h, cx + half_w, cy + half_h)
                self.canvas.itemconfigure(self.mouth_item, state="normal")
            self.canvas.tag_raise(self.item)
            self.canvas.tag_raise(self.mouth_item)
        except Exception:
            pass
        self._talk_after = self.root.after(95, self._talk_tick)


# --------------------------------------------------------------------------- 头顶气泡
class Bubble:
    def __init__(self, canvas: tk.Canvas, width: int):
        self.canvas = canvas
        self.width = width
        self.font = tkfont.Font(family="Microsoft YaHei", size=10)
        self._items: list[int] = []
        self._hide_id = None

    def show(self, text: str, duration_ms: int | None = None, inner_voice: str | None = None) -> None:
        self.hide()
        text = (text or "").strip()
        self.current_text = text  # 3D 身体从 /v1/pet/state 读这一句
        if not text:
            return
        if len(text) > 90:
            text = text[:88] + "…"
        max_width = min(320, self.width - 40)
        lines = self._wrap(text, max_width)
        inner_lines = self._wrap(f"（{inner_voice}）", max_width) if inner_voice else []
        line_height = self.font.metrics("linespace") + 2
        measured = [self.font.measure(line) for line in lines + inner_lines]
        bubble_width = max(80, min(max_width, max(measured) + 26))
        bubble_height = line_height * (len(lines) + len(inner_lines)) + 16
        x1 = (self.width - bubble_width) / 2
        y1 = 8
        x2, y2 = x1 + bubble_width, y1 + bubble_height
        cx = self.width / 2
        # 阴影 + 小尾巴 + 气泡主体（尾巴先画，主体会盖住它的上边缘）
        shadow = self._round_rect(x1 + 3, y1 + 5, x2 + 3, y2 + 5, 12, fill="#d9e4f7", outline="")
        tail = self.canvas.create_polygon(
            cx - 9, y2 - 2, cx + 9, y2 - 2, cx, y2 + 10,
            fill="#ffffff", outline="#7aa8f0", width=2, state="normal",
        )
        body = self._round_rect(x1, y1, x2, y2, 12, fill="#ffffff", outline="#7aa8f0", width=2)
        text_item = self.canvas.create_text(
            cx, y1 + 8, text="\n".join(lines), anchor="n", justify="center",
            font=self.font, fill="#1f2a44",
        )
        self._items = [shadow, tail, body, text_item]
        if inner_lines:
            inner_font = tkfont.Font(family="Microsoft YaHei", size=9, slant="italic")
            inner_item = self.canvas.create_text(
                cx, y1 + 8 + line_height * len(lines), text="\n".join(inner_lines),
                anchor="n", justify="center", font=inner_font, fill="#8a8f99",
            )
            self._items.append(inner_item)
        for item in self._items:
            self.canvas.tag_raise(item)
        if duration_ms is None:
            duration_ms = max(2600, len(text) * 150)
        self._hide_id = self.canvas.after(duration_ms, self.hide)

    def hide(self) -> None:
        self.current_text = ""
        if self._hide_id is not None:
            try:
                self.canvas.after_cancel(self._hide_id)
            except Exception:
                pass
            self._hide_id = None
        for item in self._items:
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        self._items = []

    def _wrap(self, text: str, max_width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for char in text:
            if self.font.measure(current + char) > max_width and current:
                lines.append(current)
                current = char
            else:
                current += char
        if current:
            lines.append(current)
        return lines[:4]

    def _round_rect(self, x1, y1, x2, y2, radius, **kwargs) -> int:
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)


class Particles:
    """头顶飘的小符号（♥ / ✦ / ? / Z…）；纯 Canvas 文字，几十毫秒一步，自动清理。"""

    FONT_FAMILY = "Segoe UI Symbol"

    def __init__(self, canvas: tk.Canvas, width: int, height: int):
        self.canvas = canvas
        self.width = width
        self.height = height
        self._items: list[tuple[int, float, float, int]] = []

    def burst(self, emotion: str, count: int = 6, base_y: int | None = None) -> None:
        spec = PARTICLE_SPECS.get(emotion)
        if not spec:
            return
        glyphs, color = spec
        if base_y is None:
            base_y = max(60, self.height // 3)
        for index in range(count):
            glyph = random.choice(glyphs)
            self.canvas.after(index * 90, self._spawn, glyph, color, base_y)

    def _spawn(self, glyph: str, color: str, base_y: int) -> None:
        try:
            x = random.randint(int(self.width * 0.28), int(self.width * 0.72))
            y = base_y + random.randint(-20, 45)
            size = random.choice((13, 15, 17))
            item = self.canvas.create_text(
                x, y, text=glyph, fill=color,
                font=(self.FONT_FAMILY, size), state="normal",
            )
        except Exception:
            return
        vy = random.uniform(-2.3, -1.2)
        drift = random.uniform(-0.45, 0.45)
        self._items.append((item, vy, drift, 26))
        self.canvas.tag_raise(item)
        self._tick(item)

    def _tick(self, item: int) -> None:
        for index, (it, vy, drift, ttl) in enumerate(self._items):
            if it != item:
                continue
            if ttl <= 0:
                try:
                    self.canvas.delete(item)
                except Exception:
                    pass
                self._items.pop(index)
                return
            self.canvas.move(item, drift, vy)
            self.canvas.tag_raise(item)
            self._items[index] = (it, vy, drift, ttl - 1)
            self.canvas.after(40, self._tick, item)
            return


# --------------------------------------------------------------------------- 表情包
def sticker_photo(path: Path, max_side: int) -> ImageTk.PhotoImage | None:
    try:
        image = Image.open(path).convert("RGBA")
    except Exception as exc:
        log(f"表情包打不开：{path.name}（{exc}）")
        return None
    image.thumbnail((max_side, max_side), Image.LANCZOS)
    return ImageTk.PhotoImage(image)


def insert_sticker(text_widget: tk.Text, path: Path, keep: list, max_side: int = 120) -> None:
    """把表情包当图片插进聊天记录（keep 用来保住 PhotoImage 引用，防止被回收变空白）。"""
    photo = sticker_photo(path, max_side)
    if photo is None:
        return
    keep.append(photo)
    del keep[:-30]
    text_widget.configure(state="normal")
    text_widget.image_create("end", image=photo, padx=4, pady=2)
    text_widget.insert("end", "\n\n")
    text_widget.configure(state="disabled")
    text_widget.see("end")


class StickerPopup:
    """桌宠旁边弹出的表情包小卡片：淡入、停几秒、淡出；点一下就收起。"""

    MAX_SIDE = 170

    def __init__(self, app: "PetApp"):
        self.app = app
        self.win: tk.Toplevel | None = None
        self._photo = None
        self._jobs: list = []

    def show(self, path: Path, duration_ms: int = 4200) -> bool:
        self.hide()
        photo = sticker_photo(path, self.MAX_SIDE)
        if photo is None:
            return False
        app = self.app
        win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
            win.attributes("-alpha", 0.0)
        except Exception:
            pass
        frame = tk.Frame(win, bg="#ffffff", highlightbackground="#a9c3f2", highlightthickness=2)
        frame.pack()
        label = tk.Label(frame, image=photo, bg="#ffffff", cursor="hand2")
        label.pack(padx=6, pady=6)
        label.bind("<Button-1>", lambda _event: self.hide())
        self.win, self._photo = win, photo
        # 放在她身体左边（靠头的高度），左边放不下就放右边
        w, h = photo.width() + 16, photo.height() + 16
        body_left = app.root.winfo_x() + (app.W - app.pet_w) // 2
        x = body_left - w + 12
        if x < 0:
            x = body_left + app.pet_w - 12
        y = max(0, app.root.winfo_y() + app.H - app.pet_h - h // 3)
        win.geometry(f"{w}x{h}+{x}+{y}")
        self._fade(0.0, 0.12, then=lambda: self._jobs.append(
            app.root.after(duration_ms, lambda: self._fade(1.0, -0.12, then=self.hide))))
        return True

    def _fade(self, alpha: float, step: float, then=None) -> None:
        if self.win is None:
            return
        alpha = max(0.0, min(1.0, alpha + step))
        try:
            self.win.attributes("-alpha", alpha)
        except Exception:
            pass
        if (step > 0 and alpha >= 1.0) or (step < 0 and alpha <= 0.0):
            if then:
                then()
            return
        self._jobs.append(self.app.root.after(30, self._fade, alpha, step, then))

    def hide(self) -> None:
        for job in self._jobs:
            try:
                self.app.root.after_cancel(job)
            except Exception:
                pass
        self._jobs = []
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
        self.win = None
        self._photo = None


# --------------------------------------------------------------------------- 聊天窗
class ChatWindow(tk.Toplevel):
    def __init__(self, app: "PetApp"):
        super().__init__(app.root)
        self.app = app
        self.title(f"和{app.display_name}说话")
        self.configure(bg="#eef3ff")
        self.geometry(self._initial_geometry())
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        self._listening = False
        self.conversation_mode = False
        self._conv_job = None
        self._barge_job = None
        self._barge_reply = ""

        header = tk.Frame(self, bg="#eef3ff")
        header.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(header, text=f"🐋 {app.display_name}", bg="#eef3ff", fg="#24468f",
                 font=("Microsoft YaHei", 12, "bold")).pack(side="left")
        self.status_label = tk.Label(header, text=app.brain.status_text(), bg="#eef3ff",
                                     fg="#7a88aa", font=("Microsoft YaHei", 8))
        self.status_label.pack(side="right")

        body = tk.Frame(self, bg="#eef3ff")
        body.pack(fill="both", expand=True, padx=10, pady=4)
        self.log = tk.Text(body, height=18, wrap="word", state="disabled", relief="flat",
                           bg="#ffffff", fg="#1f2a44", font=("Microsoft YaHei", 10),
                           padx=8, pady=8, borderwidth=0)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(body, command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.tag_configure("me", foreground="#3f6fe0", font=("Microsoft YaHei", 10, "bold"))
        self.log.tag_configure("her", foreground="#24468f", font=("Microsoft YaHei", 10, "bold"))
        self.log.tag_configure("sys", foreground="#9aa5c0", font=("Microsoft YaHei", 9))

        bottom = tk.Frame(self, bg="#eef3ff")
        bottom.pack(fill="x", padx=10, pady=(2, 10))
        self.entry = tk.Entry(bottom, font=("Microsoft YaHei", 10), relief="flat",
                              bg="#ffffff", fg="#1f2a44", insertbackground="#24468f")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda event: self.send())
        self.mic_button = tk.Button(bottom, text="🎤 说话", command=self.toggle_mic,
                                    bg="#dce7ff", fg="#24468f", relief="flat",
                                    font=("Microsoft YaHei", 9), padx=8)
        self.mic_button.pack(side="left", padx=(6, 0))
        self.conv_button = tk.Button(bottom, text="🔁 连续对话", command=self.toggle_conversation,
                                     bg="#eef7e6", fg="#3f6b2a", relief="flat",
                                     font=("Microsoft YaHei", 9), padx=8)
        self.conv_button.pack(side="left", padx=(6, 0))
        tk.Button(bottom, text="发送", command=self.send, bg="#4a7cf0", fg="#ffffff",
                  relief="flat", font=("Microsoft YaHei", 9, "bold"), padx=10).pack(side="left", padx=(6, 0))

        self.append("sys", "系统", "桌宠已就位。打字，或者点「🎤 说话」用麦克风。")
        self.append("her", "大肥鱼", app.brain.status_text() + " 先聊两句试试～")
        self.entry.focus_set()
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _initial_geometry(self) -> str:
        app = self.app
        width, height = 440, 520
        x = app.root.winfo_x() - width - 16
        y = max(40, app.root.winfo_y() + app.H - height)
        if x < 20:
            x = app.root.winfo_x() + app.W + 16
        return f"{width}x{height}+{x}+{y}"

    def append(self, tag: str, who: str, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{who}：", tag)
        self.log.insert("end", text + "\n\n")
        self.log.configure(state="disabled")
        self.log.see("end")

    def append_sticker(self, who: str, path: Path) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{who}：", "her")
        self.log.configure(state="disabled")
        insert_sticker(self.log, path, self.__dict__.setdefault("_sticker_refs", []), 140)

    def send(self, text: str | None = None) -> None:
        message = (text if text is not None else self.entry.get()).strip()
        if not message:
            return
        self.app._touch()
        self.app._last_user_text = message
        self.entry.delete(0, "end")
        self.append("me", "你", message)
        agent_cfg = self.app.config.get("agent") or {}
        if getattr(self.app, "agent", None) is not None and bool(agent_cfg.get("enabled", True)):
            self.status_label.configure(text="大肥鱼正在想…（她会自己查资料/用工具）")
            self.app.ask_agent(message, self._on_reply)
        else:
            self.status_label.configure(text="大肥鱼正在想…")
            self.app.ask(message, self._on_reply)

    def _on_reply(self, reply: str, source: str) -> None:
        self.append("her", "大肥鱼", reply)
        if source == "llm":
            self.status_label.configure(text=self.app.brain.status_text())
        elif source == "offline-error":
            self.append("sys", "系统", "模型没连上，这次用的是离线台词。检查右键 → 设置里的 Key / 网络。")
            self.status_label.configure(text="离线台词模式（模型调用失败）")
        else:
            self.status_label.configure(text=self.app.brain.status_text())
        if self.conversation_mode:
            self._barge_reply = reply or ""
            self._schedule_barge_listen(350)

    def _cancel_barge_job(self) -> None:
        if self._barge_job is not None:
            try:
                self.after_cancel(self._barge_job)
            except Exception:
                pass
            self._barge_job = None

    def _schedule_barge_listen(self, delay: int = 500) -> None:
        if not self.conversation_mode:
            return
        self._cancel_barge_job()
        self._barge_job = self.after(delay, self._barge_listen)

    @staticmethod
    def _is_self_echo(text: str, reply: str) -> bool:
        import difflib
        import re as _re

        clean = lambda s: _re.sub(r"[\s，。！？、；：…,.!?;:~～~（）()\[\]【】\"'“”‘’]+", "", s or "")
        heard, said = clean(text), clean(reply)
        if len(heard) < 2:
            return True
        if not said:
            return False
        if heard in said or said in heard:
            return True
        return difflib.SequenceMatcher(None, heard, said).ratio() >= 0.45

    def _barge_listen(self) -> None:
        self._barge_job = None
        if not self.conversation_mode:
            return
        if not self.app.voice.busy():
            self._schedule_conversation_listen(300)
            return
        if not self.app.config.get("barge_in_enabled", True):
            self._schedule_barge_listen(600)
            return
        if self._listening:
            self._schedule_barge_listen(450)
            return
        self._listening = True
        self.mic_button.configure(text="👂 可打断")

        def done(text, error):
            try:
                self.after(0, self._on_barge_done, text, error)
            except Exception:
                pass

        self.app.asr.listen(done, timeout=2.0, phrase_limit=4.0)

    def _on_barge_done(self, text, error) -> None:
        self._listening = False
        self.mic_button.configure(text="🎤 说话")
        if not self.conversation_mode:
            return
        if text and not self._is_self_echo(str(text), self._barge_reply):
            self.app.voice.stop()
            self.append("sys", "系统", "（你打断了她）")
            self.entry.delete(0, "end")
            self.entry.insert(0, str(text))
            self.send()
            return
        if self.app.voice.busy():
            self._schedule_barge_listen(650)
        else:
            self._schedule_conversation_listen(300)

    def toggle_conversation(self) -> None:
        """连续对话：她说完后自动再听，不用每句点一次麦克风。"""
        self.conversation_mode = not self.conversation_mode
        if self.conversation_mode:
            self.conv_button.configure(text="⏹ 结束连续")
            self.append("sys", "系统", "连续对话开了：说完一句，她回完会自动继续听。再点一次结束。")
            self._schedule_conversation_listen(300)
        else:
            self.conv_button.configure(text="🔁 连续对话")
            self._cancel_conversation_job()
            self._cancel_barge_job()
            self.append("sys", "系统", "连续对话结束。")

    def _cancel_conversation_job(self) -> None:
        if self._conv_job is not None:
            try:
                self.after_cancel(self._conv_job)
            except Exception:
                pass
            self._conv_job = None

    def _schedule_conversation_listen(self, delay: int = 600) -> None:
        if not self.conversation_mode:
            return
        self._cancel_conversation_job()
        self._conv_job = self.after(delay, self._conversation_listen)

    def _conversation_listen(self) -> None:
        self._conv_job = None
        if not self.conversation_mode:
            return
        if self.app.voice.busy():
            self._schedule_conversation_listen(450)
            return
        if self._listening:
            self._schedule_conversation_listen(450)
            return
        self.toggle_mic()

    def toggle_mic(self) -> None:
        if self._listening:
            self._listening = False
            self.mic_button.configure(text="🎤 说话")
            return
        if self.app.voice.busy():
            # 她一开口念，点麦克风就是打断，不再等整段念完
            self.app.voice.stop()
            self.append("sys", "系统", "（打断她）")
        if not self.app.asr.ok:
            self.append("sys", "系统", "语音输入不可用：" + self.app.asr.reason)
            return
        self._listening = True
        self.mic_button.configure(text="👂 听你说…")
        self.status_label.configure(text="正在听…（最多 8 秒）")
        self.app.set_state("waiting")

        def done(text, error):
            self.after(0, self._on_asr_done, text, error)

        self.app.asr.listen(done)

    def _on_asr_done(self, text, error) -> None:
        self._listening = False
        self.mic_button.configure(text="🎤 说话")
        self.app.set_state("idle")
        if text:
            self.entry.delete(0, "end")
            self.entry.insert(0, str(text))
            if self.conversation_mode:
                # 连续对话模式：识别到就发，不再要求每句确认；回复完再自动听
                self.send()
                return
            self.entry.focus_set()
            self.append("sys", "系统", "语音识别结果已填入，确认/修改后按发送。")
            if self.app.config.get("voice_auto_send"):
                self.send()
        else:
            reason = str(error or "再试一次吧。")
            self.append("sys", "系统", "没听清：" + reason)
            self.status_label.configure(text=self.app.brain.status_text())
            if self.conversation_mode:
                self._schedule_conversation_listen(1500)
            else:
                self.app.bubble.show("没听清，再说一遍好不好？", 3200)
                try:
                    self.app.voice.speak("没听清，再说一遍好不好？")
                except Exception:
                    pass

    def close(self) -> None:
        self._cancel_conversation_job()
        self._cancel_barge_job()
        self.app.chat_window = None
        self.destroy()


# --------------------------------------------------------------------------- 快捷聊天条
class QuickChat(tk.Toplevel):
    """贴在桌宠旁边的聊天面板：带最近对话记录，敲一句就和她聊。"""

    def __init__(self, app: "PetApp"):
        super().__init__(app.root)
        self.app = app
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        self.configure(bg="#e6efff")
        self._drag = None
        self._history: list[tuple[str, str]] = []
        self._side = "right"
        self._listening = False
        outer = tk.Frame(self, bg="#ffffff", highlightbackground="#a9c3f2", highlightthickness=2)
        outer.pack(fill="both", expand=True, padx=3, pady=3)

        header = tk.Frame(outer, bg="#eaf2ff")
        header.pack(fill="x")
        avatar = tk.Label(header, text="🐋", bg="#eaf2ff", font=("Segoe UI Emoji", 14))
        avatar.pack(side="left", padx=(8, 2), pady=2)
        title = tk.Label(header, text=app.display_name, bg="#eaf2ff", fg="#1d3f86",
                         font=("Microsoft YaHei", 10, "bold"))
        title.pack(side="left")
        tk.Label(header, text="· 大肥鱼 · 在线", bg="#eaf2ff", fg="#5b9c6c",
                 font=("Microsoft YaHei", 8)).pack(side="left", padx=(2, 0))
        tk.Button(header, text="×", command=self.close, bg="#eaf2ff", fg="#7b8aa8",
                  activebackground="#dbe7ff", relief="flat",
                  font=("Microsoft YaHei", 11), padx=8, cursor="hand2").pack(side="right")
        for widget in (header, title):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag_move)

        self.transcript = tk.Text(outer, height=8, wrap="word", state="disabled",
                                  relief="flat", bg="#fbfdff", fg="#2a3550",
                                  font=("Microsoft YaHei", 9), padx=8, pady=8,
                                  borderwidth=0, spacing1=2, spacing3=6)
        self.transcript.pack(fill="both", expand=True, padx=8, pady=(6, 4))
        self.transcript.tag_configure("me_name", foreground="#3f6fe0",
                                      font=("Microsoft YaHei", 9, "bold"))
        self.transcript.tag_configure("her_name", foreground="#b07a2a",
                                      font=("Microsoft YaHei", 9, "bold"))
        self.transcript.tag_configure("me", background="#e8f1ff", foreground="#1f2a44",
                                      lmargin1=18, lmargin2=18, rmargin=4, spacing1=1)
        self.transcript.tag_configure("her", background="#fff8e6", foreground="#1f2a44",
                                      lmargin1=4, lmargin2=4, rmargin=18, spacing1=1)
        self.transcript.tag_configure("sys", foreground="#9aa5c0",
                                      font=("Microsoft YaHei", 8), justify="center")
        self.append("her", "大肥鱼", "嗯？你叫我？我在呢，直接说吧。")

        row = tk.Frame(outer, bg="#eef4ff")
        row.pack(fill="x", padx=8, pady=(2, 8))
        self.entry = tk.Entry(row, font=("Microsoft YaHei", 10), relief="flat",
                              bg="#ffffff", fg="#1f2a44", insertbackground="#24468f",
                              highlightbackground="#c9d9f7", highlightthickness=1)
        self.entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        self.entry.bind("<Return>", lambda event: self.send())
        self.mic_button = tk.Button(row, text="🎤", command=self.toggle_mic,
                                    bg="#dce7ff", fg="#24468f", activebackground="#c9d9f7",
                                    relief="flat", cursor="hand2",
                                    font=("Microsoft YaHei", 10), padx=6)
        self.mic_button.pack(side="left", padx=(0, 5))
        tk.Button(row, text="发送", command=self.send, bg="#4a7cf0", fg="#ffffff",
                  activebackground="#3b6be0", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 9, "bold"), padx=10).pack(side="left")
        self.tail = tk.Canvas(self, bg="#e6efff", highlightthickness=0)
        self.geometry("400x330")
        self.follow_pet()
        self.entry.focus_set()

    def follow_pet(self) -> None:
        """贴在桌宠旁边：右侧放得下就放右边，否则左边；尾巴指向她。"""
        try:
            px, py = self.app.root.winfo_x(), self.app.root.winfo_y()
            pw, ph = self.app.W, self.app.H
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            width, height = 400, 330
            if px + pw + 8 + width <= screen_w - 8:
                self._side = "right"
                x = px + pw + 8
            elif px - width - 8 >= 8:
                self._side = "left"
                x = px - width - 8
            else:
                self._side = "right"
                x = max(8, screen_w - width - 8)
            y = max(20, min(screen_h - height - 40, py + 90))
            self.geometry(f"{width}x{height}+{x}+{y}")
            self._draw_tail()
        except Exception:
            pass

    def _draw_tail(self) -> None:
        try:
            self.tail.delete("all")
            if self._side == "right":
                self.tail.place(x=0, y=120, width=16, height=28)
                self.tail.create_polygon(15, 0, 15, 28, 1, 14,
                                         fill="#ffffff", outline="#a9c3f2", width=2)
            else:
                self.tail.place(x=384, y=120, width=16, height=28)
                self.tail.create_polygon(1, 0, 1, 28, 15, 14,
                                         fill="#ffffff", outline="#a9c3f2", width=2)
        except Exception:
            pass

    def append(self, tag: str, who: str, text: str) -> None:
        self._history.append((who, text))
        del self._history[:-40]
        try:
            name_tag = "me_name" if tag == "me" else "her_name" if tag == "her" else "sys"
            bubble = "me" if tag == "me" else "her" if tag == "her" else "sys"
            self.transcript.configure(state="normal")
            self.transcript.insert("end", f"{who}\n", name_tag)
            self.transcript.insert("end", (text + "\n\n"), bubble)
            self.transcript.configure(state="disabled")
            self.transcript.see("end")
        except Exception:
            pass

    def append_sticker(self, who: str, path: Path) -> None:
        try:
            self.transcript.configure(state="normal")
            self.transcript.insert("end", f"{who}\n", "her_name")
            self.transcript.configure(state="disabled")
            insert_sticker(self.transcript, path, self.__dict__.setdefault("_sticker_refs", []), 110)
        except Exception:
            pass

    def _start_drag(self, event) -> None:
        self._drag = (event.x_root, event.y_root, self.winfo_x(), self.winfo_y())

    def _drag_move(self, event) -> None:
        if not self._drag:
            return
        sx, sy, wx, wy = self._drag
        self.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def send(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.append("me", "你", text)
        self.app._touch()
        self.app._last_user_text = text
        on_reply = self._on_reply
        agent_cfg = self.app.config.get("agent") or {}
        if getattr(self.app, "agent", None) is not None and bool(agent_cfg.get("enabled", True)):
            self.app.ask_agent(text, on_reply)
        else:
            self.app.ask(text, on_reply)

    def _on_reply(self, reply: str, source: str) -> None:
        if source == "agent":
            self.append("sys", "系统", "（用了工具）")
        self.append("her", "大肥鱼", (reply or "").strip() or "……")

    def toggle_mic(self) -> None:
        """快捷聊天条里的语音输入：识别后填进输入框并直接发送。"""
        if self._listening:
            self._listening = False
            self.mic_button.configure(text="🎤")
            return
        if not self.app.asr.ok:
            self.append("sys", "系统", "语音输入不可用：" + str(self.app.asr.reason))
            return
        self._listening = True
        self.mic_button.configure(text="👂")
        self.append("sys", "系统", "正在听…（最多 8 秒）")
        self.app.set_state("waiting")

        def done(text, error):
            try:
                self.after(0, self._on_asr_done, text, error)
            except Exception:
                pass

        self.app.asr.listen(done)

    def _on_asr_done(self, text, error) -> None:
        self._listening = False
        try:
            self.mic_button.configure(text="🎤")
        except Exception:
            pass
        self.app.set_state("idle")
        if text:
            self.entry.delete(0, "end")
            self.entry.insert(0, str(text))
            self.entry.focus_set()
            self.append("sys", "系统", "语音识别好了，确认/修改后按发送。")
            if self.app.config.get("voice_auto_send"):
                self.send()
        else:
            reason = str(error or "再试一次吧。")
            self.append("sys", "系统", "没听清：" + reason)
            self.app.bubble.show("没听清，再说一遍好不好？", 3200)
            try:
                self.app.voice.speak("没听清，再说一遍好不好？")
            except Exception:
                pass

    def close(self) -> None:
        self.app.quick_chat = None
        self.destroy()


class ApprovalPrompt(tk.Toplevel):
    """桌宠旁边的确认气泡：她要用工具时，不弹系统框，直接在鱼旁边问「允许吗」。"""

    def __init__(self, app: "PetApp", tool_name: str, preview: str, callback):
        super().__init__(app.root)
        self.app = app
        self._callback = callback
        self._done = False
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        self.configure(bg="#e6efff")
        self._side = "right"
        outer = tk.Frame(self, bg="#ffffff", highlightbackground="#f0b45a",
                         highlightthickness=2)
        outer.pack(fill="both", expand=True, padx=3, pady=3)
        header = tk.Frame(outer, bg="#fff6e6")
        header.pack(fill="x")
        tk.Label(header, text="🐋", bg="#fff6e6", font=("Segoe UI Emoji", 14)).pack(
            side="left", padx=(8, 2), pady=2)
        tk.Label(header, text=f"{app.display_name}想动一下电脑", bg="#fff6e6", fg="#8a5a12",
                 font=("Microsoft YaHei", 10, "bold")).pack(side="left")
        tk.Label(header, text=f"· {tool_name}", bg="#fff6e6", fg="#b07a2a",
                 font=("Microsoft YaHei", 8)).pack(side="left", padx=(3, 0))
        body = tk.Label(outer, text=str(preview)[:700], bg="#ffffff", fg="#2a3550",
                        justify="left", anchor="w", wraplength=356,
                        font=("Microsoft YaHei", 9))
        body.pack(fill="x", padx=12, pady=(8, 6))
        buttons = tk.Frame(outer, bg="#ffffff")
        buttons.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(buttons, text="拒绝", command=lambda: self._finish(False),
                  bg="#eef1f6", fg="#5a6b8f", activebackground="#dde3ee",
                  relief="flat", cursor="hand2", padx=16,
                  font=("Microsoft YaHei", 9)).pack(side="right", padx=(8, 0))
        tk.Button(buttons, text="允许这次", command=lambda: self._finish(True),
                  bg="#4a7cf0", fg="#ffffff", activebackground="#3b6be0",
                  relief="flat", cursor="hand2", padx=14,
                  font=("Microsoft YaHei", 9, "bold")).pack(side="right")
        self.bind("<Return>", lambda event: self._finish(True))
        self.bind("<Escape>", lambda event: self._finish(False))
        self.update_idletasks()
        height = max(150, self.winfo_reqheight())
        self.follow_pet(height)
        self.after(120, lambda: self.follow_pet(self.winfo_height() or height))
        self.after(180000, lambda: self._finish(False))
        self.focus_force()

    def follow_pet(self, height: int) -> None:
        try:
            px, py = self.app.root.winfo_x(), self.app.root.winfo_y()
            pw = self.app.W
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            if px < 1 or py < 1:  # 桌宠还没映射好时，先靠屏幕右下角
                px, py = max(200, screen_w - 520), max(200, screen_h - 620)
            width = 400
            if px + pw + 8 + width <= screen_w - 8:
                self._side = "right"
                x = px + pw + 8
            elif px - width - 8 >= 8:
                self._side = "left"
                x = px - width - 8
            else:
                self._side = "right"
                x = max(8, screen_w - width - 8)
            y = max(20, min(screen_h - height - 40, py + 150))
            self.geometry(f"{width}x{height}+{x}+{y}")
            if self._side == "right":
                self.bind("<Configure>", lambda event: None)
        except Exception:
            pass

    def _finish(self, ok: bool) -> None:
        if self._done:
            return
        self._done = True
        try:
            self._callback(bool(ok))
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def destroy(self) -> None:
        if not self._done:
            self._done = True
            try:
                self._callback(False)
            except Exception:
                pass
        super().destroy()


# --------------------------------------------------------------------------- 设置窗
class SettingsWindow(tk.Toplevel):
    def __init__(self, app: "PetApp"):
        super().__init__(app.root)
        self.app = app
        self.title(f"{app.display_name}设置")
        self.configure(bg="#f5f8ff")
        self.geometry("470x780")
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        config = app.config
        llm = config.setdefault("llm", {})

        form = tk.Frame(self, bg="#f5f8ff")
        form.pack(fill="both", expand=True, padx=18, pady=14)

        self.enabled = tk.BooleanVar(value=bool(llm.get("enabled")))
        tk.Checkbutton(form, text="启用在线模型（OpenAI 兼容接口）", variable=self.enabled,
                       bg="#f5f8ff", fg="#2a3550", font=("Microsoft YaHei", 9)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.base_url = self._row(form, 1, "接口地址", llm.get("base_url") or "https://api.deepseek.com/v1")
        self.model = self._row(form, 2, "模型名", llm.get("model") or "deepseek-chat")
        self.api_key = self._row(form, 3, "API Key", llm.get("api_key") or "", show="*")
        self.voice = self._row(form, 4, "音色", config.get("voice") or "zh-CN-XiaoyiNeural")
        self.user_name = self._row(form, 5, "你的称呼", config.get("user_name") or app.brain.user_name())

        self.mute = tk.BooleanVar(value=bool(config.get("mute")))
        tk.Checkbutton(form, text="静音（只显示文字）", variable=self.mute, bg="#f5f8ff",
                       fg="#2a3550", font=("Microsoft YaHei", 9)).grid(row=6, column=0, columnspan=2,
                                                                       sticky="w", pady=(8, 0))
        self.greet = tk.BooleanVar(value=bool(config.get("greet_on_start", True)))
        tk.Checkbutton(form, text="启动时打招呼", variable=self.greet, bg="#f5f8ff",
                       fg="#2a3550", font=("Microsoft YaHei", 9)).grid(row=7, column=0, columnspan=2,
                                                                       sticky="w")
        self.topmost = tk.BooleanVar(value=bool(config.get("always_on_top", True)))
        tk.Checkbutton(form, text="窗口置顶", variable=self.topmost, bg="#f5f8ff",
                       fg="#2a3550", font=("Microsoft YaHei", 9)).grid(row=8, column=0, columnspan=2,
                                                                       sticky="w")
        proactive = config.get("proactive") or {}
        self.proactive = tk.BooleanVar(value=bool(proactive.get("enabled", True)))
        tk.Checkbutton(form, text="允许主动搭话（闲太久时冒个泡）", variable=self.proactive,
                       bg="#f5f8ff", fg="#2a3550", font=("Microsoft YaHei", 9)).grid(
            row=9, column=0, columnspan=2, sticky="w")
        dsh_cfg = config.get("dsh_status") or {}
        self.dsh_follow = tk.BooleanVar(value=bool(dsh_cfg.get("enabled", True)))
        tk.Checkbutton(form, text="跟随 DSH 工作状态（需装 dsh-whale-bridge 插件）",
                       variable=self.dsh_follow, bg="#f5f8ff", fg="#2a3550",
                       font=("Microsoft YaHei", 9)).grid(row=10, column=0, columnspan=2, sticky="w")
        personal_cfg = config.get("personal") or {}
        self.personal_enabled = tk.BooleanVar(value=bool(personal_cfg.get("enabled", True)))
        tk.Checkbutton(form, text="接入个人库（读取 Shared 的个人背景/偏好/记忆）",
                       variable=self.personal_enabled, bg="#f5f8ff", fg="#2a3550",
                       font=("Microsoft YaHei", 9)).grid(row=11, column=0, columnspan=2, sticky="w")
        self.personal_path = self._row(
            form, 12, "个人库路径",
            personal_cfg.get("library_path") or "")
        agent_cfg = config.get("agent") or {}
        self.agent_enabled = tk.BooleanVar(value=bool(agent_cfg.get("enabled", True)))
        tk.Checkbutton(form, text="允许她帮我做事（执行命令/写文件前会先问我）",
                       variable=self.agent_enabled, bg="#f5f8ff", fg="#2a3550",
                       font=("Microsoft YaHei", 9)).grid(row=13, column=0, columnspan=2, sticky="w")
        overview_cfg = config.get("overview") or {}
        self.overview_enabled = tk.BooleanVar(value=bool(overview_cfg.get("enabled", True)))
        tk.Checkbutton(form, text="自动抓取工作总览（最近做过/待办）",
                       variable=self.overview_enabled, bg="#f5f8ff", fg="#2a3550",
                       font=("Microsoft YaHei", 9)).grid(row=14, column=0, columnspan=2, sticky="w")

        test_row = tk.Frame(form, bg="#f5f8ff")
        test_row.grid(row=15, column=0, columnspan=2, sticky="we", pady=(10, 0))
        self.api_test_btn = tk.Button(test_row, text="测试连接", command=self.test_connection,
                                      bg="#dce7ff", fg="#24468f", relief="flat",
                                      font=("Microsoft YaHei", 9), padx=10)
        self.api_test_btn.pack(side="left")
        self.api_test_label = tk.Label(test_row, text="", bg="#f5f8ff", fg="#7a88aa",
                                       font=("Microsoft YaHei", 8), justify="left",
                                       wraplength=300, anchor="w")
        self.api_test_label.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self.memory_label = tk.Label(form,
                                     text=app.brain.memory_summary() + "\n" + app.brain.personal_summary(),
                                     bg="#f5f8ff", fg="#7a88aa", font=("Microsoft YaHei", 8),
                                     justify="left", anchor="w", wraplength=420)
        self.memory_label.grid(row=16, column=0, columnspan=2, sticky="we", pady=(8, 0))

        tips = ("提示：DeepSeek 开放平台的 Key 可以在 platform.deepseek.com 申请；\n"
                "其它 OpenAI 兼容服务（如本地 vLLM / Ollama 的 /v1）也能填。\n"
                "Key 也可以放进 DFY_API_KEY 环境变量或 pet/.secret_api_key 文件。\n"
                "语音输入走系统麦克风 + 在线识别；语音输出走 edge-tts（需要联网）。")
        tk.Label(form, text=tips, bg="#f5f8ff", fg="#7a88aa", justify="left",
                 font=("Microsoft YaHei", 8)).grid(row=17, column=0, columnspan=2, sticky="w", pady=(10, 0))

        buttons = tk.Frame(self, bg="#f5f8ff")
        buttons.pack(fill="x", padx=18, pady=(0, 14))
        tk.Button(buttons, text="保存", command=self.save, bg="#4a7cf0", fg="#ffffff",
                  relief="flat", font=("Microsoft YaHei", 10, "bold"), padx=18).pack(side="right")
        tk.Button(buttons, text="取消", command=self.destroy, bg="#dce7ff", fg="#24468f",
                  relief="flat", font=("Microsoft YaHei", 10), padx=14).pack(side="right", padx=(0, 8))

    def _row(self, parent, row: int, label: str, value: str, show: str | None = None):
        tk.Label(parent, text=label, bg="#f5f8ff", fg="#2a3550",
                 font=("Microsoft YaHei", 9)).grid(row=row, column=0, sticky="e", pady=3, padx=(0, 8))
        entry = tk.Entry(parent, font=("Microsoft YaHei", 9), relief="solid", borderwidth=1,
                         show=show or "")
        entry.insert(0, str(value))
        entry.grid(row=row, column=1, sticky="we", pady=3)
        parent.columnconfigure(1, weight=1)
        return entry

    def test_connection(self) -> None:
        """用输入框里当前的 Key/地址/模型测一次；不写盘，测完恢复现场。"""
        llm = self.app.config.setdefault("llm", {})
        backup = dict(llm)
        llm["enabled"] = True
        llm["api_key"] = self.api_key.get().strip() or llm.get("api_key", "")
        llm["base_url"] = self.base_url.get().strip() or llm.get("base_url")
        llm["model"] = self.model.get().strip() or llm.get("model")
        self.api_test_btn.configure(state="disabled")
        self.api_test_label.configure(text="正在测试连接…", fg="#7a88aa")

        def worker() -> None:
            try:
                ok, detail = self.app.brain.test_connection()
            except Exception as exc:  # 不让 UI 线程收到异常
                ok, detail = False, "测试失败：" + str(exc)[:120]
            self.after(0, self._on_test_done, ok, detail, backup)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ok: bool, detail: str, backup: dict) -> None:
        self.app.config["llm"] = backup
        self.api_test_btn.configure(state="normal")
        self.api_test_label.configure(text=detail, fg="#2e7d32" if ok else "#c62828")

    def save(self) -> None:
        config = self.app.config
        llm = config.setdefault("llm", {})
        llm["enabled"] = bool(self.enabled.get())
        llm["base_url"] = self.base_url.get().strip() or "https://api.deepseek.com/v1"
        llm["model"] = self.model.get().strip() or "deepseek-chat"
        llm["api_key"] = self.api_key.get().strip()
        config["voice"] = self.voice.get().strip() or "zh-CN-XiaoyiNeural"
        config["user_name"] = self.user_name.get().strip()
        config["mute"] = bool(self.mute.get())
        config["greet_on_start"] = bool(self.greet.get())
        config["always_on_top"] = bool(self.topmost.get())
        proactive = config.setdefault("proactive", {})
        if not isinstance(proactive, dict):
            proactive = {}
            config["proactive"] = proactive
        proactive["enabled"] = bool(self.proactive.get())
        dsh_cfg = config.setdefault("dsh_status", {})
        if not isinstance(dsh_cfg, dict):
            dsh_cfg = {}
            config["dsh_status"] = dsh_cfg
        dsh_cfg["enabled"] = bool(self.dsh_follow.get())
        personal_cfg = config.setdefault("personal", {})
        if not isinstance(personal_cfg, dict):
            personal_cfg = {}
            config["personal"] = personal_cfg
        personal_cfg["enabled"] = bool(self.personal_enabled.get())
        personal_cfg["library_path"] = self.personal_path.get().strip()
        agent_cfg = config.setdefault("agent", {})
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}
            config["agent"] = agent_cfg
        agent_cfg["enabled"] = bool(self.agent_enabled.get())
        overview_cfg = config.setdefault("overview", {})
        if not isinstance(overview_cfg, dict):
            overview_cfg = {}
            config["overview"] = overview_cfg
        overview_cfg["enabled"] = bool(self.overview_enabled.get())
        # 个人库开关/路径即时生效（路径变了下次启动完全重载；这里先 reload）
        personal_obj = getattr(self.app.brain, "personal", None)
        if personal_obj is not None:
            try:
                personal_obj.enabled = bool(personal_cfg["enabled"])
                personal_obj.reload(force=True)
            except Exception as exc:
                self.app.log(f"刷新个人库失败：{exc}")
        if hasattr(self.app, "proactive_var"):
            self.app.proactive_var.set(proactive["enabled"])
        if hasattr(self.app, "dsh_var"):
            self.app.dsh_var.set(dsh_cfg["enabled"])
        self.app.brain.memory["user_name"] = config["user_name"]
        self.app.save_config()
        try:
            self.app.root.attributes("-topmost", config["always_on_top"])
        except Exception:
            pass
        if self.app.chat_window is not None:
            self.app.chat_window.status_label.configure(text=self.app.brain.status_text())
        self.app.bubble.show("设置存好啦。", 2200)
        self.destroy()


# --------------------------------------------------------------------------- 启动画面
class Splash:
    """启动时先弹的小卡片：她的头像 +「正在醒来」+ 进度，把加载素材的几秒填上。"""

    def __init__(self, root: tk.Tk, name: str, avatar: Path | None):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        try:
            self.win.attributes("-topmost", True)
            self.win.attributes("-alpha", 0.0)
        except Exception:
            pass
        frame = tk.Frame(self.win, bg="#ffffff", highlightbackground="#a9c3f2", highlightthickness=2)
        frame.pack()
        self._photo = None
        if avatar is not None:
            try:
                image = Image.open(avatar)
                image.seek(0)
                image = image.convert("RGBA")
                box = image.getchannel("A").getbbox()
                if box:
                    image = image.crop(box)
                image.thumbnail((180, 200), Image.LANCZOS)
                card = Image.new("RGBA", image.size, (255, 255, 255, 255))
                card.paste(image, (0, 0), image)
                self._photo = ImageTk.PhotoImage(card)
                tk.Label(frame, image=self._photo, bg="#ffffff").pack(padx=24, pady=(18, 6))
            except Exception:
                self._photo = None
        tk.Label(frame, text=f"{name} 正在醒来", bg="#ffffff", fg="#1d3f86",
                 font=("Microsoft YaHei", 13, "bold")).pack(padx=24)
        self.stage_var = tk.StringVar(value="伸个懒腰……")
        tk.Label(frame, textvariable=self.stage_var, bg="#ffffff", fg="#7a88aa",
                 font=("Microsoft YaHei", 9)).pack(padx=24, pady=(2, 16))
        self.win.update_idletasks()
        w, h = self.win.winfo_width(), self.win.winfo_height()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2 - 40}")
        for step in range(1, 6):  # 淡入
            self._alpha(step / 5)
        self.stage("伸个懒腰……")

    def _alpha(self, value: float) -> None:
        try:
            self.win.attributes("-alpha", value)
            self.win.update()
        except Exception:
            pass

    def stage(self, text: str) -> None:
        """加载到哪一步了（init 是同步的，所以每步手动刷新一下界面）。"""
        try:
            self.stage_var.set(text)
            self.win.update()
        except Exception:
            pass

    def close(self, then=None) -> None:
        def fade(step: int) -> None:
            if step <= 0:
                try:
                    self.win.destroy()
                except Exception:
                    pass
                if then:
                    then()
                return
            try:
                self.win.attributes("-alpha", step / 6)
            except Exception:
                pass
            self.root.after(35, fade, step - 1)
        fade(6)


# --------------------------------------------------------------------------- 记忆窗口
class MemoryWindow(tk.Toplevel):
    """看她记得什么：事实（可删/调重要度）、日记、心情、最近一次召回、饭量、提醒。"""

    def __init__(self, app: "PetApp"):
        super().__init__(app.root)
        from tkinter import ttk
        self.app = app
        self.title(f"{app.display_name}的记忆")
        self.geometry("760x520")
        self.configure(bg="#eef3ff")
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self._facts_tab(notebook, ttk)
        for title, builder in (("日记", self._diary_text), ("心情", self._mood_text),
                               ("最近一次召回", self._recall_text), ("饭量", self._appetite_text),
                               ("提醒", self._reminder_text)):
            frame = tk.Frame(notebook, bg="#ffffff")
            text = tk.Text(frame, wrap="word", relief="flat", font=("Microsoft YaHei", 10), padx=10, pady=8)
            text.pack(fill="both", expand=True)
            try:
                text.insert("end", builder())
            except Exception as exc:
                text.insert("end", f"读不出来：{exc}")
            text.configure(state="disabled")
            notebook.add(frame, text=title)
        bar = tk.Frame(self, bg="#eef3ff")
        bar.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(bar, text="打开原始 memory.json", command=self._open_raw, relief="flat",
                  bg="#dbe7ff").pack(side="right")

    # ---------- 记得的事 ----------
    def _facts_tab(self, notebook, ttk) -> None:
        from memory import freshness
        frame = tk.Frame(notebook, bg="#ffffff")
        columns = ("date", "text", "key", "imp", "fresh", "state")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=16)
        for col, title, width in (("date", "日期", 90), ("text", "内容", 320), ("key", "事实键", 110),
                                  ("imp", "重要度", 60), ("fresh", "鲜活度", 60), ("state", "状态", 70)):
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor="w" if col == "text" else "center")
        tree.pack(fill="both", expand=True)
        self.tree = tree
        buttons = tk.Frame(frame, bg="#ffffff")
        buttons.pack(fill="x", pady=4)
        for label, command in (("删掉这条", self._forget), ("更重要", lambda: self._importance(+1)),
                               ("没那么重要", lambda: self._importance(-1))):
            tk.Button(buttons, text=label, command=command, relief="flat", bg="#e6efff").pack(side="left", padx=4)
        notebook.add(frame, text="记得的事")
        self._freshness = freshness
        self._fill_facts()

    def _fill_facts(self) -> None:
        self.tree.delete(*self.tree.get_children())
        store = self.app.brain.memory_store
        for fact in sorted(store.facts, key=lambda f: f.get("time", 0), reverse=True):
            self.tree.insert("", "end", values=(
                fact.get("ts") or "", fact.get("text", ""), fact.get("key") or "",
                fact.get("importance", 1), f"{self._freshness(fact):.0%}",
                "已被取代" if fact.get("superseded") else "有效"))

    def _selected_text(self) -> str:
        selection = self.tree.selection()
        return self.tree.item(selection[0], "values")[1] if selection else ""

    def _forget(self) -> None:
        text = self._selected_text()
        if text and self.app.brain.memory_store.forget(text):
            self.app.brain.memory_store.save()
            self._fill_facts()

    def _importance(self, step: int) -> None:
        text = self._selected_text()
        if not text:
            return
        store = self.app.brain.memory_store
        current = next((int(f.get("importance", 1)) for f in store.facts if f.get("text") == text), 1)
        store.set_importance(text, current + step)
        store.save()
        self._fill_facts()

    # ---------- 其余标签 ----------
    def _diary_text(self) -> str:
        store = self.app.brain.memory_store
        lines = ["【记忆日记】（旧对话压缩出来的）"]
        for episode in reversed(store.episodes[-40:]):
            lines.append(f"· {format_time(episode.get('end') or episode.get('time'))}  {episode.get('text', '')}")
        diary_dir = DATA_DIR / "home" / "diary"
        files = sorted(diary_dir.glob("*.md"), reverse=True)[:7] if diary_dir.exists() else []
        if files:
            lines.append("\n【她自己写的日记】")
            for path in files:
                lines.append(f"—— {path.stem} ——\n{path.read_text(encoding='utf-8', errors='ignore').strip()}")
        return "\n".join(lines) if len(lines) > 1 else "还没有日记。聊满 40 条会压缩出第一条。"

    def _mood_text(self) -> str:
        state = self.app.living.state
        n = state.needs
        lines = [f"现在：{state.summary()}",
                 f"饿 {n['hunger']:.0f} · 精力 {n['energy']:.0f} · 心情 {n['mood']:.0f} · 无聊 {n['boredom']:.0f} · 想主人 {n['social']:.0f}",
                 "情绪余温：" + ("、".join(f"{k} {v:.1f}" for k, v in state.feelings.items()) or "平静"),
                 "\n【心情时间线】"]
        for entry in reversed(state.mood_log[-30:]):
            when = time.strftime("%m-%d %H:%M", time.localtime(entry.get("time", 0)))
            lines.append(f"· {when}  {entry.get('delta', 0):+d} {entry.get('emotion', '')}  —— {entry.get('reason', '')}")
        return "\n".join(lines)

    def _recall_text(self) -> str:
        recall = self.app.brain.memory_store.last_recall or {}
        if not recall:
            return "还没有召回记录（下次聊天后再看）。"
        lines = [f"问的是：{recall.get('query', '')}"]
        for item in recall.get("items", []):
            mark = "✓ 想起来了" if item.get("chosen") else f"✗ {item.get('why', '')}"
            lines.append(f"· {item.get('text', '')}  （相关 {item.get('score', 0)}，鲜活 {item.get('fresh', 0)}）  {mark}")
        return "\n".join(lines)

    def _appetite_text(self) -> str:
        appetite = self.app.appetite
        lines = [appetite.summary(), "", "【今天按用途】"]
        for purpose, tokens in (appetite.data.get("eaten") or {}).items():
            lines.append(f"· {purpose}: {tokens}")
        history = appetite.data.get("history") or []
        if history:
            lines.append("\n【前几天】")
            lines.extend(f"· {h.get('day')}: {h.get('eaten', 0)} token（缓存 {h.get('hit', 0)}）" for h in reversed(history[-14:]))
        return "\n".join(lines)

    def _reminder_text(self) -> str:
        items = self.app.reminders.upcoming(20)
        if not items:
            return "没有待提醒的事。跟她说「明天下午三点提醒我开会」试试。"
        return "\n".join(f"· {self.app.reminders.describe(i)}：{i.get('text', '')}" for i in items)

    def _open_raw(self) -> None:
        try:
            os.startfile(str(BASE / "memory.json"))
        except Exception as exc:
            self.app.bubble.show(f"打不开：{exc}", 3000)


# --------------------------------------------------------------------------- 主程序
class NestWindow(tk.Toplevel):
    """她的小窝：桌面上的房间；东西存在 pet/home/nest.json，她可以自己添。"""

    W, H = 580, 460
    CATALOG = ("🍙 白饭", "🐟 小鱼干", "🍉 西瓜", "📚 书", "🧸 玩偶", "⭐ 星星灯",
               "🪴 小盆栽", "🎀 蝴蝶结", "🕯️ 蜡烛", "💎 亮晶晶", "🎮 小游戏机", "🧺 篮子")

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("小鲸鱼的小窝")
        self.resizable(False, False)
        self.configure(bg="#f7ead8")
        self.geometry(f"{self.W}x{self.H}+{max(60, app.root.winfo_x() - 120)}+{max(60, app.root.winfo_y() - 260)}")
        self._photo = None
        self.items = self._load()
        self.canvas = tk.Canvas(self, width=self.W, height=self.H - 46, bg="#f7ead8", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        bar = tk.Frame(self, bg="#f7ead8")
        bar.pack(fill="x", pady=(0, 8))
        tk.Button(bar, text="让她自己添一件", command=self.app.build_nest).pack(side="left", padx=10)
        tk.Button(bar, text="放点东西…", command=self.app.add_nest_item).pack(side="left")
        tk.Button(bar, text="关门", command=self.destroy).pack(side="right", padx=10)
        self.draw()

    def _path(self):
        return home_root(BASE) / "nest.json"

    def _load(self) -> list:
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else None
            cleaned = [str(item)[:24] for item in (items or []) if str(item).strip()]
            return cleaned[:18] or list(self.CATALOG[:6])
        except Exception:
            return list(self.CATALOG[:6])

    def save(self) -> None:
        try:
            write_home_text(home_root(BASE), "nest.json",
                            json.dumps({"items": self.items}, ensure_ascii=False, indent=1))
        except Exception:
            pass

    def add_item(self, label: str) -> None:
        label = str(label or "").strip()[:24]
        if label and label not in self.items and len(self.items) < 18:
            self.items.append(label)
            self.save()
            self.draw()

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        # 墙/地
        for i in range(0, self.H, 8):
            color = "#fbefe0" if i < 300 else "#efd7b8"
            c.create_rectangle(0, i, self.W, i + 8, fill=color, outline="")
        c.create_rectangle(0, 300, self.W, self.H, fill="#e8c9a5", outline="")
        c.create_rectangle(0, 300, self.W, 306, fill="#d8ae84", outline="")
        # 窗和月光
        c.create_rectangle(46, 56, 196, 206, fill="#cfe4ff", outline="#8aa9cf", width=3)
        c.create_line(121, 56, 121, 206, fill="#8aa9cf", width=2)
        c.create_line(46, 131, 196, 131, fill="#8aa9cf", width=2)
        c.create_oval(146, 74, 176, 104, fill="#fff4c8", outline="")
        c.create_text(121, 34, text="她的窗", fill="#7b6654", font=("Microsoft YaHei", 10))
        # 架子
        c.create_rectangle(410, 92, 548, 102, fill="#b98c61", outline="")
        for i, label in enumerate(self.items[:6]):
            c.create_text(430 + (i % 3) * 44, 78, text=label.split(" ")[0],
                          font=("Segoe UI Emoji", 18))
        # 地毯与窝
        c.create_oval(70, 356, 510, 442, fill="#d9a77d", outline="#b5825c", width=2)
        c.create_oval(150, 320, 430, 420, fill="#f4d7a8", outline="#c89a68", width=3)
        c.create_oval(176, 338, 404, 398, fill="#ffe9c4", outline="")
        # 她的立绘
        try:
            frames = getattr(self.app.library, "states", {}).get("idle") or []
            if frames:
                image = frames[0][0]
                image = image.resize((180, int(180 * image.height / max(1, image.width))), Image.LANCZOS)
                self._photo = ImageTk.PhotoImage(image)
                c.create_image(290, 378, anchor="s", image=self._photo)
        except Exception:
            c.create_text(290, 360, text="🐳", font=("Segoe UI Emoji", 64))
        # 地面小物
        for i, label in enumerate(self.items[6:14]):
            c.create_text(120 + (i % 4) * 110, 430 - (i // 4) * 32,
                          text=label, fill="#5c4734", font=("Microsoft YaHei", 9))
        c.create_text(290, 446, text="她自己的家 · 想放什么就放什么",
                      fill="#8a6d50", font=("Microsoft YaHei", 9))


class PetApp:
    def __init__(self, demo: bool = False, hidden: bool = False):
        self.demo = demo
        self.hidden = hidden
        # 从哪读就写回哪：DFY_CONFIG 指向临时配置时（冒烟测试），绝不能写坏真正的 config.json
        self.config_path = Path(os.environ.get("DFY_CONFIG") or CONFIG_PATH).expanduser()
        self.config = load_json(self.config_path, {})
        self.config.setdefault("llm", {})
        self.config.setdefault("voice", "zh-CN-XiaoyiNeural")
        self.config.setdefault("voice_rate", "-4%")
        self.config.setdefault("voice_pitch", "+6Hz")
        self.config.setdefault("greet_on_start", True)
        self.config.setdefault("always_on_top", True)
        self.config.setdefault("mute", False)
        self.config.setdefault("volume", 800)
        proactive = self.config.setdefault("proactive", {})
        if not isinstance(proactive, dict):
            proactive = {}
            self.config["proactive"] = proactive
        proactive.setdefault("enabled", True)
        proactive.setdefault("min_idle_minutes", 25)
        proactive.setdefault("min_interval_minutes", 25)
        proactive.setdefault("daily_limit", 8)
        feed_counts = self.config.setdefault("feed_counts", {})
        if not isinstance(feed_counts, dict):
            feed_counts = {}
            self.config["feed_counts"] = feed_counts
        for food in FEED_LINES:
            feed_counts.setdefault(food, 0)
        self.config.setdefault("inner_voice_chance", 1 / 6)
        self.config.setdefault("sticker_chance", 0.3)  # 聊天回复后顺手甩个表情包的概率
        dsh_status = self.config.setdefault("dsh_status", {})
        if not isinstance(dsh_status, dict):
            dsh_status = {}
            self.config["dsh_status"] = dsh_status
        dsh_status.setdefault("enabled", True)
        dsh_status.setdefault("path", "~/.dsh/whale-bridge/status.json")
        dsh_status.setdefault("poll_ms", 1000)
        personal = self.config.setdefault("personal", {})
        if not isinstance(personal, dict):
            personal = {}
            self.config["personal"] = personal
        personal.setdefault("enabled", False)
        personal.setdefault("library_path", "")
        personal.setdefault("max_chars", 1600)
        overview = self.config.setdefault("overview", {})
        if not isinstance(overview, dict):
            overview = {}
            self.config["overview"] = overview
        overview.setdefault("enabled", True)
        overview.setdefault("min_interval_minutes", 10)
        overview.setdefault("refresh_minutes", 15)
        overview.setdefault("roots", [])
        agent = self.config.setdefault("agent", {})
        if not isinstance(agent, dict):
            agent = {}
            self.config["agent"] = agent
        agent.setdefault("enabled", True)
        agent.setdefault("max_steps", 6)
        agent.setdefault("allowed_roots", [str(BASE.parent)])
        self.config.setdefault("quick_chat", False)
        self.config.setdefault("chat_open_on_start", False)
        self.config.setdefault("autonomy_enabled", True)
        self.config.setdefault("self_improve_enabled", True)
        self.config.setdefault("self_improve_autonomous", True)
        self.config.setdefault("self_improve_model_enabled", True)
        self.config.setdefault("self_improve_home_enabled", True)
        self.config.setdefault("self_improve_interval_min", 30)
        self.config.setdefault("self_improve_home_interval_min", 60)
        self.config.setdefault("self_improve_estimate_tokens", 0)
        self.config.setdefault("autonomy_daily_limit", 0)
        self.config.setdefault("autonomy_model", "deepseek-v4-pro")
        self.config.setdefault("autonomy_max_steps", 24)
        self.config.setdefault("position_awareness", True)
        self.config.setdefault("ambient_screen_enabled", True)
        self.config.setdefault("ambient_screen_interval", 300)
        self.config.setdefault("roam_enabled", False)
        self.config.setdefault("click_sound", True)
        self.config.setdefault("click_sound_style", "duck")
        self.config.setdefault("personality_reactions", True)
        self.config.setdefault("tray_enabled", True)
        self.config.setdefault("autostart", False)
        self.config.setdefault("click_through", False)
        self.config.setdefault("lock_position", False)
        self.config.setdefault("shift_drag_only", False)
        self.config.setdefault("window_opacity", 1.0)
        self.config.setdefault("hotkeys_enabled", True)
        self.config.setdefault("free_placement", True)
        vision_cfg = self.config.setdefault("vision", {})
        if not isinstance(vision_cfg, dict):
            vision_cfg = {}
            self.config["vision"] = vision_cfg
        vision_cfg.setdefault("enabled", False)
        vision_cfg.setdefault("base_url", "")
        vision_cfg.setdefault("model", "")
        vision_cfg.setdefault("timeout_s", 60)
        vision_cfg.setdefault("max_tokens", 400)
        video_cfg = self.config.setdefault("video_gen", {})
        if not isinstance(video_cfg, dict):
            video_cfg = {}
            self.config["video_gen"] = video_cfg
        video_cfg.setdefault("model", "doubao-seedance-1-5-pro-251215")
        video_cfg.setdefault("ratio", "16:9")
        video_cfg.setdefault("duration", 5)
        video_cfg.setdefault("resolution", "720p")
        video_cfg.setdefault("generate_audio", True)
        self.config.setdefault("voice_backend", "gptsovits")
        gs_cfg = self.config.setdefault("gptsovits", {})
        if not isinstance(gs_cfg, dict):
            gs_cfg = {}
            self.config["gptsovits"] = gs_cfg
        gs_cfg.setdefault("base_url", "http://127.0.0.1:9880")
        gs_cfg.setdefault("ref_audio_path", "cache/gptsovits/ref.wav")
        gs_cfg.setdefault("prompt_text", "本鲸叫小鲸鱼，最喜欢白饭和主人的摸头。")
        gs_cfg.setdefault("prompt_lang", "zh")
        gs_cfg.setdefault("text_lang", "zh")
        gs_cfg.setdefault("text_split_method", "cut5")
        self.config.setdefault("voice_auto_send", False)
        self.config.setdefault("asr_backend", "whisper")
        self.config.setdefault("barge_in_enabled", True)
        self.config.setdefault("click_jump", True)
        self.config.setdefault("double_click_chat", False)
        self.config.setdefault("visual_memory", True)
        self.config.setdefault("visual_memory_interval", 300)
        agent_cfg = self.config.setdefault("agent", {})
        if isinstance(agent_cfg, dict):
            agent_cfg.setdefault("auto_approve", False)
            agent_cfg.setdefault("auto_approve_own_home", True)  # 只自动放行 pet/home
        wake_cfg = self.config.setdefault("wake", {})
        if not isinstance(wake_cfg, dict):
            wake_cfg = {}
            self.config["wake"] = wake_cfg
        wake_cfg.setdefault("enabled", False)
        wake_cfg.setdefault("phrases", ["大肥鱼", "肥鱼", "小肥鱼"])
        wake_cfg.setdefault("language", "zh-CN")
        server_cfg = self.config.setdefault("server", {})
        if not isinstance(server_cfg, dict):
            server_cfg = {}
            self.config["server"] = server_cfg
        server_cfg.setdefault("enabled", True)
        server_cfg.setdefault("port", 8765)
        server_cfg.setdefault("token", "")
        qq_cfg = self.config.setdefault("qq", {})
        if not isinstance(qq_cfg, dict):
            qq_cfg = {}
            self.config["qq"] = qq_cfg
        qq_cfg.setdefault("enabled", False)
        qq_cfg.setdefault("base_url", "http://127.0.0.1:3000")
        qq_cfg.setdefault("token", "")
        qq_cfg.setdefault("private_user", "")
        qq_cfg.setdefault("group_id", "")
        qq_cfg.setdefault("whitelist", [])
        qq_cfg.setdefault("poll_seconds", 4)
        wechat_cfg = self.config.setdefault("wechat", {})
        if not isinstance(wechat_cfg, dict):
            wechat_cfg = {}
            self.config["wechat"] = wechat_cfg
        wechat_cfg.setdefault("enabled", False)
        wechat_cfg.setdefault("port", 8800)
        wechat_cfg.setdefault("token", "")
        proactive = self.config.get("proactive")
        if isinstance(proactive, dict):
            proactive.setdefault("quiet_hours", [1, 8])
        if not self.config.get("character"):
            default_char = "codex-whale" if (ASSETS / "characters" / "codex-whale" / "manifest.json").exists() else "dfy"
            self.config["character"] = default_char
        self.character = str(self.config.get("character") or "dfy")

        from brain import Brain
        from asr import ASR
        from voice import VoiceEngine

        self.root = tk.Tk()
        self.root.title(f"{self.display_name} · 大肥鱼桌宠")
        self.root.overrideredirect(True)
        self.root.configure(bg=KEY_COLOR)
        try:
            self.root.attributes("-topmost", bool(self.config.get("always_on_top", True)))
        except Exception:
            pass
        try:
            self.root.attributes("-transparentcolor", KEY_COLOR)
        except Exception as exc:
            log(f"透明色键不可用：{exc}")

        # 启动画面：先把主窗口藏起来，加载完再从屏幕底下游上来（隐藏模式不播）
        self.splash = None
        if not hidden:
            self.root.withdraw()
            try:
                self.splash = Splash(self.root, self.display_name, self._splash_avatar())
                self.splash.stage("加载形象……")
            except Exception as exc:
                log(f"启动画面失败（不影响启动）：{exc}")
                self.splash = None

        self.library = AnimationLibrary(ASSETS, character=self.character)
        source_width, source_height = self.library.first_size()
        self.pet_w = DFY_TARGET_WIDTH if self.library.is_dfy else LEGACY_TARGET_WIDTH
        bubble_zone = DFY_BUBBLE_ZONE if self.library.is_dfy else BUBBLE_ZONE
        self.pet_h = int(source_height * self.pet_w / float(source_width)) if source_width else 260
        self.W = max(300, self.pet_w + 40)
        self.H = bubble_zone + self.pet_h + 6
        self.root.geometry(f"{self.W}x{self.H}")
        if hidden:
            self.root.withdraw()

        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H, bg=KEY_COLOR,
                                highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)
        self.animator = Animator(self.root, self.canvas, self.library, self.pet_w, self.W, self.H)
        self.sfx = Sfx(BASE / "cache" / "sfx",
                       enabled=lambda: not self.config.get("mute") and self.config.get("click_sound", True),
                       style=lambda: str(self.config.get("click_sound_style") or "duck"))
        self.bubble = Bubble(self.canvas, self.W)

        if self.splash:
            self.splash.stage("唤醒大脑……")
        self.brain = Brain(BASE, self.config, log=log)
        try:
            # 记一次「自己身体的大致布局」，让模型点哪里时有自己的参考，而不是每次靠硬编码
            facts = self.brain.memory_store.facts
            if not any(str(item.get("key") or "") == "自我.身体布局" for item in facts):
                self.brain.memory_store.add_fact(BODY_MAP_HINT, kind="note", importance=2,
                                                  key="自我.身体布局")
                self.brain.memory_store.save()
        except Exception:
            pass
        # 她吃的是 token：真实用量记账，自己花的从零食额度扣（appetite.json，个人数据）
        from appetite import Appetite
        life_cfg = self.config.get("life") if isinstance(self.config.get("life"), dict) else {}
        self.appetite = Appetite(DATA_DIR / "appetite.json",
                                 daily_snack=int(life_cfg.get("daily_snack_tokens") or 4000), log=log)
        self.brain.appetite = self.appetite
        # 约定与提醒：她答应「到时候提醒你」就真的提醒（reminders.json，个人数据）
        from reminders import ReminderBook
        self.reminders = ReminderBook(DATA_DIR / "reminders.json", log=log)
        self.appetite.listeners.append(
            lambda tokens, purpose: self.root.after(0, self._ate_tokens, tokens, purpose))
        self.asr = ASR(language=self.config.get("asr_language", "zh-CN"), log=log,
                       backend=self.config.get("asr_backend", "whisper"),
                       model_dir=BASE.parent / ".tools" / "faster-whisper-small")
        self.voice = VoiceEngine(self.root, ASSETS, CACHE, self.config, log=log,
                                 on_speaking=self._on_speaking)
        self.wake_listener = None
        self.shared_server = None
        self.qq_bridge = None
        self.wechat_server = None
        self.approval_prompt = None

        # DSH 状态桥：读取插件写的状态文件（没装插件时安静地什么都不做）
        from status_client import DshStatusWatcher
        self.status = DshStatusWatcher(self._status_path(), log=log)

        # 工作总览 / Agent 层：可选模块，缺失时安静降级（不影响聊天和动画）
        self.overview = None
        self.agent = None
        try:
            from work_overview import WorkOverview
            self.overview = WorkOverview(BASE, self.config,
                                         personal=getattr(self.brain, "personal", None), log=log)
        except Exception as exc:
            log(f"工作总览模块未启用：{exc}")
        try:
            from agent import AgentRunner
            self.agent = AgentRunner(BASE, self.config,
                                     personal=getattr(self.brain, "personal", None),
                                     overview=self.overview,
                                     status_path=self._status_path(), log=log,
                                     confirm=self._confirm_tool)
            self.agent.memory = self.brain  # Agent 聊天也读记忆（之前只有人设，每次都像第一次见面）
        except Exception as exc:
            log(f"Agent 层未启用：{exc}")

        self._last_interaction = time.time()
        self._last_speech = 0.0
        self._last_proactive = time.time()
        self._proactive_count = 0
        self._proactive_day = time.strftime("%Y-%m-%d")
        self._dsh_state = "idle"
        self._dsh_last_react = 0.0
        self._speaking = False
        self._last_user_text = ""
        self.particles = Particles(self.canvas, self.W, self.H)
        self.stickers = StickerBook(ASSETS / "stickers")
        self.songbook = Songbook(ASSETS / "songs", log=log)
        self.douyin = DouyinBridge(log=log) if DouyinBridge else None
        if self.douyin is not None:
            try:
                ok, message = self.douyin.sync_persona(BASE / "persona.md")
                if ok:
                    log("抖音助手人设：" + message)
            except Exception as exc:
                log(f"抖音助手人设同步失败（不影响桌宠）：{exc}")
        self.sticker_popup = StickerPopup(self)
        self._eating = False
        self._food_frames: list = []
        self._emotion = "idle"
        self._emotion_job = None
        self._speech_settle_job = None
        self.prop_item = self.canvas.create_text(
            self.W - 14, 12, anchor="ne", text="", font=("Microsoft YaHei", 8),
            fill="#5f739c", state="hidden")

        self.chat_window: ChatWindow | None = None
        self.quick_chat: QuickChat | None = None
        self.settings_window: SettingsWindow | None = None
        self.tray_service: TrayService | None = None
        self.nest_window: NestWindow | None = None
        self.hotkey_service: HotkeyService | None = None
        self._hotkey_queue: queue.Queue[str] = queue.Queue()
        self._hidden_paused = False
        self._last_visual_memory = 0.0
        self._last_screen_observation = ""
        self._touch_zone_pending = "body"
        self._touch_point_pending = (0.5, 0.2)
        self._pat_zone = "body"
        self._pat_point = (0.5, 0.2)
        self._drag: dict | None = None
        self._flight: dict | None = None
        self._flight_after = None
        self._preset_lines = self._load_preset_lines()
        self._rice_count = int(self.config.get("rice_count") or 0)
        self._busy = False
        self._last_hover = 0.0

        self._place_window()
        self.root.bind("<Enter>", self._on_hover, add="+")
        self._bind_events()
        # 住在桌面上：窗口当平台、自己过日子（生活状态存 life.json，她写的东西在 home/）
        if self.splash:
            self.splash.stage("翻翻记忆和小窝……")
        from living import Living
        self.living = Living(self, DATA_DIR / "life.json", DATA_DIR / "home", log=log)
        self.menu = self._build_menu()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        self.animator.play(self.library.best("idle"))
        if self.splash:
            self.splash.stage("马上就来……")
            self.root.after(250, self._entrance)  # 入场动画播完再打招呼
        else:
            self.root.after(700, self._startup)
        self.living.start()
        self._pet_state: dict = {}
        self._refresh_pet_state()
        self.root.after(5000, self._reminder_tick)
        self.root.after(1500, self._save_rect)
        self.root.after(1300, self._poll_dsh_status)
        self.root.after(8000, self._proactive_tick)
        self.root.after(3000, self._overview_tick)
        self.root.after(8000, self._roam_tick)
        if not hidden and self.config.get("quick_chat", False):
            self.root.after(1600, self.open_quick_chat)
        if not hidden and self.config.get("chat_open_on_start"):
            self.root.after(2000, self.open_chat)
        self.root.after(15000, self._autonomy_tick)
        self.root.after(20 * 60 * 1000, self._self_improve_tick)
        self.root.after(30 * 60 * 1000, self._home_improve_tick)
        if os.environ.get("DFY_AUTONOMY_ONCE") == "1":
            self.root.after(8000, self._autonomy_once_startup)
        self.root.after(60 * 1000, self._ambient_screen_tick)
        if not hidden and (self.config.get("wake") or {}).get("enabled"):
            self.root.after(2500, self.start_wake)
        if self.config.get("server", {}).get("enabled", True):
            self.root.after(1800, self.start_shared_brain)
        if (self.config.get("qq") or {}).get("enabled"):
            self.root.after(2600, self.start_qq)
        if (self.config.get("wechat") or {}).get("enabled"):
            self.root.after(3200, self.start_wechat)
        self.config["autostart"] = autostart_enabled()
        self.root.after(900, self._start_tray)
        self.root.after(1200, self._start_hotkeys)
        self.root.after(200, self._poll_hotkeys)
        self.root.after(1400, self._apply_saved_opacity)
        self.root.after(2600, self._cleanup_old_screens)

    # ---------- DSH 状态 / 互动时间 ----------
    def _status_path(self) -> Path:
        raw = str((self.config.get("dsh_status") or {}).get("path") or "~/.dsh/whale-bridge/status.json")
        return Path(raw).expanduser()

    def _touch(self) -> None:
        self._last_interaction = time.time()

    # ---------- 形象切换 ----------
    def available_characters(self) -> list[tuple[str, str]]:
        characters: list[tuple[str, str]] = [("dfy", "鲸鱼娘 · dsh-fy 立绘")]
        chars_dir = ASSETS / "characters"
        if chars_dir.exists():
            for pack in sorted(chars_dir.iterdir()):
                manifest = load_json(pack / "manifest.json", {})
                if not manifest:
                    continue
                source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
                name = str(manifest.get("display_name") or source.get("name") or pack.name)
                characters.append((pack.name, name))
        return characters

    def switch_character(self, character: str) -> None:
        if not character or character == self.character:
            return
        try:
            new_library = AnimationLibrary(ASSETS, character=character)
            if not new_library.states:
                self.bubble.show("这个形象包没加载出来。", 2800)
                return
            self.library = new_library
            self.animator.library = new_library
            self.animator._cache.clear()
            self.animator.stop()
            self._cancel_emotion_job()
            self._cancel_speech_settle()
            self._emotion = "idle"
            self.animator.play(self.library.best("idle"))
            self.character = character
            self.config["character"] = character
            self.save_config()
            self.bubble.show(f"换好啦：{self.library.display_name}", 3200)
            log(f"切换形象：{character}（{self.library.display_name}）")
        except Exception as exc:
            log(f"切换形象失败：{exc}")
            self.bubble.show("换形象失败了，看下日志。", 2800)

    # ---------- 个人库 / 工作总览 ----------
    def show_personal(self) -> None:
        self.bubble.show(self.brain.personal_summary(), 7000)

    def refresh_personal(self) -> None:
        summary = self.brain.refresh_personal()
        self.bubble.show(summary, 5000)
        if self.overview is not None:
            threading.Thread(target=self._overview_refresh_worker, args=(True,), daemon=True).start()

    def open_personal_dir(self) -> None:
        path = (self.config.get("personal") or {}).get("library_path") or ""
        if not path:
            self.bubble.show("还没设置个人库路径（右键 → 设置）")
            return
        try:
            os.startfile(str(Path(str(path)).expanduser()))
        except Exception as exc:
            self.bubble.show(f"打不开个人库目录：{exc}", 3200)

    def _overview_tick(self) -> None:
        cfg = self.config.get("overview") or {}
        if self.overview is not None and cfg.get("enabled", True):
            threading.Thread(target=self._overview_refresh_worker, args=(False,), daemon=True).start()
        try:
            minutes = max(1, int(cfg.get("refresh_minutes") or 15))
        except (TypeError, ValueError):
            minutes = 15
        self.root.after(minutes * 60 * 1000, self._overview_tick)

    def _overview_refresh_worker(self, force: bool) -> None:
        try:
            self.overview.refresh(force=force)
            text = self.overview.text_summary()
        except Exception as exc:
            log(f"工作总览刷新失败：{exc}")
            return
        self.root.after(0, self._overview_context_ready, text)

    def _overview_context_ready(self, text: str) -> None:
        self.brain.set_overview_context(text)
        log(f"工作总览已更新：{str(text)[:100]}")

    def open_overview(self) -> None:
        if self.overview is None:
            self.bubble.show("总览模块不可用（缺 work_overview.py）。", 3000)
            return
        self.bubble.show("我去翻翻最近做过的东西…", 3000)
        threading.Thread(target=self._overview_open_worker, daemon=True).start()

    def _overview_open_worker(self) -> None:
        try:
            self.overview.refresh(force=True)
            text = self.overview.text_summary()
            path = str(getattr(self.overview, "html_path", "") or "")
        except Exception as exc:
            log(f"生成工作总览失败：{exc}")
            self.root.after(0, lambda: self.bubble.show("总览生成失败了，看下日志。", 3200))
            return
        self.root.after(0, self._overview_open_done, text, path)

    def _overview_open_done(self, text: str, path: str) -> None:
        self.brain.set_overview_context(text)
        self.bubble.show(text or "总览已更新。", 9000)
        if path:
            try:
                os.startfile(path)
            except Exception as exc:
                log(f"打开总览 HTML 失败：{exc}")

    # ---------- Agent：帮她真的动手 ----------
    def _confirm_tool(self, tool_name, args, preview) -> bool:
        """在工作线程里等桌宠旁边的确认气泡；没有界面时一律拒绝。"""
        if self.config.get("agent", {}).get("auto_approve"):
            log(f"全权限托管：自动允许 {tool_name}｜{str(preview)[:160]}")
            return True
        result = {"ok": False}
        done = threading.Event()
        if self.hidden:
            return False

        def ask() -> None:
            try:
                prompt = ApprovalPrompt(self, tool_name, preview,
                                        lambda ok: (result.__setitem__("ok", bool(ok)), done.set()))
                self.approval_prompt = prompt
            except Exception as exc:
                log(f"确认气泡拉起失败：{exc}")
                done.set()

        try:
            self.root.after(0, ask)
        except Exception:
            return False
        finished = done.wait(timeout=180)
        self.approval_prompt = None
        return bool(finished and result["ok"])

    def live_context(self) -> str:
        """「此刻」一行：几点、她在哪、刚在干嘛、状态、主人在干嘛。"""
        weekday = "一二三四五六日"[time.localtime().tm_wday]
        parts = [f"周{weekday} {time.strftime('%H:%M')}"]
        if self.config.get("position_awareness", True):
            try:
                parts.append(self._position_summary())
            except Exception:
                pass
        living = getattr(self, "living", None)
        if living is not None:
            doing = {"stroll": "在桌面上溜达", "climb_window": "爬到窗口顶上玩", "peek_edge": "躲在屏幕边偷看",
                     "nap": "刚在打盹", "snack": "刚偷吃了零食", "dance": "刚在跳舞", "browse_file": "刚翻了主人的文件",
                     "write_diary": "刚写完日记", "call_master": "刚在喊主人"}.get(living.activity)
            if doing:
                parts.append(f"你{doing}")
            parts.append(f"你现在{living.state.summary()}")
            unsaid = living.state.pop_unsaid()  # 上一轮心里那一下，只告诉她一次
            if unsaid:
                parts.append(unsaid)
        upcoming = self.reminders.summary(2) if getattr(self, "reminders", None) else ""
        if upcoming:
            parts.append(f"你记着的提醒：{upcoming}")
        appetite = getattr(self, "appetite", None)
        if appetite is not None and appetite.hungry_for_tokens():
            parts.append("你的 token 零食额度快吃光了，有点饿")
        douyin = getattr(self, "douyin", None)
        if douyin is not None:
            try:
                report = douyin.report_summary(120)
                if report:
                    parts.append(f"抖音助手：{report}")
                watching = douyin.context_line(160)
                if watching:
                    parts.append(watching)
            except Exception:
                pass
        try:
            import desktop
            label = desktop.guess_activity(desktop.foreground_title())
            if label:
                parts.append(f"主人好像在{label}")
        except Exception:
            pass
        return "；".join(parts)[:240]

    def _position_changed(self) -> None:
        """被放到新位置后，更新她自己的位置感；不额外调模型。"""
        self._last_position_at = time.time()
        try:
            self.brain.live_context = self.live_context()
        except Exception:
            pass

    def _position_summary(self) -> str:
        """她自己知道此刻在屏幕的哪里，而不是只有 x/y。"""
        x = int(self.root.winfo_x())
        y = int(self.root.winfo_y())
        sw = max(1, int(self.root.winfo_screenwidth()))
        sh = max(1, int(self.root.winfo_screenheight()))
        cx = x + self.W // 2
        cy = y + self.H // 2
        px, py = cx / sw, cy / sh
        col = "左" if px < 0.34 else ("右" if px > 0.66 else "中")
        row = "上" if py < 0.34 else ("下" if py > 0.66 else "中")
        edge = min(x, y, sw - (x + self.W), sh - (y + self.H))
        near = "贴着屏幕边" if edge < 80 else "离屏幕边还有距离"
        return f"你现在在屏幕{col}{row}部（横向{px:.0%}、纵向{py:.0%}，{near}）"

    def _reminder_note(self, text: str) -> str:
        """主人让她提醒：先在本地把提醒排好，再告诉她结果（她就不会空口答应做不到的事）。"""
        from reminders import ASK_RE
        if not ASK_RE.search(str(text or "")):
            return ""
        added = self.reminders.add_from_text(text)
        if added is None:
            return "主人想让你提醒他，但没说清什么时候，问他具体时间（比如明天几点）"
        if not added:
            return "主人说的那个时间已经过了，跟他确认一下"
        # 每个时间点写清是干什么的：只给一串时间，她会把「事后追问」当成事情本身的时间（实测过）
        plan = "；".join(self.reminders.describe(i) for i in added)
        log(f"记下提醒：{added[0]['event']} → {plan}")
        return f"你刚刚记下了「{added[0]['event']}」的提醒（{plan}），到点系统会自动提醒主人，可以放心答应，简单说一下就行"

    def ask_agent(self, text: str, on_reply=None) -> None:
        if self._maybe_sing(text, on_reply):
            return
        self.brain.live_context = self.live_context()
        note = self._reminder_note(text)
        if note:
            self.brain.live_context += "；" + note
        if getattr(self, "agent", None) is None:
            self.ask(text, on_reply)
            return
        self._busy = True
        self._touch()
        self._last_user_text = str(text or "")
        self._cancel_emotion_job()
        self.animator.play(self._state_for("thinking"))
        threading.Thread(target=self._agent_worker, args=(text, on_reply), daemon=True).start()

    def _agent_worker(self, text: str, on_reply) -> None:
        result: dict = {}
        try:
            result = self.agent.run(text, on_step=self._on_agent_step)
        except Exception as exc:
            log(f"Agent 执行失败：{exc}")
            result = {"source": "error", "error": str(exc), "reply": "", "steps": []}
        if result.get("source") == "agent" and result.get("reply"):
            result["reply"] = self.brain.absorb_mood(str(result.get("reply") or ""))  # 拆掉〔心情〕标记
            try:
                self.brain.note_exchange(text, str(result.get("reply") or ""))  # Agent 聊天也进记忆
            except Exception as exc:
                log(f"记忆写入失败：{exc}")
            self.root.after(0, self._agent_done, result, on_reply)
            return
        log(f"Agent 回退普通聊天：{result.get('error')}")
        try:
            reply, source = self.brain.reply(text)
        except Exception as exc:
            log(f"回退聊天失败：{exc}")
            reply, source = "呜…我刚刚走神了，再说一遍嘛。", "offline-error"
        self.root.after(0, self._show_reply, reply, source, on_reply)

    def _on_agent_step(self, step) -> None:
        if not isinstance(step, dict):
            return
        tool = str(step.get("tool") or "")
        ok = bool(step.get("ok"))
        self.root.after(0, self._agent_step_ui, tool, ok)

    def _agent_step_ui(self, tool: str, ok: bool) -> None:
        if not tool:
            return
        if self.chat_window is not None and self.chat_window.winfo_exists():
            self.chat_window.status_label.configure(text=f"{'✓' if ok else '…'} 正在用：{tool}")

    def _agent_done(self, result: dict, on_reply) -> None:
        reply = str(result.get("reply") or "")
        steps = result.get("steps") or []
        self._show_reply(reply, "llm", None)
        if self.chat_window is not None and self.chat_window.winfo_exists():
            detail = "、".join(
                f"{item.get('tool')}{'✓' if item.get('ok') else '✗'}"
                for item in steps if isinstance(item, dict) and item.get("tool")
            )
            if detail:
                self.chat_window.append("sys", "工具", detail)
        if on_reply is not None:
            try:
                on_reply(reply, "agent")
            except Exception as exc:
                log(f"工具回调失败：{exc}")

    # ---------- 位置 ----------
    def _place_window(self) -> None:
        position = self.config.get("position")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        if isinstance(position, (list, tuple)) and len(position) == 2:
            x, y = int(position[0]), int(position[1])
        else:
            x = screen_w - self.W - 40
            y = screen_h - self.H - 90
        x = max(0, min(screen_w - self.W, x))
        y = max(0, min(screen_h - self.H, y))
        self._home = (x, y)  # 入场动画的落点
        self.root.geometry(f"+{x}+{y}")

    def _save_position(self) -> None:
        self.config["position"] = [self.root.winfo_x(), self.root.winfo_y()]
        self.save_config()
        self._save_rect()
        if getattr(self, "quick_chat", None) is not None:
            try:
                self.quick_chat.follow_pet()
            except Exception:
                pass

    def _save_rect(self) -> None:
        try:
            rect = {"x": self.root.winfo_x(), "y": self.root.winfo_y(),
                    "w": self.W, "h": self.H, "screen_w": self.root.winfo_screenwidth(),
                    "screen_h": self.root.winfo_screenheight()}
            save_json(BASE / ".window_rect.json", rect)
        except Exception:
            pass

    # ---------- 事件 ----------
    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        # 双击默认打开喂食面板；想要回聊天：config 里 double_click_chat: true
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_context_menu)

    def _on_double_click(self, event=None) -> None:
        if self.config.get("double_click_chat"):
            self.open_chat()
        else:
            self.open_feed_panel()

    def _on_press(self, event) -> None:
        if self._flight_after is not None:
            try:
                self.root.after_cancel(self._flight_after)
            except Exception:
                pass
            self._flight_after = None
        self._flight = None
        self.animator.press()
        self.sfx.play("press")
        self._touch_zone_pending = self._zone_at(event.x, event.y)
        self._touch_point_pending = self._point_at(event.x, event.y)
        locked = bool(self.config.get("lock_position"))
        shift_only = bool(self.config.get("shift_drag_only"))
        shift_down = bool(event.state & 0x0001)
        if locked or (shift_only and not shift_down):
            self._drag = {"locked": True, "moved": False}
            return
        self._drag = {
            "sx": event.x_root, "sy": event.y_root,
            "wx": self.root.winfo_x(), "wy": self.root.winfo_y(), "moved": False,
            "samples": [(time.monotonic(), event.x_root, event.y_root)],
        }

    def _on_motion(self, event) -> None:
        if not self._drag or self._drag.get("locked"):
            return
        self._drag["samples"].append((time.monotonic(), event.x_root, event.y_root))
        self._drag["samples"] = self._drag["samples"][-6:]
        dx = event.x_root - self._drag["sx"]
        dy = event.y_root - self._drag["sy"]
        if abs(dx) + abs(dy) > 5 and not self._drag["moved"]:
            self._drag["moved"] = True
            self.animator.release()  # 拖起来了就别一直扁着
        if self._drag["moved"]:
            self.root.geometry(f"+{self._drag['wx'] + dx}+{self._drag['wy'] + dy}")

    def _on_release(self, event) -> None:
        drag = self._drag
        self._drag = None
        if not drag:
            return
        if not drag["moved"]:
            self.animator.release()
            self.sfx.play("release")
        if drag["moved"]:
            drag["samples"].append((time.monotonic(), event.x_root, event.y_root))
            drag["samples"] = drag["samples"][-6:]
            samples = drag["samples"]
            elapsed = samples[-1][0] - samples[0][0]
            if len(samples) >= 2 and elapsed > 0:
                vx = (samples[-1][1] - samples[0][1]) / elapsed
                vy = (samples[-1][2] - samples[0][2]) / elapsed
                if math.hypot(vx, vy) >= 650:
                    self._start_flight(vx, vy)
                    return
            self._save_position()
            self._position_changed()
            if self._living_on():
                self.living.dropped()  # 放在半空会掉到下面的窗口或任务栏上
        else:
            self.pat(self._touch_zone_pending, self._touch_point_pending)

    def _point_at(self, x: float, y: float) -> tuple[float, float]:
        """画布坐标 → 立绘内的归一化坐标（x 0..1，y 0..1）。"""
        try:
            coords = self.canvas.coords(self.animator.item)
            if not coords:
                return 0.5, 0.2
            target_w = float(self.animator.target_width)
            state = self.library.best("idle")
            frames = self.library.states.get(state) or []
            if not frames:
                return 0.5, 0.2
            image = frames[0][0]
            target_h = image.height * (target_w / max(1, image.width))
            cx, cy = float(coords[0]), float(coords[1])
            nx = (float(x) - (cx - target_w / 2)) / max(1.0, target_w)
            ny = (float(y) - (cy - target_h)) / max(1.0, target_h)
            return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))
        except Exception:
            return 0.5, 0.2

    def _zone_at(self, x: float, y: float) -> str:
        """按当前立绘在画布上的位置，判断点到哪个身体区域。"""
        try:
            coords = self.canvas.coords(self.animator.item)
            if not coords:
                return "body"
            target_w = float(self.animator.target_width)
            state = self.library.best("idle")
            frames = self.library.states.get(state) or []
            if not frames:
                return "body"
            image = frames[0][0]
            target_h = image.height * (target_w / max(1, image.width))
            cx, cy = float(coords[0]), float(coords[1])
            return zone_for(x, y, cx - target_w / 2, cy - target_h, target_w, target_h)
        except Exception:
            return "body"

    def _living_on(self) -> bool:
        living = getattr(self, "living", None)
        return living is not None and living.enabled()

    def _work_area(self) -> tuple[int, int, int, int] | None:
        """桌宠当前所在那块屏幕的工作区（去掉任务栏），多显示器时不会被吸回主屏。"""
        if sys.platform != "win32":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            class Rect(ctypes.Structure):
                _fields_ = [(name, wintypes.LONG) for name in ("left", "top", "right", "bottom")]

            class MonitorInfo(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", Rect),
                            ("rcWork", Rect), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            center = wintypes.POINT(self.root.winfo_x() + self.W // 2, self.root.winfo_y() + self.H // 2)
            monitor = user32.MonitorFromPoint(center, 2)  # MONITOR_DEFAULTTONEAREST
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            if monitor and user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
                work = info.rcWork
                return work.left, work.top, work.right, work.bottom
        except Exception:
            pass
        return None

    def _start_flight(self, vx: float, vy: float) -> None:
        if self.config.get("free_placement", True):
            self.bubble.show("本鲸现在固定摆放，飞不起来。", 2200)
            return
        if self._living_on():  # 住在桌面模式：交给 living 的物理，能落在窗口顶上
            self.living.toss(vx, vy)
            self._set_emotion("surprised", duration_ms=1600)
            self.bubble.show(random.choice(DRAG_LINES), 2600)
            return
        area = self._work_area() or (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        left, top, right, bottom = area
        self._flight = {"x": float(self.root.winfo_x()), "y": float(self.root.winfo_y()),
                        "vx": vx, "vy": vy, "last": time.monotonic(),
                        "left": left, "top": top, "right": right - self.W,
                        "floor": bottom - self.H}
        self._set_emotion("surprised", duration_ms=10000)
        self.root.after(420, lambda: self._set_emotion("happy", duration_ms=10000)
                        if self._flight is not None else None)
        self.bubble.show(random.choice(DRAG_LINES), 2600)
        self._flight_tick()

    def _flight_tick(self) -> None:
        state = self._flight
        if state is None:
            return
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - state["last"]))
        state["last"] = now
        x, y, vx, vy, landed = step_flight(
            state["x"], state["y"], state["vx"], state["vy"], dt,
            state["left"], state["top"], state["right"], state["floor"],
        )
        state.update(x=x, y=y, vx=vx, vy=vy)
        self.root.geometry(f"+{round(x)}+{round(y)}")
        if landed:
            self._flight = None
            self._flight_after = None
            self._save_position()
            return
        self._flight_after = self.root.after(16, self._flight_tick)

    def _on_context_menu(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.menu.grab_release()
            except Exception:
                pass

    @property
    def display_name(self) -> str:
        return str(self.config.get("display_name") or self.config.get("pet_name") or "大肥鱼")

    def save_config(self) -> None:
        save_json(self.config_path, self.config)

    # ---------- 菜单 ----------
    def _build_menu(self) -> tk.Menu:
        menu = tk.Menu(self.root, tearoff=0, font=("Microsoft YaHei", 9))

        def sub(label: str) -> tk.Menu:
            child = tk.Menu(menu, tearoff=0, font=("Microsoft YaHei", 9))
            menu.add_cascade(label=label, menu=child)
            return child

        # 顶层只留最常用三个，其余收进分组
        menu.add_command(label="🗨️  打开聊天大窗", command=self.open_chat)
        menu.add_command(label="👀  看看屏幕", command=self.see_screen)
        menu.add_separator()

        # ---------- 聊天与语音 ----------
        chat = sub("💬  聊天与语音")
        self.quick_var = tk.BooleanVar(value=bool(self.config.get("quick_chat", False)))
        chat.add_checkbutton(label="⌨  桌面聊天面板", variable=self.quick_var,
                             command=self.toggle_quick_chat)
        self.wake_var = tk.BooleanVar(value=bool((self.config.get("wake") or {}).get("enabled")))
        chat.add_checkbutton(label="🎙  喊名字唤醒", variable=self.wake_var, command=self.toggle_wake)
        self.ambient_screen_var = tk.BooleanVar(value=bool(self.config.get("ambient_screen_enabled", True)))
        chat.add_checkbutton(label="👀  自己定期看你在干嘛", variable=self.ambient_screen_var,
                             command=self.toggle_ambient_screen)
        chat.add_separator()
        self.mute_var = tk.BooleanVar(value=bool(self.config.get("mute")))
        chat.add_checkbutton(label="🔇  静音", variable=self.mute_var, command=self.toggle_mute)
        voice_menu = tk.Menu(chat, tearoff=0, font=("Microsoft YaHei", 9))
        current_voice = str(self.config.get("voice_backend") or "edge").lower()
        if current_voice not in ("gptsovits", "gpt-sovits", "edge"):
            current_voice = "edge"
        self.voice_backend_var = tk.StringVar(value=current_voice)
        voice_menu.add_radiobutton(label="晓伊（在线系统音色）", variable=self.voice_backend_var,
                                   value="edge", command=lambda: self.set_voice_backend("edge"))
        voice_menu.add_radiobutton(label="GPT-SoVITS（本地克隆）", variable=self.voice_backend_var,
                                   value="gptsovits", command=lambda: self.set_voice_backend("gptsovits"))
        voice_menu.add_separator()
        voice_menu.add_command(label="🔊  试听现在的声音", command=self.test_voice)
        chat.add_cascade(label="🔊  说话声音", menu=voice_menu)
        asr_menu = tk.Menu(chat, tearoff=0, font=("Microsoft YaHei", 9))
        self.asr_backend_var = tk.StringVar(value=str(self.config.get("asr_backend") or "whisper"))
        asr_menu.add_radiobutton(label="本地 Whisper（推荐，离线）", variable=self.asr_backend_var,
                                 value="whisper", command=lambda: self.set_asr_backend("whisper"))
        asr_menu.add_radiobutton(label="Google 在线识别", variable=self.asr_backend_var,
                                 value="google", command=lambda: self.set_asr_backend("google"))
        chat.add_cascade(label="🎧  听写引擎", menu=asr_menu)
        chat.add_separator()
        chat.add_command(label="👀  看看屏幕（截图+窗口）", command=self.see_screen)
        chat.add_command(label="🖼  打开最近截图", command=self.open_last_screen)
        chat.add_command(label="🎞  看视频…", command=self.see_video)
        chat.add_command(label="📰  看看新闻", command=self.see_news)
        chat.add_command(label="🌐  上网查资料…", command=self.research_web)
        chat.add_command(label="🪄  自己找资料优化自己…", command=self.optimize_self)
        chat.add_command(label="🔮  视觉模型设置…", command=self.configure_vision)
        chat.add_command(label="💬  网页聊天（浏览器）", command=self.open_web_chat)

        # ---------- 小窝与记忆 ----------
        home = sub("🏠  小窝与记忆")
        home.add_command(label="🏡  打开桌面小窝", command=self.open_nest)
        home.add_command(label="🪺  让她往窝里添一件", command=self.build_nest)
        home.add_command(label="📦  往窝里放点东西…", command=self.add_nest_item)
        home.add_command(label="📂  打开小窝文件夹", command=self.open_home)
        home.add_command(label="🧠  让她整理小屋/自省", command=self.reflect_home)
        home.add_command(label="🧬  自主进化（模型/Agent/能力/外形）…", command=self.autonomy_improve)
        self.self_auto_var = tk.BooleanVar(value=bool(self.config.get("self_improve_model_enabled", True)))
        home.add_checkbutton(label="🧬  模型/能力自主进化", variable=self.self_auto_var,
                             command=self.toggle_self_autonomy)
        self.home_auto_var = tk.BooleanVar(value=bool(self.config.get("self_improve_home_enabled", True)))
        home.add_checkbutton(label="🏠  小屋自主优化", variable=self.home_auto_var,
                             command=self.toggle_home_autonomy)
        evo_menu = tk.Menu(home, tearoff=0, font=("Microsoft YaHei", 9))
        self.autonomy_model_var = tk.StringVar(
            value=str(self.config.get("autonomy_model") or "deepseek-v4-pro"))
        model_menu = tk.Menu(evo_menu, tearoff=0, font=("Microsoft YaHei", 9))
        model_menu.add_radiobutton(label="MAX / deepseek-v4-pro（最强）",
                                   variable=self.autonomy_model_var, value="deepseek-v4-pro",
                                   command=lambda: self.set_autonomy_model("deepseek-v4-pro"))
        model_menu.add_radiobutton(label="跟聊天同一个模型",
                                   variable=self.autonomy_model_var, value="",
                                   command=lambda: self.set_autonomy_model(""))
        model_menu.add_radiobutton(label="deepseek-flash（快/省）",
                                   variable=self.autonomy_model_var, value="deepseek-flash",
                                   command=lambda: self.set_autonomy_model("deepseek-flash"))
        evo_menu.add_cascade(label="🧠 模型进化用的模型", menu=model_menu)
        evo_menu.add_separator()
        evo_menu.add_command(label="⏱ 设置模型进化频率…", command=self.set_autonomy_interval)
        evo_menu.add_command(label="⏱ 设置小屋优化频率…", command=self.set_home_interval)
        evo_menu.add_command(label="🎯 设置单轮 token 上限…", command=self.set_autonomy_round_limit)
        evo_menu.add_command(label="🧱 设置单轮最大步数…", command=self.set_autonomy_steps)
        evo_menu.add_command(label="📅 设置每日 token 上限…", command=self.set_autonomy_limit)
        home.add_cascade(label="⚙️  自主进化设置", menu=evo_menu)
        home.add_separator()
        home.add_command(label="🧾  看她的记忆", command=self.show_memory)
        home.add_command(label="📝  打开相处笔记", command=self.open_learned_notes)
        home.add_command(label="🧾  打开 PARTNER.md", command=self.open_partner)
        home.add_separator()
        self.roam_var = tk.BooleanVar(value=bool(self.living.cfg.get("enabled", True)))
        home.add_checkbutton(label="🏠  住在桌面上（自己生活）", variable=self.roam_var,
                             command=self.toggle_living)
        self.autonomy_var = tk.BooleanVar(value=bool(self.config.get("autonomy_enabled", True)))
        home.add_checkbutton(label="🎲  让她自己动一动", variable=self.autonomy_var,
                             command=self.toggle_autonomy)

        # ---------- 形象与桌面 ----------
        look = sub("🎭  形象与桌面")
        look.add_command(label="🎭  2D 虚拟形象（浏览器）", command=self.open_avatar)
        look.add_command(label="🪟  2D 悬浮层（置顶）", command=self.open_avatar_overlay)
        character_menu = tk.Menu(look, tearoff=0, font=("Microsoft YaHei", 9))
        for char_id, char_name in self.available_characters():
            prefix = "✓ " if char_id == self.character else "   "
            character_menu.add_command(label=prefix + char_name,
                                       command=lambda c=char_id: self.switch_character(c))
        look.add_cascade(label="🎨  换个形象", menu=character_menu)
        self.topmost_var = tk.BooleanVar(value=bool(self.config.get("always_on_top", True)))
        look.add_checkbutton(label="📌  窗口置顶", variable=self.topmost_var,
                             command=self.toggle_topmost)
        self.position_aware_var = tk.BooleanVar(value=bool(self.config.get("position_awareness", True)))
        look.add_checkbutton(label="📍  让她知道自己在屏幕哪", variable=self.position_aware_var,
                             command=self.toggle_position_awareness)
        self.body3d_var = tk.BooleanVar(value=False)
        if self.config.get("body3d_menu"):
            look.add_checkbutton(label="🧊  3D 身体（实验）", variable=self.body3d_var,
                                 command=self.toggle_body3d)
        look.add_separator()
        look.add_command(label="⌨  全局快捷键说明", command=self.show_hotkeys)

        desktop_menu = tk.Menu(look, tearoff=0, font=("Microsoft YaHei", 9))
        self.autostart_var = tk.BooleanVar(value=autostart_enabled())
        desktop_menu.add_checkbutton(label="🚀  开机自启", variable=self.autostart_var,
                                     command=self.toggle_autostart)
        self.click_through_var = tk.BooleanVar(value=bool(self.config.get("click_through", False)))
        tray_hint = "" if tray_available() else "（需先启用托盘）"
        desktop_menu.add_checkbutton(label=f"🖱  鼠标穿透{tray_hint}", variable=self.click_through_var,
                                     command=self.toggle_click_through)
        self.lock_position_var = tk.BooleanVar(value=bool(self.config.get("lock_position", False)))
        desktop_menu.add_checkbutton(label="🔒  锁定位置", variable=self.lock_position_var,
                                     command=self.toggle_lock_position)
        self.shift_drag_var = tk.BooleanVar(value=bool(self.config.get("shift_drag_only", False)))
        desktop_menu.add_checkbutton(label="⇧  仅按住 Shift 可拖动", variable=self.shift_drag_var,
                                     command=self.toggle_shift_drag)
        opacity_menu = tk.Menu(desktop_menu, tearoff=0, font=("Microsoft YaHei", 9))
        for pct in (100, 90, 75, 60, 45):
            opacity_menu.add_command(label=f"{pct}%",
                                     command=lambda v=pct: self.set_window_opacity(v / 100))
        desktop_menu.add_cascade(label="👁  不透明度", menu=opacity_menu)
        self.free_place_var = tk.BooleanVar(value=bool(self.config.get("free_placement", True)))
        desktop_menu.add_checkbutton(label="📌  放到哪停哪（关闭重力/自动走）",
                                     variable=self.free_place_var,
                                     command=self.toggle_free_placement)
        desktop_menu.add_command(label="↩  回到默认位置", command=self.reset_position)
        if tray_available():
            desktop_menu.add_command(label="📌  托盘图标常驻", command=self.show_tray_hint)
        look.add_cascade(label="🖥  桌面集成", menu=desktop_menu)

        # ---------- 互动与音乐 ----------
        fun = sub("🍚  互动与音乐")
        fun.add_command(label="🤚  摸摸头", command=self.pat)
        fun.add_command(label="🍽  喂食面板", command=self.open_feed_panel)
        feed_menu = tk.Menu(fun, tearoff=0, font=("Microsoft YaHei", 9))
        for key, label in (("rice", "🍚 白饭"), ("fish", "🐟 小鱼干"),
                           ("cake", "🍰 蛋糕"), ("tangyuan", "🍡 团子"),
                           ("diamond", "💎 钻石"), ("carrot", "🥕 胡萝卜"),
                           ("apple", "🍎 苹果"), ("seaweed", "🌿 海苔"),
                           ("plant", "🪴 小盆栽")):
            feed_menu.add_command(label=label, command=lambda food=key: self.feed(food))
        fun.add_cascade(label="🍽  直接投喂", menu=feed_menu)
        fun.add_command(label="🖼  甩个表情包",
                        command=lambda: self.send_sticker(
                            self._emotion if self._emotion != "idle" else "happy", force=True))
        perform_menu = tk.Menu(fun, tearoff=0, font=("Microsoft YaHei", 9))
        for key, label in (("happy", "开心"), ("shy", "害羞"), ("angry", "生气"),
                           ("sad", "难过"), ("surprised", "惊讶"), ("confused", "困惑"),
                           ("sleepy", "困倦"), ("proud", "得意"), ("hungry", "馋嘴")):
            perform_menu.add_command(label=label, command=lambda e=key: self.perform(e))
        perform_menu.add_separator()
        perform_menu.add_command(label="🎲 随机一个",
                                 command=lambda: self.perform(random.choice(DFY_EMOTIONS)))
        fun.add_cascade(label="✨  让她表演", menu=perform_menu)
        fun.add_separator()
        fun.add_command(label="🎵  唱首歌", command=self.sing_song)
        fun.add_command(label="🎤  让她唱（Seed-VC）…", command=self.sing_seedvc)
        fun.add_command(label="🎶  让她自己找首歌唱", command=self.sing_seedvc_auto)
        itunes_menu = tk.Menu(fun, tearoff=0, font=("Microsoft YaHei", 9))
        itunes_menu.add_command(label="播放 / 暂停", command=lambda: self.itunes_action(itunes_play_pause))
        itunes_menu.add_command(label="上一首", command=lambda: self.itunes_action(itunes_previous))
        itunes_menu.add_command(label="下一首", command=lambda: self.itunes_action(itunes_next))
        itunes_menu.add_command(label="从音乐库第一首播放", command=lambda: self.itunes_action(itunes_play_first))
        itunes_menu.add_command(label="搜索播放…", command=self.itunes_search)
        itunes_menu.add_separator()
        itunes_menu.add_command(label="现在播放什么", command=self.itunes_show_now)
        itunes_menu.add_command(label="停止", command=lambda: self.itunes_action(itunes_stop))
        fun.add_cascade(label="🎵  iTunes", menu=itunes_menu)
        netease_menu = tk.Menu(fun, tearoff=0, font=("Microsoft YaHei", 9))
        netease_menu.add_command(label="搜索播放…", command=self.netease_play)
        netease_menu.add_command(label="停止播放", command=self.voice.stop)
        fun.add_cascade(label="☁️  网易云音乐", menu=netease_menu)

        # ---------- 连接与助手 ----------
        connect = sub("📡  连接与助手")
        douyin_menu = tk.Menu(connect, tearoff=0, font=("Microsoft YaHei", 9))
        douyin_menu.add_command(label="看今日抖音日报", command=self.show_douyin_report)
        douyin_menu.add_command(label="把小鲸鱼人设同步过去", command=self.sync_douyin_persona)
        douyin_menu.add_command(label="抖音助手状态", command=self.show_douyin_status)
        connect.add_cascade(label="📱  抖音助手", menu=douyin_menu)
        self.qq_var = tk.BooleanVar(value=bool((self.config.get("qq") or {}).get("enabled")))
        connect.add_checkbutton(label="📨  通到 QQ（OneBot）", variable=self.qq_var, command=self.toggle_qq)
        connect.add_command(label="📡  QQ 桥状态", command=self.show_qq_status)
        self.wechat_var = tk.BooleanVar(value=bool((self.config.get("wechat") or {}).get("enabled")))
        connect.add_checkbutton(label="📮  公众号接入", variable=self.wechat_var, command=self.toggle_wechat)
        connect.add_command(label="📡  公众号状态", command=self.show_wechat_status)
        connect.add_separator()
        connect.add_command(label="🌉  QQ 共用大脑接口", command=self.show_shared_brain)
        connect.add_command(label="📋  工作总览", command=self.open_overview)
        personal_menu = tk.Menu(connect, tearoff=0, font=("Microsoft YaHei", 9))
        personal_menu.add_command(label="查看接入状态", command=self.show_personal)
        personal_menu.add_command(label="刷新个人库", command=self.refresh_personal)
        personal_menu.add_command(label="打开个人库目录", command=self.open_personal_dir)
        connect.add_cascade(label="📚  个人库", menu=personal_menu)

        # ---------- 设置与系统 ----------
        system = sub("⚙️  设置与系统")
        system.add_command(label="🍚  看看饭碗（余额/今日 token）", command=self.check_balance)
        budget_menu = tk.Menu(system, tearoff=0, font=("Microsoft YaHei", 9))
        budget_menu.add_command(label="查看 token 用量/剩余", command=self.show_token_budget)
        budget_menu.add_command(label="设置今日可用 token 额度…", command=self.set_today_allowance)
        budget_menu.add_command(label="设置每日发放额度…", command=self.set_daily_snack)
        budget_menu.add_command(label="设置自主进化每日上限…", command=self.set_autonomy_limit)
        budget_menu.add_separator()
        budget_menu.add_command(label="✏️ 自定义投喂 token…", command=self.feed_custom_tokens)
        system.add_cascade(label="🍚  token 额度与用量", menu=budget_menu)
        self.dsh_var = tk.BooleanVar(value=bool((self.config.get("dsh_status") or {}).get("enabled", True)))
        system.add_checkbutton(label="🌊  跟随 DSH 工作状态", variable=self.dsh_var,
                               command=self.toggle_dsh_follow)
        self.proactive_var = tk.BooleanVar(
            value=bool((self.config.get("proactive") or {}).get("enabled", True)))
        system.add_checkbutton(label="💬  允许主动搭话", variable=self.proactive_var,
                               command=self.toggle_proactive)
        self.auto_approve_var = tk.BooleanVar(
            value=bool((self.config.get("agent") or {}).get("auto_approve")))
        system.add_checkbutton(label="🔓  全权限托管（危险）", variable=self.auto_approve_var,
                               command=self.toggle_auto_approve)
        system.add_separator()
        system.add_command(label="⚙  设置…", command=self.open_settings)
        system.add_command(label="❌  退出", command=self.quit)
        return menu

    def toggle_mute(self) -> None:
        self.config["mute"] = bool(self.mute_var.get())
        self.save_config()
        self.bubble.show("静音啦。" if self.config["mute"] else "声音回来啦。", 1600)

    def set_voice_backend(self, backend: str) -> None:
        backend = "gptsovits" if str(backend).lower() in ("gptsovits", "gpt-sovits") else "edge"
        self.config["voice_backend"] = backend
        self.save_config()
        try:
            self.voice_backend_var.set(backend)
        except Exception:
            pass
        if backend == "gptsovits":
            self.bubble.show("声音切到 GPT-SoVITS；服务器没开时会自动回退晓伊。", 3800)
        else:
            self.bubble.show("声音切回晓伊（在线系统音色）。", 3000)

    def test_voice(self) -> None:
        try:
            self.voice.speak("本鲸现在用这个声音说话，你听听喜不喜欢。")
            self.bubble.show("试听中……", 1800)
        except Exception as exc:
            self.bubble.show("试听失败：" + str(exc)[:220], 4200)

    def set_asr_backend(self, backend: str) -> None:
        backend = "google" if str(backend).lower() == "google" else "whisper"
        self.config["asr_backend"] = backend
        try:
            self.asr.backend = backend
        except Exception:
            pass
        self.save_config()
        try:
            self.asr_backend_var.set(backend)
        except Exception:
            pass
        self.bubble.show("听写切到：" + ("本地 Whisper" if backend == "whisper" else "Google 在线"), 2600)

    def toggle_voice_backend(self) -> None:
        use_new = bool(self.gptsovits_var.get())
        self.config["voice_backend"] = "gptsovits" if use_new else "edge"
        self.save_config()
        self.bubble.show("以后用她的 GPT-SoVITS 声音说话。" if use_new
                         else "先用回晓伊声音（GPT-SoVITS 服务器可留着）。", 3600)

    def toggle_position_awareness(self) -> None:
        enabled = bool(self.position_aware_var.get())
        self.config["position_awareness"] = enabled
        self.save_config()
        self.bubble.show("好，我会记得自己在屏幕的哪边。" if enabled
                         else "好，先不记位置了。", 3000)

    def toggle_topmost(self) -> None:
        value = bool(self.topmost_var.get())
        self.config["always_on_top"] = value
        self.save_config()
        try:
            self.root.attributes("-topmost", value)
        except Exception:
            pass

    def toggle_dsh_follow(self) -> None:
        cfg = self.config.setdefault("dsh_status", {})
        cfg["enabled"] = bool(self.dsh_var.get())
        self.save_config()
        if cfg["enabled"]:
            self.bubble.show("好，我跟着 DSH 干活的状态动啦。", 2000)
        else:
            self.bubble.show("先不跟 DSH 状态了。", 1600)

    def toggle_proactive(self) -> None:
        cfg = self.config.setdefault("proactive", {})
        cfg["enabled"] = bool(self.proactive_var.get())
        self.save_config()
        self.bubble.show("我会偶尔来找你说句话～" if cfg["enabled"] else "那我先安静待着。", 1800)

    # ---------- 情绪状态机 ----------
    def _cancel_emotion_job(self) -> None:
        if self._emotion_job is not None:
            try:
                self.root.after_cancel(self._emotion_job)
            except Exception:
                pass
            self._emotion_job = None

    def _cancel_speech_settle(self) -> None:
        if self._speech_settle_job is not None:
            try:
                self.root.after_cancel(self._speech_settle_job)
            except Exception:
                pass
            self._speech_settle_job = None

    def _state_for(self, name: str) -> str:
        if self.library.is_dfy:
            return self.library.best(name, "idle")
        mapped = LEGACY_EMOTION_STATE.get(name, name)
        return self.library.best(mapped, "idle")

    def _set_emotion(self, emotion: str, duration_ms: int | None = None,
                     spawn_particles: bool = True) -> None:
        self._emotion = emotion
        self._cancel_emotion_job()
        self.animator.play(self._state_for(emotion))
        if spawn_particles:
            self.particles.burst(emotion)
        if duration_ms:
            self._emotion_job = self.root.after(duration_ms, self._back_to_idle)

    def _back_to_idle(self) -> None:
        self._emotion_job = None
        self._emotion = "idle"
        if self._busy or self._speaking:
            return
        if self._dsh_state in ("running", "tool"):
            self.animator.play(self._state_for("thinking"))
        else:
            self.animator.play(self._state_for("idle"))

    def _settle_emotion(self) -> None:
        self._cancel_emotion_job()
        self.animator.play(self._state_for(self._emotion))
        self._emotion_job = self.root.after(2400, self._back_to_idle)

    def _schedule_speech_settle(self, delay_ms: int = 1600) -> None:
        self._cancel_speech_settle()
        self._speech_settle_job = self.root.after(delay_ms, self._speech_settle)

    def _speech_settle(self) -> None:
        self._speech_settle_job = None
        if self._speaking:
            self._schedule_speech_settle(1200)
            return
        self._settle_emotion()

    def _emotion_for_reply(self, reply: str, source: str) -> str:
        if source == "offline-error":
            return "sad"
        text = reply or ""
        if any(key in text for key in ("对不起", "抱歉", "呜", "难过", "哭")):
            return "sad"
        if any(key in text for key in ("哼", "讨厌", "生气", "不理你")):
            return "angry"
        if any(key in text for key in ("喜欢", "嘿嘿", "开心", "好呀", "谢谢", "夸")):
            return "shy"
        if any(key in text for key in ("？", "为什么", "什么", "也许", "可能", "唔")):
            return "confused"
        return "happy"

    def _show_tool_prop(self, tool: str) -> None:
        low = (tool or "").lower()
        if any(key in low for key in ("read", "cat", "view", "file", "open")):
            label = "读文件"
        elif any(key in low for key in ("search", "grep", "glob", "find")):
            label = "搜索"
        elif any(key in low for key in ("write", "edit", "patch", "apply")):
            label = "写入"
        elif any(key in low for key in ("bash", "pwsh", "shell", "command", "terminal", "python")):
            label = "终端"
        elif any(key in low for key in ("web", "fetch")):
            label = "上网"
        else:
            label = (tool or "工具")[:12]
        try:
            self.canvas.itemconfigure(self.prop_item, text="⚙ " + label, state="normal")
            self.canvas.tag_raise(self.prop_item)
        except Exception:
            pass

    def _clear_tool_prop(self) -> None:
        try:
            self.canvas.itemconfigure(self.prop_item, text="", state="hidden")
        except Exception:
            pass

    def perform(self, emotion: str) -> None:
        self._touch()
        self._cancel_speech_settle()
        self._set_emotion(emotion, duration_ms=4200)
        self.bubble.show(random.choice(EMOTION_LINES.get(emotion, ("……",))), 2600)

    # ---------- 启动：启动画面 → 从屏幕底下游上来 → 打招呼 ----------
    def _splash_avatar(self) -> Path | None:
        pack = ASSETS / "characters" / self.character / "anim"
        for candidate in (pack / "idle.webp", pack / "happy.webp", ASSETS / "anim" / "idle.webp"):
            if candidate.exists():
                return candidate
        return None

    def _entrance(self) -> None:
        splash, self.splash = self.splash, None
        if splash is not None:
            splash.close(then=self._rise_up)
        else:
            self._rise_up()

    def _rise_up(self) -> None:
        """从屏幕底下沿弧线游上来，快到位时轻轻回弹，一路冒泡泡；落定后打招呼。"""
        x, y = getattr(self, "_home", (self.root.winfo_x(), self.root.winfo_y()))
        start_y = self.root.winfo_screenheight() + 20
        self._entering = True  # 入场时人在屏幕下面：桌面生活的物理先别管她，不然会当成悬空一路掉出屏幕
        self.root.geometry(f"+{x}+{start_y}")
        self.root.deiconify()
        self.animator.play(self._state_for("happy"))
        frames = 28

        def ease_out_back(t: float) -> float:
            c1 = 1.4
            return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2

        def step(i: int) -> None:
            t = i / frames
            cur = start_y + (y - start_y) * ease_out_back(t)
            sway = math.sin(t * math.pi * 2) * 10 * (1 - t)  # 游的时候左右摆一摆
            self.root.geometry(f"+{int(x + sway)}+{int(cur)}")
            if i % 7 == 0:
                self.particles.burst("bubbles", count=3, base_y=self.H - 40)
            if i < frames:
                self.root.after(30, step, i + 1)
            else:
                self.root.geometry(f"+{x}+{y}")
                self._entering = False
                if self._living_on():
                    self.living.dropped()  # 落定后交给物理：站到脚下的窗口或任务栏上
                self._startup()

        step(1)

    def _greet_animation(self) -> None:
        if self.library.is_dfy:
            self.animator.play(self._state_for("wave"), once=True, then=self._state_for("idle"))
        else:
            self.animator.play(self.library.best("waving", "idle"), once=True,
                               then=self.library.best("idle"))

    # ---------- 动作 ----------
    def _load_preset_lines(self) -> list[tuple[str, Path]]:
        manifest = load_json(ASSETS / "manifest.json", {})
        lines: list[tuple[str, Path]] = []
        for info in (manifest.get("voices") or {}).values():
            if not isinstance(info, dict):
                continue
            text = (info.get("text") or "").strip()
            file = info.get("file") or ""
            path = ASSETS / file if file else None
            if text and path and path.exists():
                lines.append((text, path))
        return lines

    def _pick_preset(self, *keywords: str):
        candidates = [(text, path) for text, path in self._preset_lines if any(k in text for k in keywords)]
        return random.choice(candidates) if candidates else None

    def _speak_preset(self, text: str, path) -> None:
        """GPT-SoVITS 模式跳过旧晓伊预合成，用新音色重合成。"""
        if str(self.config.get("voice_backend") or "").lower() == "gptsovits":
            self.voice.speak(text)
        else:
            self.voice.speak(preset=path)

    def _react(self, keywords: tuple[str, ...], fallback: tuple[str, ...], emotion: str | None,
               event: str | None = None) -> None:
        # 人格反应：不查固定台词，让模型按她的人设/记忆/上下文即兴说一句。
        if self.config.get("personality_reactions", True) and getattr(self, "brain", None) is not None:
            base = event or (f"主人对你说/做了「{keywords[0]}」" if keywords else "主人在逗你")
            fallback_line = random.choice(fallback)
            if emotion:
                self._set_emotion(emotion, duration_ms=3200)

            def worker() -> None:
                try:
                    line = self.brain.react(base, fallback_line)
                except Exception:
                    line = fallback_line
                final_emotion = emotion or self._emotion_for_reply(line, "llm")
                self.root.after(0, self._react_done, line, final_emotion)

            threading.Thread(target=worker, daemon=True).start()
            return
        picked = self._pick_preset(*keywords)
        if picked:
            text, path = picked
            self.bubble.show(text)
            self._speak_preset(text, path)
        else:
            text = random.choice(fallback)
            self.bubble.show(text)
            self.voice.speak(text)
        self._cancel_speech_settle()
        self._set_emotion(emotion, duration_ms=3200)

    def _react_done(self, text: str, emotion: str) -> None:
        text = (text or "").strip() or "……"
        self.bubble.show(text, 4800)
        self.voice.speak(text)
        self._cancel_speech_settle()
        self._set_emotion(emotion, duration_ms=3200)

    def pat(self, zone: str = "body", point: tuple[float, float] | None = None) -> None:
        self._touch()
        self.living.event("pat")
        self._pat_zone = zone if zone in ("head", "face", "ear", "belly", "tail", "hand", "body") else "body"
        self._pat_point = point or (0.5, 0.2)
        # 连续点击合并成一次反应：声音/按压每下都有，但说话和气泡只在停手后回一次。
        self._pat_count = int(getattr(self, "_pat_count", 0)) + 1
        job = getattr(self, "_pat_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._pat_job = self.root.after(700, self._finish_pat)

    def _finish_pat(self) -> None:
        count = max(1, int(getattr(self, "_pat_count", 1)))
        point = getattr(self, "_pat_point", (0.5, 0.2))
        self._pat_job = None
        self._pat_count = 0
        # 不告诉模型“这是头/尾巴”，只给坐标；让模型自己理解身体部位并决定反应
        self._react(("摸摸", "碰碰", "贴贴", "抱抱"),
                    ("唔……", "……"), None,
                    event=touch_point_event(point[0], point[1], count))
        if self.config.get("click_jump", True):
            try:
                self.animator.play(self.library.best("jump", "happy", "idle"), once=True,
                                   then=self.library.best("idle"))
            except Exception:
                pass

    def _on_hover(self, event=None) -> None:
        try:
            now = time.time()
            if now - getattr(self, "_last_hover", 0.0) < 90:
                return
            if self._busy or self._speaking or self._emotion_job is not None:
                return
            self._last_hover = now
            if random.random() < 0.4:
                self.bubble.show(random.choice(HOVER_LINES), 3600)
                self._set_emotion(random.choice(("shy", "happy", "surprised")), duration_ms=2600)
        except Exception:
            pass

    def open_feed_panel(self) -> None:
        """她身边的喂食面板：双击或右键菜单打开。"""
        panel = getattr(self, "feed_panel", None)
        if panel is not None and panel.winfo_exists():
            panel.lift()
            return
        win = tk.Toplevel(self.root)
        self.feed_panel = win
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.configure(bg="#eef4ff")
        px, py = self.root.winfo_x(), self.root.winfo_y()
        win.geometry(f"440x146+{px + 40}+{max(20, py - 150)}")
        outer = tk.Frame(win, bg="#ffffff", highlightbackground="#a9c3f2", highlightthickness=2)
        outer.pack(fill="both", expand=True, padx=3, pady=3)
        tk.Label(outer, text="喂她点什么？（也可以直接给 token）", bg="#ffffff", fg="#24468f",
                 font=("Microsoft YaHei", 9, "bold")).pack(anchor="w", padx=8, pady=(5, 2))
        foods = (("rice", "🍚 白饭"), ("fish", "🐟 小鱼干"), ("cake", "🍰 蛋糕"),
                 ("tangyuan", "🍡 团子"), ("diamond", "💎 钻石"), ("carrot", "🥕 胡萝卜"),
                 ("apple", "🍎 苹果"), ("seaweed", "🌿 海苔"), ("plant", "🪴 小盆栽"))
        for start in (0, 5):
            row = tk.Frame(outer, bg="#ffffff")
            row.pack(fill="x", padx=6, pady=(0, 3))
            for key, label in foods[start:start + 5]:
                tk.Button(row, text=label, command=lambda f=key: (self.feed(f), win.destroy()),
                          bg="#f5f8ff", fg="#2a3550", relief="flat", cursor="hand2",
                          font=("Microsoft YaHei", 8), padx=4).pack(side="left", padx=2)
        tk.Button(outer, text="✏️ 自定义 token…", command=lambda: (win.destroy(), self.feed_custom_tokens()),
                  bg="#eef7e6", fg="#3f6b2a", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 8), padx=6).pack(anchor="w", padx=8)
        win.bind("<Escape>", lambda e: win.destroy())
        win.after(15000, lambda: win.destroy() if win.winfo_exists() else None)

    def feed(self, food: str = "rice") -> None:
        self._touch()
        if food not in FEED_LINES:
            food = "rice"
        counts = self.config.setdefault("feed_counts", {})
        counts[food] = int(counts.get(food, 0)) + 1
        self.living.event(f"fed:{food}")
        granted = self.appetite.feed(food)  # 投喂 = 给她一份能自己花的 token 零食额度
        self.root.after(3400, lambda: self.bubble.show(
            f"（零食额度 +{granted} token，现在有 {self.appetite.allowance}）", 2600))
        if food == "rice":
            self._rice_count += 1
            self.config["rice_count"] = self._rice_count
        self.save_config()
        lines = FEED_LINES[food]
        if food == "rice":
            self._react(("饭", "吃", "馋", "投喂"),
                        (lines[0].format(count=self._rice_count), lines[1]), "hungry")
        else:
            text = random.choice(lines)
            self.bubble.show(text)
            self.voice.speak(text)
            self._cancel_speech_settle()
            self._set_emotion("hungry", duration_ms=3200)
        self._start_eating(food)

    # ---------- 吃东西：手里拿出食物 → 一口一口咬 → 吃完开心 ----------
    def _start_eating(self, food: str) -> None:
        if self._eating:  # 嘴里还有就先吃完这口，台词照说
            return
        self._eating = True
        size = int(min(96, max(56, self.pet_w * 0.22)))
        try:
            self._food_frames = [ImageTk.PhotoImage(render_food(food, size, bites)) for bites in range(4)]
        except Exception as exc:
            log(f"食物道具画不出来：{exc}")
            self._eating = False
            return
        # 举在身体右前方、大概手的高度；先从下面冒出来
        x = self.W / 2 + self.pet_w * 0.18
        y = self.H - self.pet_h * 0.42
        item = self.canvas.create_image(x, y + 24, image=self._food_frames[0])
        self.canvas.tag_raise(item)
        self._food_keep_on_top(item)
        for step in range(1, 7):
            self.root.after(step * 30, lambda: self._food_move(item, 0, -4))
        self.root.after(650, lambda: self.animator.play(self._state_for("eat")))
        for bite, delay in enumerate((1000, 1600, 2200), start=1):
            self.root.after(delay, self._food_bite, item, bite, x, y)
        self.root.after(950, lambda: self.voice.speak(random.choice(CHOMP_SOUNDS)))  # 咀嚼声（静音时 speak 自己会跳过）
        self.root.after(2850, self._finish_eating, item, food)

    def _food_keep_on_top(self, item: int) -> None:
        """动画每帧会把角色图层提到最上面，吃东西期间不停把食物再提上来，不然会被身体盖住。"""
        if not self._eating:
            return
        try:
            if not self.canvas.type(item):
                return
            self.canvas.tag_raise(item)
        except Exception:
            return
        self.root.after(40, self._food_keep_on_top, item)

    def _food_move(self, item: int, dx: float, dy: float) -> None:
        try:
            self.canvas.move(item, dx, dy)
            self.canvas.tag_raise(item)
        except Exception:
            pass

    def _food_bite(self, item: int, bite: int, x: float, y: float) -> None:
        try:
            self.canvas.itemconfigure(item, image=self._food_frames[min(bite, 3)])
        except Exception:
            return
        for _ in range(4):  # 掉几粒碎屑
            crumb = self.canvas.create_oval(0, 0, 4, 4, fill=random.choice(("#f3e3c3", "#e9c98f", "#ffffff")),
                                            outline="")
            self.canvas.move(crumb, x + random.randint(-14, 18), y + random.randint(-6, 10))
            self._crumb_fall(crumb, random.uniform(-0.8, 0.8), 10)

    def _crumb_fall(self, crumb: int, drift: float, ttl: int) -> None:
        if ttl <= 0:
            try:
                self.canvas.delete(crumb)
            except Exception:
                pass
            return
        try:
            self.canvas.move(crumb, drift, 3)
            self.canvas.tag_raise(crumb)
        except Exception:
            return
        self.root.after(40, self._crumb_fall, crumb, drift, ttl - 1)

    def _finish_eating(self, item: int, food: str) -> None:
        try:
            self.canvas.delete(item)
        except Exception:
            pass
        self._food_frames = []
        self._eating = False
        self._set_emotion("happy", duration_ms=2600)
        if random.random() < 0.5:
            self.send_sticker("eat", force=True)

    # ---------- 表情包 ----------
    def send_sticker(self, emotion: str = "", text: str = "", tags: tuple[str, ...] = (),
                     force: bool = False) -> bool:
        """按情绪/内容挑一张表情包：桌宠旁边弹卡片，聊天窗开着就也插一张。"""
        if not len(self.stickers):
            if force:
                self.bubble.show("本鲸的表情包还没下载（tools/sync_stickers.py）", 3000)
            return False
        if not force:
            try:
                if random.random() >= float(self.config.get("sticker_chance", 0.3)):
                    return False
            except (TypeError, ValueError):
                return False
        picked = self.stickers.pick(emotion, text, tags)
        if picked is None:
            return False
        if not self.hidden:
            self.sticker_popup.show(picked["path"])
        for window in (self.chat_window, self.quick_chat):
            try:
                if window is not None and window.winfo_exists():
                    window.append_sticker(self.display_name, picked["path"])
            except Exception:
                pass
        log(f"表情包：{picked.get('alt', '')[:30]}")
        return True

    # ---------- 微信公众号 ----------
    def start_wechat(self) -> None:
        if self.wechat_server is not None and getattr(self.wechat_server, "running", False):
            return
        try:
            from wechat_official import WeChatOfficialServer
        except Exception as exc:
            log(f"公众号模块不可用：{exc}")
            return
        cfg = self.config.setdefault("wechat", {})
        if not config_token(cfg):
            import secrets
            cfg["token"] = secrets.token_hex(8)
            self.save_config()
        server = WeChatOfficialServer(self.brain, self.config, log=log)
        ok, message = server.start()
        if ok:
            self.wechat_server = server
            log(f"公众号接口：{message}")
            if not self.hidden:
                self.bubble.show("公众号接口开好了（公网转发见 公众号-配置.txt）", 4200)
        else:
            log(f"公众号启动失败：{message}")
            if not self.hidden:
                self.bubble.show(f"公众号没起来：{message}", 4200)

    def stop_wechat(self) -> None:
        if self.wechat_server is not None:
            try:
                self.wechat_server.stop()
            except Exception:
                pass
            self.wechat_server = None
            log("公众号接口已停止")

    def toggle_wechat(self) -> None:
        cfg = self.config.setdefault("wechat", {})
        enabled = bool(self.wechat_var.get())
        cfg["enabled"] = enabled
        self.save_config()
        if enabled:
            self.start_wechat()
        else:
            self.stop_wechat()
            self.bubble.show("公众号接口关啦。", 2200)

    def show_wechat_status(self) -> None:
        server = self.wechat_server
        cfg = self.config.get("wechat") or {}
        if server is None or not getattr(server, "running", False):
            self.bubble.show("公众号接口没开：右键勾「公众号接入」。", 3600)
        else:
            self.bubble.show(f"公众号在 http://127.0.0.1:{cfg.get('port')}/wechat；"
                             "公网地址和 Token 见仓库根目录的 公众号-配置.txt", 5200)

    # ---------- API 余额 ----------
    def check_balance(self) -> None:
        self.bubble.show("本鲸看看 token 碗里还剩多少…", 2500)
        threading.Thread(target=self._balance_worker, daemon=True).start()

    def show_token_budget(self) -> None:
        daily = int(self.config.get("life", {}).get("daily_snack_tokens") or self.appetite.daily_snack)
        limit = int(self.config.get("autonomy_daily_limit", 20000) or 0)
        spent = self.appetite.eaten_by("self_improve")
        cap_text = "不限" if limit <= 0 else f"{limit}"
        round_cap = int(self.config.get("self_improve_estimate_tokens", 0) or 0)
        round_text = "不限" if round_cap <= 0 else str(round_cap)
        text = (f"{self.appetite.summary()}\n"
                f"每日发放：{daily} token；自主进化今日已用：{spent} / {cap_text}；"
                f"单轮预估上限：{round_text}；频率：每 {self.config.get('self_improve_interval_min', 30)} 分钟")
        self.bubble.show(text, 8000)

    def _ask_token_int(self, title: str, prompt: str, current: int) -> int | None:
        from tkinter import simpledialog
        value = simpledialog.askinteger(title, prompt, parent=self.root,
                                        initialvalue=int(current), minvalue=0,
                                        maxvalue=10_000_000)
        return None if value is None else int(value)

    def set_today_allowance(self) -> None:
        value = self._ask_token_int("今日 token 额度", "今天她还能自己用多少 token？",
                                    self.appetite.allowance)
        if value is None:
            return
        self.appetite.set_allowance(value)
        self.bubble.show(f"今天的自主额度设为 {value} token。", 3600)

    def set_daily_snack(self) -> None:
        life = self.config.setdefault("life", {})
        if not isinstance(life, dict):
            life = self.config["life"] = {}
        value = self._ask_token_int(
            "每日发放额度", "以后每天自动补到多少 token？（下次跨天生效）",
            int(life.get("daily_snack_tokens") or self.appetite.daily_snack))
        if value is None:
            return
        life["daily_snack_tokens"] = value
        self.appetite.set_daily_snack(value)
        self.save_config()
        self.bubble.show(f"以后每天补到 {value} token。", 3800)

    def set_autonomy_limit(self) -> None:
        value = self._ask_token_int(
            "自主进化每日上限", "自主进化每天最多花多少 token？（0 = 不限）",
            int(self.config.get("autonomy_daily_limit", 0) or 0))
        if value is None:
            return
        self.config["autonomy_daily_limit"] = value
        self.save_config()
        self.bubble.show("自主进化每日上限设为 " + ("不限" if value <= 0 else f"{value} token"), 3600)

    def set_autonomy_round_limit(self) -> None:
        value = self._ask_token_int(
            "自主进化单轮上限", "一轮自主进化最多花多少 token？（0 = 不限）",
            int(self.config.get("self_improve_estimate_tokens", 0) or 0))
        if value is None:
            return
        self.config["self_improve_estimate_tokens"] = value
        self.save_config()
        self.bubble.show("单轮上限设为 " + ("不限" if value <= 0 else f"{value} token"), 3600)

    def set_autonomy_steps(self) -> None:
        value = self._ask_token_int(
            "单轮最大步数", "一轮自主进化最多用多少步？（1–80；大文件改动可以调高）",
            int(self.config.get("autonomy_max_steps", 24) or 24))
        if value is None:
            return
        value = max(1, min(80, int(value)))
        self.config["autonomy_max_steps"] = value
        self.save_config()
        self.bubble.show(f"单轮最多 {value} 步；大文件可以用 replace_in_file 局部改。", 3800)

    def set_autonomy_interval(self) -> None:
        value = self._ask_token_int("自主进化频率", "每多少分钟自己进化一次？（10–1440）",
                                    int(self.config.get("self_improve_interval_min", 30) or 30))
        if value is None:
            return
        value = max(10, min(1440, int(value)))
        self.config["self_improve_interval_min"] = value
        self.save_config()
        self.bubble.show(f"以后每 {value} 分钟自主进化一次。", 3600)

    def set_home_interval(self) -> None:
        value = self._ask_token_int("小屋优化频率", "每多少分钟整理一次小屋？（10–1440）",
                                    int(self.config.get("self_improve_home_interval_min", 60) or 60))
        if value is None:
            return
        value = max(10, min(1440, int(value)))
        self.config["self_improve_home_interval_min"] = value
        self.save_config()
        self.bubble.show(f"以后每 {value} 分钟整理一次小屋。", 3600)

    def toggle_home_autonomy(self) -> None:
        enabled = bool(self.home_auto_var.get())
        self.config["self_improve_home_enabled"] = enabled
        self.save_config()
        self.bubble.show("小屋自主优化开着。" if enabled else "小屋先不由她自己整理。", 3000)

    def set_autonomy_model(self, model: str) -> None:
        self.config["autonomy_model"] = str(model or "")
        try:
            self.autonomy_model_var.set(str(model or ""))
        except Exception:
            pass
        self.save_config()
        label = model or "跟聊天同一个模型"
        self.bubble.show(f"自主进化用：{label}", 3200)

    def feed_custom_tokens(self) -> None:
        value = self._ask_token_int("自定义投喂", "直接喂她多少 token？", 3000)
        if value is None:
            return
        if value <= 0:
            return
        granted = self.appetite.add_allowance(value)
        self.bubble.show(f"喂了 {granted} token；现在还剩 {self.appetite.allowance}。", 4200)

    def _balance_worker(self) -> None:
        try:
            from usage import query_balance
            llm = self.config.get("llm") or {}
            key, _source = self.brain._resolve_api_key()
            result = query_balance(key, str(llm.get("base_url") or "https://api.deepseek.com/v1"))
            text = result.get("text") or "余额查询没有结果。"
        except Exception as exc:
            text = f"查余额失败：{exc}"
        text = f"{str(text).replace('API 余额', '饭碗里还有')}\n{self.appetite.summary()}"
        self.root.after(0, lambda: self.bubble.show(text, 7000))
        log(text.replace("\n", " · "))

    def _ate_tokens(self, tokens: int, purpose: str) -> None:
        """吃了一口 token：饿的程度往下掉（每 1000 token 约 8 点）。"""
        living = getattr(self, "living", None)
        if living is not None:
            living.state.needs["hunger"] = max(0.0, living.state.needs["hunger"] - tokens / 1000 * 8)

    # ---------- 通到 QQ（OneBot HTTP） ----------
    def _qq_reply(self, text: str) -> str:
        note = self._reminder_note(text)  # QQ 里让她提醒也算数（到点在桌面上提醒）
        if note:
            self.brain.live_context = self.live_context() + "；" + note
        try:
            reply, source = self.brain.reply(text)
        except Exception as exc:
            log(f"QQ 聊天失败：{exc}")
            return "呜……本鲸刚刚走神了，再说一遍嘛。"
        log(f"QQ 聊天：{text[:60]} → {source}（{len(reply)} 字）")
        return reply

    def start_qq(self) -> None:
        if self.qq_bridge is not None and getattr(self.qq_bridge, "running", False):
            return
        try:
            from qq_bridge import QQBridge
        except Exception as exc:
            log(f"QQ 桥模块不可用：{exc}")
            return
        bridge = QQBridge(self.config, on_message=self._qq_reply, log=log)
        ok, message = bridge.start()
        if ok:
            self.qq_bridge = bridge
            log(f"QQ 桥：{message}")
            if not self.hidden:
                self.bubble.show(f"QQ 连上啦：{message}", 4200)
        else:
            log(f"QQ 桥启动失败：{message}")
            if not self.hidden:
                self.bubble.show(f"QQ 没连上：{message}", 4200)

    def stop_qq(self) -> None:
        if self.qq_bridge is not None:
            try:
                self.qq_bridge.stop()
            except Exception:
                pass
            self.qq_bridge = None
            log("QQ 桥已停止")

    def toggle_qq(self) -> None:
        cfg = self.config.setdefault("qq", {})
        enabled = bool(self.qq_var.get())
        cfg["enabled"] = enabled
        self.save_config()
        if enabled:
            self.bubble.show("去连 QQ 后端（NapCat / SnowLuma 的 OneBot HTTP）…", 3000)
            self.start_qq()
        else:
            self.stop_qq()
            self.bubble.show("QQ 桥关啦。", 2200)

    def show_qq_status(self) -> None:
        bridge = self.qq_bridge
        if bridge is None or not getattr(bridge, "running", False):
            self.bubble.show("QQ 桥没开：右键勾「通到 QQ」并先跑 NapCat/SnowLuma。", 4200)
        else:
            self.bubble.show(f"QQ 桥在跑：{bridge.base_url}（只聊天、不动电脑）", 3600)

    def _maybe_sing(self, text: str, on_reply=None) -> bool:
        """「唱首歌/哼一段」不走大模型，直接从歌本里挑一首放；返回是否已处理。"""
        if not re.search(r"唱歌|唱首|唱个|唱一段|来一首|哼一段|哼首|哼个", str(text or "")):
            return False
        song = self.songbook.pick(str(text or ""))
        if song is None:
            self.bubble.show("呜……歌本还是空的，等本鲸学会唱再给你听。", 3600)
            return True
        self._touch()
        self._last_user_text = str(text or "")
        reply = f"好呀，本鲸给你哼一段《{song.title}》～"
        self.bubble.show(reply, 5000)
        try:
            self.animator.play(self._state_for("wave"), once=True, then=self._state_for("idle"))
        except Exception:
            pass
        self.voice.speak(preset=song.path)
        if callable(on_reply):
            on_reply(reply, "llm")
        return True

    def sing_song(self) -> None:
        song = self.songbook.pick("")
        if song is None:
            self.bubble.show("歌本还是空的……", 3000)
            return
        self._maybe_sing("唱首歌", None)

    # ---------- 抖音助手（读写它的共享人格/日报） ----------
    def show_douyin_report(self) -> None:
        try:
            path = self.douyin.latest_report() if self.douyin else None
            if path is None:
                self.bubble.show("抖音助手还没有日报（先让它刷一会儿）。", 3400)
                return
            self.bubble.show(self.douyin.report_summary(300) or "日报是空的。", 7000)
            try:
                os.startfile(str(path))
            except Exception:
                pass
        except Exception as exc:
            self.bubble.show(f"读抖音日报失败：{exc}", 3600)

    def sync_douyin_persona(self) -> None:
        if self.douyin is None:
            self.bubble.show("没找到抖音助手桥（douyin_bridge.py）。", 3200)
            return
        ok, message = self.douyin.sync_persona(BASE / "persona.md")
        self.bubble.show(message, 6000 if ok else 4200)
        log(message)

    def show_douyin_status(self) -> None:
        if self.douyin is None:
            self.bubble.show("抖音助手桥没接上。", 3200)
            return
        pieces = [self.douyin.status_text()]
        obs = self.douyin.latest_observation() if hasattr(self.douyin, "latest_observation") else None
        if obs:
            pieces.append(f"刚看：《{obs['title'][:60]}》")
        self.bubble.show("｜".join(pieces), 7000)

    # ---------- 自主活动 / 全权限 / 记忆查看 ----------
    def toggle_living(self) -> None:
        on = bool(self.roam_var.get())
        self.living.cfg["enabled"] = on
        self.save_config()
        if on:
            self.living.dropped()
        self.bubble.show("好呀，本鲸自己在桌面上过日子啦～" if on else "那本鲸乖乖待在这儿不动。", 2600)

    # ---------- 3D 身体（body3d/，Electron）：大脑还是这里，身体换成 3D ----------
    def _refresh_pet_state(self) -> None:
        living = getattr(self, "living", None)
        self._pet_state = {
            "emotion": self._emotion or "idle",
            "speaking": bool(self._speaking),
            "bubble": getattr(self.bubble, "current_text", "") or "",
            "activity": getattr(living, "activity", "idle") if living else "idle",
            "mood": round(living.state.needs["mood"]) if living else 60,
            "feeling": living.state.top_feeling() if living else "",
            "name": self.display_name,
        }
        self.root.after(200, self._refresh_pet_state)

    def _reminder_tick(self) -> None:
        """每 20 秒看看有没有到点的提醒：气泡 + 念出来 + 挥手；聊天窗开着就也发一条。"""
        try:
            for item in self.reminders.due():
                text = item["text"]
                self.bubble.show(text, 9000)
                self.voice.speak(text)
                self._set_emotion("wave", duration_ms=3000)
                for window in (self.chat_window, self.quick_chat):
                    try:
                        if window is not None and window.winfo_exists():
                            window.append("her", self.display_name, f"⏰ {text}")
                    except Exception:
                        pass
                log(f"提醒到点：{text}")
        except Exception as exc:
            log(f"提醒检查失败：{exc}")
        self.root.after(20000, self._reminder_tick)

    def _summoned(self) -> None:
        """又双击了一次桌面图标：她跳出来挥挥手（3D 模式下 2D 不出来，3D 会显示气泡）。"""
        if not self.body3d_var.get():
            self.root.deiconify()
            self.root.lift()
        self._set_emotion("wave", duration_ms=2500)
        self.bubble.show(random.choice(("我在这儿呢～", "叫本鲸？已经在啦。", "嘿嘿，没走开哦。")), 3200)

    def _state_for_body(self) -> dict:
        """3D 身体每 200ms 来取一次状态（HTTP 线程）：顺手记下它还活着，2D 那边据此决定收起还是找回。"""
        self._body3d_seen = time.time()
        return self._pet_state

    def _pet_event(self, kind: str) -> bool:
        """3D 身体上的点击（在 HTTP 线程里调用，切回主线程执行）。"""
        actions = {"pat": self.pat, "open_chat": self.open_chat, "summon": self._summoned,
                   "back_2d": lambda: (self.body3d_var.set(False), self.toggle_body3d())}  # 3D 窗口右键「回到 2D」
        if kind.startswith("feed:"):
            food = kind.split(":", 1)[1]
            actions[kind] = lambda: self.feed(food)
        action = actions.get(kind)
        if action is None:
            return False
        self.root.after(0, action)
        return True

    def toggle_body3d(self) -> None:
        if self.body3d_var.get():
            self._start_body3d()
        else:
            self._stop_body3d("3D 身体已关闭，回到 2D")

    def _start_body3d(self, no_sandbox: bool = False) -> None:
        """启动 3D 身体。等它真的连上大脑（开始取状态）才收起 2D；起不来/中途退出就自动回 2D。

        不再用 cmd /c start.bat：路径里有中文时 cmd 会把路径转坏、找不到文件，之前因此 2D 收了、3D 没出来，桌面上什么都没有。
        """
        import shutil
        import subprocess
        body = BASE.parent / "body3d"
        tools = BASE.parent / ".tools"
        cli = body / "node_modules" / "electron" / "cli.js"
        node = tools / "node" / "node.exe"
        node_cmd = str(node) if node.exists() else shutil.which("node")
        if not cli.exists() or not node_cmd:
            self.body3d_var.set(False)
            self.bubble.show("3D 身体还没装好（body3d/ 里要先 npm ci）", 3600)
            return
        if self.shared_server is None:
            self.start_shared_brain()  # 3D 身体靠共用大脑接口拿状态
        env = dict(os.environ)
        if (tools / "tmp").exists() or tools.exists():
            (tools / "tmp").mkdir(parents=True, exist_ok=True)
            env.update(TEMP=str(tools / "tmp"), TMP=str(tools / "tmp"))
        try:
            extra = os.environ.get("DFY_BODY3D_ARGS", "").split()  # 仅测试用
            if no_sandbox and "--no-sandbox" not in extra:
                extra.append("--no-sandbox")
            self._body3d_no_sandbox = no_sandbox
            self._body3d_proc = subprocess.Popen([node_cmd, str(cli), ".", *extra], cwd=str(body), env=env,
                                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self.body3d_var.set(False)
            self.bubble.show(f"3D 身体启动失败：{exc}", 4000)
            return
        self._body3d_started = time.time()
        self._body3d_seen = 0.0
        self.bubble.show("本鲸换个 3D 身体，等一下下……", 4000)
        log("3D 身体启动中（等它连上大脑再收起 2D）")
        self.root.after(1000, self._watch_body3d)

    def _watch_body3d(self) -> None:
        """每秒看一眼 3D 身体：连上了就收起 2D；没起来、退出了或卡住了就回 2D，绝不让桌面上空着。"""
        proc = getattr(self, "_body3d_proc", None)
        if proc is None:
            return
        now = time.time()
        seen = getattr(self, "_body3d_seen", 0.0)
        if proc.poll() is not None:
            code = proc.returncode & 0xFFFFFFFF
            if code == 0x80000003 and not getattr(self, "_body3d_no_sandbox", False):
                # Chromium 沙箱起不来（桌宠被受限环境启动时会这样，实测退出码 0x80000003）：关掉沙箱重试一次。
                # 3D 窗口只加载本机文件、CSP 只许本机资源，不开网页，关沙箱风险很低。
                log("3D 身体：Chromium 沙箱起不来，关掉沙箱重试")
                self._body3d_proc = None
                self._start_body3d(no_sandbox=True)
                return
            self._stop_body3d(f"3D 身体退出了（代码 {proc.returncode}），先回 2D")
            return
        if not seen and now - self._body3d_started > 15:
            self._stop_body3d("3D 身体 15 秒都没起来，先回 2D")
            return
        if seen and now - seen > 5:
            self._stop_body3d("3D 身体好像卡住了，先回 2D")
            return
        if seen and self.root.state() != "withdrawn":
            self.root.withdraw()
            log("3D 身体已连上，2D 窗口收起")
        self.root.after(1000, self._watch_body3d)

    def _stop_body3d(self, reason: str) -> None:
        import subprocess
        proc = getattr(self, "_body3d_proc", None)
        self._body3d_proc = None
        if proc is not None and proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        self.body3d_var.set(False)
        if self.root.state() == "withdrawn" and not self.hidden:
            self.root.deiconify()
        log(reason)
        if "关闭" not in reason:
            self.bubble.show(reason, 4000)

    def open_home(self) -> None:
        home = DATA_DIR / "home"
        home.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(home))
        except Exception as exc:
            self.bubble.show(f"小窝打不开：{exc}", 3000)

    def _roam_tick(self) -> None:
        try:
            if (self.config.get("roam_enabled") and not self._living_on() and not self._busy and not self._drag
                    and not self._speaking and self._dsh_state in ("idle", "disconnected")
                    and time.time() - self._last_interaction > 45 and random.random() < 0.5):
                screen_w = self.root.winfo_screenwidth()
                screen_h = self.root.winfo_screenheight()
                x = self.root.winfo_x() + random.randint(-140, 140)
                y = self.root.winfo_y() + random.randint(-70, 70)
                x = max(0, min(screen_w - self.W, x))
                y = max(0, min(screen_h - self.H - 60, y))
                self.root.geometry(f"+{x}+{y}")
                self.animator.play(self.library.best("walk") if "walk" in self.library.states
                                   else self.library.best("idle"), once=False,
                                   then=self.library.best("idle"))
        except Exception as exc:
            log(f"自由活动失败：{exc}")
        self.root.after(9000, self._roam_tick)

    def toggle_auto_approve(self) -> None:
        cfg = self.config.setdefault("agent", {})
        enabled = bool(self.auto_approve_var.get())
        cfg["auto_approve"] = enabled
        self.save_config()
        if enabled:
            self.bubble.show("⚠ 全权限托管开了：她以后动电脑不再问你。"
                             "本鲸会记到日志里，想收回随时关掉。", 7000)
        else:
            self.bubble.show("全权限托管关了，动手前会再问你。", 3000)

    def show_memory(self) -> None:
        """记忆窗口：记得的事（可删/调重要度）、日记、心情、最近一次召回、饭量、提醒。"""
        window = getattr(self, "memory_window", None)
        if window is not None and window.winfo_exists():
            window.lift()
            return
        self.memory_window = MemoryWindow(self)

    # ---------- 自主小动作 / 相处笔记 / 喊名字唤醒 ----------
    def _autonomy_tick(self) -> None:
        try:
            cfg_enabled = bool(self.config.get("autonomy_enabled", True))
            quiet = (not self._busy and not self._speaking and self._emotion_job is None
                     and self._dsh_state in ("idle", "disconnected"))
            if (cfg_enabled and quiet and not self._living_on() and time.time() - self._last_interaction > 120
                    and random.random() < 0.35):
                action = random.choice(("wave", "happy", "dance", "sleepy", "walk", "jump"))
                state = self._state_for(action)
                if state != self.library.best("idle"):
                    self.animator.play(state, once=action in ("wave", "jump"),
                                       then=self.library.best("idle"))
                    if random.random() < 0.35:
                        self.bubble.show(random.choice(AUTONOMY_LINES), 4000)
                    log(f"自主小动作：{action}")
        except Exception as exc:
            log(f"自主小动作失败：{exc}")
        self.root.after(15000, self._autonomy_tick)

    def _autonomy_once_startup(self) -> None:
        log("DFY_AUTONOMY_ONCE：启动一次自主进化")
        topic = str(os.environ.get("DFY_AUTONOMY_TOPIC") or "").strip() or None
        if topic:
            log(f"DFY_AUTONOMY_TOPIC：{topic}")
        threading.Thread(target=self._autonomy_self_improve_worker,
                         args=(topic,), daemon=True).start()

    def _self_improve_tick(self) -> None:
        """模型/能力自主进化：单独计时，不和小屋整理混在一起。"""
        if self.config.get("self_improve_enabled", True) and self.config.get("self_improve_model_enabled", True):
            threading.Thread(target=self._autonomy_self_improve_worker, daemon=True).start()
        minutes = max(10, int(self.config.get("self_improve_interval_min", 30) or 30))
        self.root.after(minutes * 60 * 1000, self._self_improve_tick)

    def _home_improve_tick(self) -> None:
        """小屋自主优化：只整理小屋/相处笔记，不碰代码和模型。"""
        if self.config.get("self_improve_enabled", True) and self.config.get("self_improve_home_enabled", True):
            threading.Thread(target=self._self_improve_worker, daemon=True).start()
        minutes = max(10, int(self.config.get("self_improve_home_interval_min", 60) or 60))
        self.root.after(minutes * 60 * 1000, self._home_improve_tick)

    def _autonomy_self_improve_worker(self, topic: str | None = None) -> None:
        """让她自己上网查、改自己的代码/2D/3D/声音/小屋，并用测试保护。"""
        if self.agent is None:
            return
        if getattr(self, "_autonomy_busy", False):
            return
        topics = {
            "Agent 能力/工具/权限": "只做一个很小的能力改进：给 pet/agent.py 的 replace_in_file 补一个 unittest（写到 pet/tests/test_agent.py），然后跑 pytest。只读这两个文件，20 步内完成。",
            "语音模型/声音/听写": "检查 pet/asr.py 的本地 Whisper 路径/失败回退，补一个可验证的小测试或修一个真实小问题；只读相关文件，20 步内完成。",
            "外形/2D/3D/动作": "只调整一个很小的外形/动作表现（例如某个动画时长或状态映射），跑相关测试；不要重写素材，20 步内完成。",
            "大脑/记忆/人格": "只改进一个很小的记忆/说话行为，并补一个测试；只读相关文件，20 步内完成。",
            "启动/性能/桌面体验": "只修一个可复现的小启动/性能/菜单问题，跑测试；只读相关文件，20 步内完成。",
            "屏幕位置感/活人感": "只改进一处位置感或活人感行为：优先修改 _position_summary/live_context 附近，并补一个可验证测试；只读相关文件，20 步内完成。",
        }
        chosen = topic or random.choice(list(topics))
        goal = topics.get(chosen, topics["Agent 能力/工具/权限"])
        estimate = int(self.config.get("self_improve_estimate_tokens", 5000) or 5000)
        if hasattr(self, "appetite") and not self.appetite.can_afford("self_improve", estimate):
            self.root.after(0, lambda: self.bubble.show(
                "本鲸想自己优化，但今天的零食 token 不够了；喂一口再继续。", 4800))
            return
        limit = int(self.config.get("autonomy_daily_limit", 0) or 0)
        spent = self.appetite.eaten_by("self_improve") if hasattr(self, "appetite") else 0
        if limit > 0 and (spent >= limit or (estimate > 0 and spent + estimate > limit)):
            self.root.after(0, lambda: self.bubble.show(
                f"自主进化今天已经用了 {spent} token，达到/接近上限 {limit}；明天再继续。", 5200))
            return
        self._autonomy_busy = True
        if self.config.get("autonomy_use_subprocess", True):
            self._launch_autonomy_subprocess(chosen)
            return
        try:
            prompt = (
                "你是小鲸鱼，主人已明确给你很高的自主权限：你可以优化你自己的模型、Agent、功能、能力、外形、"
                "2D/3D 身体、语音/听写/视觉、记忆、人格、桌面程序和启动方式。这不是只写小屋笔记；"
                "目标由你根据当前代码和实际体验自己判断，不限于下面这一条。\n"
                "你可以使用 web_search / web_fetch 查公开资料；可以 list_dir/read_file/search_files/write_file "
                "修改本项目自己的代码（pet/*.py，包括 app/brain/agent/api_server/voice/asr）、body3d、"
                "pet/assets、tools、plugin；可以新建模块/工具/测试；可以用 run_powershell 在项目内备份、"
                "安装 Python 包、下载模型/素材、跑测试。也可以调整 config.json 中非敏感的能力/模型/声音/外形选项，"
                "但绝不能读或写 API Key、token、密钥字段。\n"
                f"这一轮先选一个具体、能验证的目标：{goal}\n"
                "流程：\n"
                "1) 先读 pet/home/SELF_DIRECTION.md 和最多两个目标文件；不要读整个项目，不要先读 AGENTS/STATUS 全文。\n"
                "2) 改之前把要改的文件备份到 pet/home/backups/ 下一个带时间戳的目录；一轮只做一个可验证的改动。\n"
                "3) 第 8 步前必须用 replace_in_file 写一次；第 20 步前必须跑相关测试；不要连续只读。\n"
                "4) 如果失败，用备份恢复并如实报告；不要改测试来假装通过。\n"
                "5) 绝对不要读/改 个人配置、密钥文件、其他个人数据；不要外发、不要 git push、"
                "不要删除项目以外的东西；不要为了通过测试削弱安全/隐私边界。\n"
                "6) 本轮不要调用 get_work_overview / read_personal_library / get_dsh_status，这些又慢又和本次改动无关。\n"
                "7) 最后用中文写 5~10 行：你给自己加了什么能力、改了哪些文件、怎么验证、下一步想做什么。\n"
                "效率要求：不要一次读整个文件；先用 search_files 定位，再用 read_file 只读相关段。"
                "改代码优先用 replace_in_file；大文件（比如 app.py）只改局部，不要整文件重写。"
                "最多 24 步，本轮目标必须在 20 步内完成。"
            )
            from agent import AgentRunner
            runner = AgentRunner(BASE, self.config, personal=None, overview=None,
                                 status_path=self._status_path(), log=log,
                                 confirm=self._confirm_tool)
            runner.memory = self.brain
            result = runner.run(
                prompt, purpose="self_improve",
                model=(str(self.config.get("autonomy_model") or "").strip() or None),
                max_steps=int(self.config.get("autonomy_max_steps", 24) or 24),
                on_step=lambda step: log(f"自主进化步骤 {step.get('tool')}：{str(step.get('detail') or '')[:120]}"))
            reply = str((result or {}).get("reply") or "").strip()
            steps = (result or {}).get("steps") or []
            mutated = any(str(s.get("tool") or "") in ("write_file", "replace_in_file")
                          and s.get("ok") for s in steps)
            if reply:
                save_note(home_root(BASE), "notes", reply, title=f"自主优化：{chosen}")
            if mutated:
                msg = f"自主优化完成（{chosen}），报告写在小屋 notes 里。"
            else:
                msg = f"这轮自主优化只读没写（{chosen}），原因已记下，下一轮换小目标。"
            self.root.after(0, lambda: self.bubble.show(msg, 5000))
            log(f"自主优化 {chosen}：{len(steps)} 步；写入={mutated}；{reply[:160]}")
        except Exception as exc:
            log(f"自主优化失败：{exc}")
            self.root.after(0, lambda: self.bubble.show("这次自主优化卡住了：" + str(exc)[:220], 5200))
        finally:
            self._autonomy_busy = False

    def _launch_autonomy_subprocess(self, topic: str) -> None:
        """在独立进程跑自主进化：不阻塞桌宠，也不让长工具链卡住 GUI。"""
        import subprocess
        import sys

        script = BASE.parent / "tools" / "autonomy_runner.py"
        if not script.is_file():
            self._autonomy_busy = False
            self.bubble.show("自主进化脚本不在，暂时跑不了。", 3600)
            return
        log_dir = BASE / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        out_path = log_dir / "autonomy-runner.log"
        out = open(out_path, "a", encoding="utf-8")
        out.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {topic} =====\n")
        out.flush()
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = subprocess.Popen(
                [sys.executable, str(script), topic],
                cwd=str(BASE.parent), env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            out.close()
            self._autonomy_busy = False
            log(f"自主进化子进程启动失败：{exc}")
            self.bubble.show("自主进化进程没启动起来：" + str(exc)[:220], 4800)
            return
        out.close()
        log(f"自主进化子进程：{topic}（pid={proc.pid}，日志 {out_path.name}）")

        def wait() -> None:
            try:
                rc = proc.wait(timeout=900)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                rc = -1
            self._autonomy_busy = False
            self.root.after(0, lambda: self.bubble.show(
                f"自主进化跑完了一轮（{topic}，退出码 {rc}），详情在小屋 notes/日志。", 5200))
            log(f"自主进化子进程结束：{topic} rc={rc}")

        threading.Thread(target=wait, daemon=True).start()

    def autonomy_improve(self) -> None:
        if self.agent is None:
            self.bubble.show("Agent 还没接上，暂时不能自主改动。", 3200)
            return
        self.bubble.show("本鲸自己找资料、改自己……要跑测试，稍等。", 3000)
        threading.Thread(target=self._autonomy_self_improve_worker, daemon=True).start()

    def toggle_self_autonomy(self) -> None:
        enabled = bool(self.self_auto_var.get())
        self.config["self_improve_autonomous"] = enabled
        self.config["self_improve_model_enabled"] = enabled
        self.save_config()
        if enabled:
            self.bubble.show("好，我每 30 分钟自己查资料、改一点自己的模型/能力/外形；token 不够会停。", 4600)
            threading.Thread(target=self._autonomy_self_improve_worker, daemon=True).start()
        else:
            self.bubble.show("模型/能力先不自己改了，等我叫你。", 2800)

    def _self_improve_worker(self) -> None:
        try:
            note = self.brain.self_reflect()
        except Exception as exc:
            log(f"相处笔记整理失败：{exc}")
            return
        if note:
            log(f"相处笔记更新：{note[:120]}")
            try:
                save_note(home_root(BASE), "notes", note, title="相处笔记")
            except Exception:
                pass
            self.root.after(0, lambda: self.bubble.show("我把最近学到的记在小本本上了～", 3200))

    def open_learned_notes(self) -> None:
        path = Path(getattr(self.brain, "learned_path", ""))
        if not path.exists():
            self.bubble.show("相处笔记还是空的，多聊几次她就会自己总结啦。", 3500)
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            self.bubble.show(f"打不开相处笔记：{exc}", 3000)

    def start_wake(self) -> None:
        try:
            from wake import WakeListener
        except Exception as exc:
            self.bubble.show(f"唤醒模块不可用：{exc}", 3000)
            return
        cfg = self.config.get("wake") or {}
        if self.wake_listener is not None and self.wake_listener.running:
            return
        self.wake_listener = WakeListener(
            phrases=cfg.get("phrases") or ["大肥鱼", "肥鱼"],
            language=cfg.get("language") or "zh-CN",
            on_wake=self._on_wake,
            log=log,
        )
        ok, message = self.wake_listener.start()
        if not ok:
            self.bubble.show(f"唤醒没开成：{message}", 4000)
        else:
            self.bubble.show("好，我在听——喊「大肥鱼」就行。", 3500)

    def stop_wake(self) -> None:
        if self.wake_listener is not None:
            self.wake_listener.stop()
            self.wake_listener = None
            log("唤醒监听已停止")

    def toggle_wake(self) -> None:
        cfg = self.config.setdefault("wake", {})
        enabled = bool(self.wake_var.get())
        cfg["enabled"] = enabled
        self.save_config()
        if enabled:
            self.bubble.show("开着唤醒啦：喊「大肥鱼」我就会应。麦克风会一直听，"
                             "音频会发到在线识别；不想用随时关。", 6000)
            self.start_wake()
        else:
            self.stop_wake()
            self.bubble.show("好，不偷听啦。", 2000)

    # ---------- 共用大脑 / PARTNER.md ----------
    def start_shared_brain(self) -> None:
        if self.shared_server is not None and getattr(self.shared_server, "running", False):
            return
        try:
            from api_server import SharedBrainServer
        except Exception as exc:
            log(f"共用大脑模块不可用：{exc}")
            return
        server = SharedBrainServer(self.brain, self.config, log=log)
        server.state_provider = self._state_for_body  # 主线程每 200ms 刷新快照，这里只读引用
        server.event_handler = self._pet_event
        ok, message = server.start()
        if ok:
            self.shared_server = server
            log(f"共用大脑接口：{message}")
            if not self.hidden:
                self.bubble.show("QQ 共用大脑接口开好了（见设置/日志）", 3000)
        else:
            log(f"共用大脑启动失败：{message}")

    def stop_shared_brain(self) -> None:
        if self.shared_server is not None:
            try:
                self.shared_server.stop()
            except Exception:
                pass
            self.shared_server = None

    def show_shared_brain(self) -> None:
        server = self.shared_server
        if server is None or not getattr(server, "running", False):
            self.bubble.show("共用大脑接口没开（日志里看原因）。", 3200)
            return
        url = getattr(server, "base_url", "http://127.0.0.1:8765/v1")
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
        except Exception:
            pass
        self.bubble.show(f"接口地址已复制：{url}（model=dfy-companion）", 5000)

    def open_avatar_overlay(self) -> None:
        script = BASE.parent / "tools" / "launch_avatar_overlay.ps1"
        if not script.exists():
            self.open_avatar()
            return
        try:
            import subprocess
            subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                              "-File", str(script)], creationflags=0x08000000)
            self.bubble.show("2D 悬浮层在打开啦（Chrome 置顶小窗）", 3600)
        except Exception as exc:
            self.bubble.show(f"悬浮层没起来：{exc}", 4200)

    def open_web_chat(self) -> None:
        server = self.shared_server
        if server is None or not getattr(server, "running", False):
            self.bubble.show("网页聊天没开：共用大脑接口没起来。", 3400)
            return
        url = getattr(server, "base_url", "http://127.0.0.1:8765/v1").replace("/v1", "/chat")
        try:
            import webbrowser
            webbrowser.open(url)
            self.bubble.show(f"网页聊天打开啦：{url}", 4000)
        except Exception as exc:
            self.bubble.show(f"打不开浏览器：{exc}（地址是 {url}）", 5000)

    def open_avatar(self) -> None:
        server = self.shared_server
        if server is None or not getattr(server, "running", False):
            self.bubble.show("2D 形象页没开：共用大脑接口没起来。", 3400)
            return
        url = getattr(server, "base_url", "http://127.0.0.1:8765/v1").replace("/v1", "/avatar")
        try:
            import webbrowser
            webbrowser.open(url)
            self.bubble.show(f"2D 虚拟形象打开啦：{url}", 4200)
        except Exception as exc:
            self.bubble.show(f"打不开浏览器：{exc}（地址是 {url}）", 5000)

    def open_partner(self) -> None:
        path = Path(getattr(self.brain, "partner_path", ""))
        if not path.parent.exists():
            self.bubble.show("找不到 PARTNER.md 的目录。", 2600)
            return
        if not path.exists():
            try:
                path.write_text(PARTNER_TEMPLATE, encoding="utf-8")
            except Exception as exc:
                self.bubble.show(f"创建 PARTNER.md 失败：{exc}", 3200)
                return
        self.brain.reload_partner()
        try:
            os.startfile(str(path))
        except Exception as exc:
            self.bubble.show(f"打不开 PARTNER.md：{exc}", 2800)

    def _on_wake(self, command: str) -> None:
        log(f"被叫醒：{command or '（没听清后半句）'}")
        self._touch()
        self._cancel_emotion_job()
        self.root.after(0, self._wake_ui, command)

    def _wake_ui(self, command: str) -> None:
        try:
            self.animator.play(self._state_for("wave"), once=True, then=self._state_for("idle"))
            self.bubble.show("嗯？你叫我？", 2400)
            self.voice.speak("嗯？你叫我？")
        except Exception:
            pass
        command = (command or "").strip("，。！？,.!? ")
        if command:
            on_reply = None
            if self.quick_chat is not None and self.quick_chat.winfo_exists():
                self.quick_chat.append("你（语音）", command)
                on_reply = self.quick_chat._on_reply
            self.ask_agent(command, on_reply)
        else:
            self.root.after(300, self._listen_follow_up)

    def _listen_follow_up(self) -> None:
        if not self.asr.ok:
            return
        self.set_state("waiting")
        self.bubble.show("我在听，说吧～", 4500)

        def done(text, error):
            self.root.after(0, self._follow_up_done, text, error)

        self.asr.listen(done)

    def _follow_up_done(self, text, error) -> None:
        self.set_state("idle")
        if text:
            on_reply = None
            if self.quick_chat is not None and self.quick_chat.winfo_exists():
                self.quick_chat.append("你（语音）", text)
                on_reply = self.quick_chat._on_reply
            self.ask_agent(text, on_reply)
        elif error:
            self.bubble.show(f"没听清：{error}", 3200)

    # ---------- 语音 / 状态回调 ----------
    def _on_speaking(self, speaking: bool) -> None:
        """TTS 开始/结束：高清立绘用 talking 动画，旧素材用嘴部叠加。"""
        try:
            self._speaking = bool(speaking)
            if speaking:
                self._last_speech = time.time()
                self._cancel_speech_settle()
                self._cancel_emotion_job()
                if self.library.is_dfy:
                    self.animator.play(self._state_for("talking"))
                else:
                    self.animator.set_talking(True)
            else:
                if self.library.is_dfy:
                    self._cancel_speech_settle()
                    self._settle_emotion()
                else:
                    self.animator.set_talking(False)
                    if not self._busy and self._dsh_state not in ("running", "tool"):
                        self.animator.play(self.library.best("idle"))
        except Exception as exc:
            log(f"语音动画切换失败：{exc}")

    def set_state(self, state: str) -> None:
        try:
            if self.library.is_dfy:
                self._cancel_emotion_job()
                mapped = "thinking" if state == "waiting" else state
                if mapped == "idle":
                    self._back_to_idle()
                else:
                    self.animator.play(self._state_for(mapped))
            else:
                self.animator.play(self.library.best(state, "idle"))
        except Exception as exc:
            log(f"set_state({state}) 失败：{exc}")

    # ---------- DSH 工作状态 ----------
    def _poll_dsh_status(self) -> None:
        try:
            cfg = self.config.get("dsh_status") or {}
            if cfg.get("enabled", True):
                event = self.status.poll()
                if event:
                    self._on_dsh_event(event)
        except Exception as exc:
            log(f"轮询 DSH 状态失败：{exc}")
        delay = int((self.config.get("dsh_status") or {}).get("poll_ms") or 1000)
        self.root.after(max(300, delay), self._poll_dsh_status)

    def _on_dsh_event(self, event: dict) -> None:
        state = str(event.get("state") or "idle")
        if state in ("running", "tool", "success", "failure", "approval", "idle"):
            self._dsh_state = state
        else:
            self._dsh_state = "idle"
        log(f"DSH 状态变化：{event.get('previous')} → {state}"
            f"（done={event.get('doneCount')} err={event.get('errorCount')}）")
        reacting = state in ("success", "failure", "approval")
        if not reacting:
            if state in ("idle", "disconnected"):
                self._clear_tool_prop()
            elif state == "tool" and event.get("lastTool"):
                self._show_tool_prop(str(event.get("lastTool")))
            if not self._busy and not self._speaking:
                if state in ("running", "tool"):
                    self._cancel_emotion_job()
                    self.animator.play(self._state_for("thinking"))
                elif self._emotion_job is None:
                    self.animator.play(self._state_for("idle"))
            return
        now = time.time()
        if now - self._dsh_last_react < 5.0:
            return
        self._dsh_last_react = now
        self._clear_tool_prop()
        if state == "success":
            picked = self._pick_preset("完成", "搞定", "收工", "撒花", "夸", "耶", "大成功")
            fallback = ("搞定啦！主人的活儿漂亮收尾，快夸两句。",)
            self._emotion = "happy"
            self.animator.play(self._state_for("jump"), once=True, then=self._state_for("happy"))
            self.particles.burst("jump", count=8)
        elif state == "failure":
            picked = self._pick_preset("出错", "难过", "别急", "呜", "问题", "放弃")
            fallback = ("呜…这步出错了，先看看日志，别急。",)
            self._set_emotion("sad", duration_ms=5200)
        else:
            picked = self._pick_preset("批准")
            fallback = ("有事情等你点头呢，别让它一直等着。",)
            self._set_emotion("surprised", duration_ms=5200)
        if picked:
            text, path = picked
            self.bubble.show(text)
            self._speak_preset(text, path)
        else:
            text = self.brain.line_for_dsh_state(state) or random.choice(fallback)
            self.bubble.show(text)
            self.voice.speak(text)

    # ---------- 主动搭话 ----------
    def _proactive_tick(self) -> None:
        try:
            cfg = self.config.get("proactive") or {}
            now = time.time()
            today = time.strftime("%Y-%m-%d")
            if today != self._proactive_day:
                self._proactive_day = today
                self._proactive_count = 0
            idle_min = max(5.0, float(cfg.get("min_idle_minutes") or 25))
            interval = max(120.0, float(cfg.get("min_interval_minutes") or 25) * 60.0)
            daily_limit = max(1, int(cfg.get("daily_limit") or 8))
            quiet = cfg.get("quiet_hours") or [1, 8]
            quiet_active = False
            try:
                start, end = int(quiet[0]), int(quiet[1])
                hour = time.localtime(now).tm_hour
                if start != end:
                    quiet_active = (start <= hour < end) if start < end else (hour >= start or hour < end)
            except Exception:
                quiet_active = False
            allowed = (
                bool(cfg.get("enabled", True))
                and not quiet_active
                and not self._busy
                and not self._speaking
                and not self.voice.busy()
                and self._dsh_state not in ("running", "tool")
                and (now - self._last_interaction) >= idle_min * 60.0
                and (now - self._last_proactive) >= interval
                and self._proactive_count < daily_limit
            )
            if allowed:
                self._proactive_count += 1
                self._last_proactive = now
                context = {
                    "time_of_day": self._time_of_day(),
                    "idle_minutes": int((now - self._last_interaction) // 60),
                    "dsh_state": self._dsh_state,
                    "last_user_text": self._last_user_text,
                }
                threading.Thread(target=self._proactive_worker, args=(context,), daemon=True).start()
        except Exception as exc:
            log(f"主动搭话调度失败：{exc}")
        self.root.after(30000, self._proactive_tick)

    def _proactive_worker(self, context: dict) -> None:
        try:
            line = self.brain.proactive_line(context)
        except Exception as exc:
            log(f"主动搭话生成失败：{exc}")
            return
        if line:
            self.root.after(0, self._show_proactive, line)

    def _show_proactive(self, line: str) -> None:
        if self._busy or self._speaking:
            return
        self.bubble.show(line)
        self._cancel_speech_settle()
        self._set_emotion(random.choice(("happy", "shy", "sleepy", "hungry")), duration_ms=3600)
        self.voice.speak(line)
        self._schedule_speech_settle(2200)
        log(f"主动搭话：{line}")

    @staticmethod
    def _time_of_day() -> str:
        hour = time.localtime().tm_hour
        if 5 <= hour < 12:
            return "morning"
        if 12 <= hour < 18:
            return "afternoon"
        if 18 <= hour < 23:
            return "evening"
        return "night"

    # ---------- 大脑 ----------
    def ask(self, text: str, on_reply=None) -> None:
        if self._maybe_sing(text, on_reply):
            return
        self.brain.live_context = self.live_context()
        note = self._reminder_note(text)
        if note:
            self.brain.live_context += "；" + note
        self._busy = True
        self._touch()
        self._last_user_text = str(text or "")
        self._cancel_emotion_job()
        self.animator.play(self._state_for("thinking"))
        threading.Thread(target=self._ask_worker, args=(text, on_reply), daemon=True).start()

    def _ask_worker(self, text: str, on_reply) -> None:
        try:
            reply, source = self.brain.reply(text)
        except Exception as exc:
            log(f"回复失败：{exc}")
            reply, source = "呜…我刚刚走神了，再说一遍嘛。", "offline-error"
        self.root.after(0, self._show_reply, reply, source, on_reply)

    def _show_reply(self, reply: str, source: str, on_reply) -> None:
        self._busy = False
        self._cancel_speech_settle()
        self._cancel_emotion_job()
        emotion = self._emotion_for_reply(reply, source)
        self.living.event("chat")
        # 回复里写了（尾巴一甩）（捂脸）这类小动作，就真的做出来，而不是一律「说话」动画
        acted = state_for_actions(reply) if source != "offline-error" else None
        mood, self.brain.last_mood = getattr(self.brain, "last_mood", None), None
        if mood:  # 回复末尾的〔心情 ±N 情绪〕：记进她的心情，情绪也能决定动作
            delta, feeling = mood
            self.living.state.feel(delta, feeling, reason=self._last_user_text)
            acted = acted or MOOD_STATES.get(feeling)
        if acted:
            emotion = acted if acted in PARTICLE_SPECS else emotion
        self._emotion = emotion
        self.animator.play(self._state_for("sad" if source == "offline-error" else (acted or "talking")))
        inner_voice = None
        chance = self.config.get("inner_voice_chance", 1 / 6)
        try:
            if random.random() < max(0.0, min(1.0, float(chance))):
                inner_voice = random.choice(INNER_VOICE_LINES)
        except (TypeError, ValueError):
            pass
        self.bubble.show(reply, inner_voice=inner_voice)
        self.particles.burst(emotion, count=4)
        self.voice.speak(reply, interrupt=True)
        self._schedule_speech_settle(2400 if acted else 1600)
        if on_reply is not None:
            try:
                on_reply(reply, source)
            except Exception as exc:
                log(f"回调失败：{exc}")
        if source != "offline-error":  # 聊天窗先显示文字，再甩图
            self.root.after(600, lambda: self.send_sticker(emotion, reply))

    def toggle_autonomy(self) -> None:
        enabled = bool(self.autonomy_var.get())
        self.config["autonomy_enabled"] = enabled
        self.save_config()
        self.bubble.show("好，我自己找点事做～" if enabled else "那我乖乖待着。", 2200)

    def toggle_quick_chat(self) -> None:
        enabled = bool(self.quick_var.get())
        self.config["quick_chat"] = enabled
        self.save_config()
        if enabled:
            self.open_quick_chat()
        elif self.quick_chat is not None:
            try:
                self.quick_chat.close()
            except Exception:
                self.quick_chat = None

    # ---------- 窗口 ----------
    def open_quick_chat(self) -> None:
        if self.quick_chat is not None and self.quick_chat.winfo_exists():
            self.quick_chat.lift()
            self.quick_chat.entry.focus_set()
            return
        self.quick_chat = QuickChat(self)

    def open_chat(self) -> None:
        if self.chat_window is not None and self.chat_window.winfo_exists():
            self.chat_window.lift()
            self.chat_window.entry.focus_set()
            return
        self.chat_window = ChatWindow(self)

    def open_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self.settings_window = SettingsWindow(self)

    # ---------- 桌面原生整合（托盘 / 自启 / 穿透 / 透明度） ----------
    def _start_tray(self) -> None:
        if not self.config.get("tray_enabled", True):
            return
        self.config["autostart"] = autostart_enabled()
        self.tray_service = TrayService(self)
        if self.tray_service.start():
            log("系统托盘：已启动")
        else:
            self.tray_service = None
            log("系统托盘：未启动（pystray 不可用或托盘被占用）")

    def toggle_visible(self) -> None:
        try:
            if self.root.state() == "withdrawn":
                self.root.deiconify()
                self.root.attributes("-topmost", bool(self.config.get("always_on_top", True)))
                self._resume_after_hidden()
            else:
                self._pause_for_hidden()
                self.root.withdraw()
        except Exception:
            pass

    def _pause_for_hidden(self) -> None:
        """隐藏时停掉动画定时器，别让透明窗在后台空转。"""
        if self._hidden_paused:
            return
        self._hidden_paused = True
        try:
            self.animator.stop()
        except Exception:
            pass

    def _resume_after_hidden(self) -> None:
        if not self._hidden_paused:
            return
        self._hidden_paused = False
        try:
            self.animator.play(self.library.best("idle"))
        except Exception:
            pass

    def _start_hotkeys(self) -> None:
        if not self.config.get("hotkeys_enabled", True):
            return
        self.hotkey_service = HotkeyService(self._hotkey_queue.put, log=log)
        if self.hotkey_service.start():
            log("全局快捷键：Ctrl+Alt+M 显示/隐藏 / J 聊天 / P 鼠标穿透")
        else:
            self.hotkey_service = None
            log("全局快捷键：注册失败（可能被其他程序占用）")

    def _poll_hotkeys(self) -> None:
        try:
            while True:
                action = self._hotkey_queue.get_nowait()
                self._handle_hotkey(action)
        except queue.Empty:
            pass
        try:
            self.root.after(160, self._poll_hotkeys)
        except Exception:
            pass

    def _handle_hotkey(self, action: str) -> None:
        if action == "show_hide":
            self.toggle_visible()
        elif action == "chat":
            self.root.deiconify()
            self._resume_after_hidden()
            self.open_chat()
        elif action == "passthrough":
            self.set_click_through(not self.config.get("click_through", False))

    def show_hotkeys(self) -> None:
        self.bubble.show("全局快捷键：" + "；".join(hotkey_labels()), 6200)

    def show_tray_hint(self) -> None:
        self.bubble.show("托盘图标在任务栏右下角；右键可显示/隐藏、关鼠标穿透、退出。", 4800)

    def toggle_click_through(self) -> None:
        self.set_click_through(bool(self.click_through_var.get()))

    def set_click_through(self, enabled: bool) -> None:
        if enabled and not (self.tray_service and self.tray_service.icon):
            self.config["click_through"] = False
            try:
                self.click_through_var.set(False)
            except Exception:
                pass
            self.bubble.show("托盘没起来，鼠标穿透会点不回来，先不开了。", 4200)
            return
        ok = click_through(self.root, enabled)
        self.config["click_through"] = bool(enabled and ok)
        try:
            self.click_through_var.set(self.config["click_through"])
        except Exception:
            pass
        self.save_config()
        if ok:
            self.bubble.show("鼠标穿透开了；从托盘菜单可以关。" if enabled else "鼠标穿透关了。", 3600)
        else:
            self.bubble.show("鼠标穿透没设置成功（仅 Windows 支持）。", 3600)

    def toggle_autostart(self) -> None:
        want = bool(self.autostart_var.get())
        ok = set_autostart(want, BASE / "app.py")
        self.config["autostart"] = bool(ok and want)
        self.autostart_var.set(self.config["autostart"])
        self.save_config()
        self.bubble.show("开机自启开了。" if self.config["autostart"] else "开机自启关了。", 3200)

    def toggle_lock_position(self) -> None:
        self.config["lock_position"] = bool(self.lock_position_var.get())
        self.save_config()
        self.bubble.show("位置锁住了，点她还是有反应。" if self.config["lock_position"] else "位置解锁了。", 3200)

    def toggle_shift_drag(self) -> None:
        self.config["shift_drag_only"] = bool(self.shift_drag_var.get())
        self.save_config()
        self.bubble.show("现在要按住 Shift 才拖得动。" if self.config["shift_drag_only"] else "现在可以直接拖了。", 3400)

    def toggle_free_placement(self) -> None:
        self.config["free_placement"] = bool(self.free_place_var.get())
        self.save_config()
        if self.config["free_placement"]:
            self.bubble.show("自由摆放：放到哪停哪，不会自己掉下去，也不会自己走。", 4200)
        else:
            self.bubble.show("恢复桌面生活：她会自己走、从半空掉到窗口/任务栏上。", 4200)
            if self._living_on():
                self.living.dropped()

    def _cleanup_old_screens(self) -> None:
        try:
            removed = cleanup_screens(DATA_DIR / "cache" / "screens", keep=4, max_age_hours=24)
            if removed:
                log(f"截图清理：删了 {removed} 张旧图")
        except Exception:
            pass

    def configure_vision(self) -> None:
        """视觉模型设置：端点/模型写 config，Key 写 .secret_vision_key。"""
        win = tk.Toplevel(self.root)
        win.title("小鲸鱼 · 视觉模型设置")
        win.geometry("520x250")
        win.attributes("-topmost", True)
        cfg = self.config.setdefault("vision", {})
        tk.Label(win, text="OpenAI 兼容视觉模型（留空则只用前台窗口文字，不发送截图）",
                 font=("Microsoft YaHei", 10), anchor="w").pack(fill="x", padx=16, pady=(14, 6))
        form = tk.Frame(win)
        form.pack(fill="x", padx=16)
        fields = {}
        for row, (key, label, value) in enumerate((
                ("base_url", "接口地址", cfg.get("base_url", "")),
                ("model", "模型名", cfg.get("model", "")),
                ("api_key", "API Key", ""))):
            tk.Label(form, text=label, font=("Microsoft YaHei", 9)).grid(row=row, column=0, sticky="w", pady=4)
            entry = tk.Entry(form, width=46, show="*" if key == "api_key" else "")
            entry.insert(0, str(value))
            entry.grid(row=row, column=1, sticky="we", padx=8, pady=4)
            fields[key] = entry
        form.columnconfigure(1, weight=1)
        tip = tk.Label(win, text="截图只在本机；开启后才会发给这个端点。Key 不写进 Git。",
                       fg="#66708a", font=("Microsoft YaHei", 8), anchor="w")
        tip.pack(fill="x", padx=16, pady=(8, 4))

        def save() -> None:
            cfg["base_url"] = fields["base_url"].get().strip()
            cfg["model"] = fields["model"].get().strip()
            key = fields["api_key"].get().strip()
            if key:
                try:
                    (BASE / ".secret_vision_key").write_text(key, encoding="utf-8")
                except Exception as exc:
                    self.bubble.show(f"Key 保存失败：{exc}", 3600)
                    return
            cfg["enabled"] = bool(cfg["base_url"] and cfg["model"])
            self.save_config()
            win.destroy()
            self.bubble.show("视觉模型设置保存啦。" if cfg["enabled"] else "视觉模型关掉了，只用前台窗口文字。", 3400)

        buttons = tk.Frame(win)
        buttons.pack(fill="x", padx=16, pady=12)
        tk.Button(buttons, text="保存", width=10, command=save).pack(side="right")
        tk.Button(buttons, text="取消", width=10, command=win.destroy).pack(side="right", padx=8)

    def see_screen(self) -> None:
        """截一张桌面 + 前台窗口，用她的口吻回应；截图只留在本机，按张数/时间清理。"""
        path = capture_screen(DATA_DIR / "cache" / "screens", keep=4, max_age_hours=24)
        ctx = screen_context()
        line = context_line(ctx)
        self.bubble.show("唔…本鲸瞄一眼。", 1800)
        if path is None:
            self.bubble.show(line + "（截图没拍成）", 5200)
            return
        self._touch()
        vision = resolve_vision(self.config, BASE)
        vision_path = vision_copy(path, path.parent) or path

        def worker() -> None:
            description = ""
            # 首选主人自己 API 的 DeepSeek 原生视觉（真实图片输入，无需额外 Key）
            try:
                description = self.brain.see_image(
                    vision_path,
                    "主人在做什么？屏幕上有什么值得注意的文字或界面？")
            except Exception:
                description = ""
            # 再回退本机 Claude Code CLI
            if not description:
                try:
                    description = claude_see(
                        path,
                        "读取这张屏幕截图，用中文客观描述：主人在用什么程序、在做什么、"
                        "屏幕上值得注意的文字或界面。不要编造看不清的内容，不要复述隐私全文。",
                        log=log)
                except Exception:
                    description = ""
            # 最后回退用户配置的 OpenAI 兼容视觉模型
            if not description and vision["ready"]:
                description = describe_screen_image(
                    path,
                    "这是主人的桌面截图。用中文客观描述：主人在用什么程序、在做什么、"
                    "画面里值得注意的内容。不要编造看不清的字；不要输出隐私细节全文。",
                    self.config, BASE, log=log)
            if description:
                prompt = (f"主人让你看了一眼屏幕。你看到的画面：{description}\n"
                          f"前台窗口：{line}。用小鲸鱼的口吻自然地回应一句，可以关心或吐槽，"
                          f"不要编造画面外的事。")
            else:
                prompt = (f"主人让你看了一眼屏幕：{line}。你手里有一张本机截图，但视觉读取没成功，"
                          f"不要编造截图里的具体画面；用小鲸鱼的口吻自然地回应一句，可以问他在做什么。")
            try:
                reply, _source = self.brain.reply(prompt)
            except Exception:
                reply = f"本鲸看到了：{line}。你在忙什么呀？"
            self._last_screen_observation = description or line
            self.root.after(0, self._screen_done, reply)

        threading.Thread(target=worker, daemon=True).start()

    def _screen_done(self, reply: str) -> None:
        self.bubble.show(reply, 7000)
        try:
            self.voice.speak(reply)
        except Exception:
            pass
        self._set_emotion("confused", duration_ms=2600)
        self._remember_visual("看屏幕", self._last_screen_observation)
        if self.quick_chat is not None and self.quick_chat.winfo_exists():
            self.quick_chat.append("她（看看屏幕）", reply)

    def toggle_ambient_screen(self) -> None:
        enabled = bool(self.ambient_screen_var.get())
        self.config["ambient_screen_enabled"] = enabled
        self.save_config()
        self.bubble.show("好，我会偶尔瞄一眼你在干嘛；不想看随时关。" if enabled
                         else "好，我不偷看啦。", 3200)

    def _ambient_screen_tick(self) -> None:
        if self.config.get("ambient_screen_enabled", True):
            threading.Thread(target=self._ambient_screen_worker, daemon=True).start()
        interval = max(120, int(self.config.get("ambient_screen_interval", 300) or 300))
        self.root.after(interval * 1000, self._ambient_screen_tick)

    def _ambient_screen_worker(self) -> None:
        """偶尔自己看一眼屏幕，记住主人在干嘛，偶尔自然嘀咕一句；不每次都说话。"""
        if getattr(self, "_ambient_screen_busy", False):
            return
        self._ambient_screen_busy = True
        try:
            path = capture_screen(DATA_DIR / "cache" / "screens", keep=4, max_age_hours=24)
            if path is None:
                return
            ctx = screen_context()
            line = context_line(ctx)
            vision_path = vision_copy(path, path.parent) or path
            description = ""
            try:
                description = self.brain.see_image(
                    vision_path,
                    "简要客观描述主人在做什么：前台程序、正在进行的任务、值得注意的界面文字；"
                    "不要复述密码、密钥、聊天隐私全文。")
            except Exception:
                description = ""
            if not description:
                try:
                    description = claude_see(
                        path,
                        "简要客观描述这张屏幕截图里主人在做什么；不要复述隐私全文。",
                        log=log)
                except Exception:
                    description = ""
            if not description:
                return
            import difflib
            previous = str(getattr(self, "_last_ambient_screen", "") or "")
            changed = difflib.SequenceMatcher(None, previous, description).ratio() < 0.82
            self._last_ambient_screen = description
            self._last_screen_observation = description
            self._remember_visual("自己看屏幕", description)
            if not changed:
                return
            try:
                remark = self.brain.remark(
                    "你刚瞄了一眼主人的屏幕。根据素材说一句自然、简短、不打扰的话；"
                    "像同居的人随口一句，不要报菜名，不要编画面外的事。",
                    material=description[:700], max_tokens=90)
            except Exception:
                remark = None
            if remark:
                self.root.after(0, self._ambient_screen_done, remark)
        except Exception as exc:
            log(f"ambient screen: {exc}")
        finally:
            self._ambient_screen_busy = False

    def _ambient_screen_done(self, remark: str) -> None:
        self.bubble.show(remark, 5200)
        try:
            if self.quick_chat is not None and self.quick_chat.winfo_exists():
                self.quick_chat.append("她（瞄了一眼）", remark)
        except Exception:
            pass

    def _remember_visual(self, kind: str, text: str) -> None:
        """把一次视觉观察作为「看过的经历」写进记忆；限频，避免刷屏。"""
        text = str(text or "").strip()
        if not self.config.get("visual_memory", True) or len(text) < 8:
            return
        interval = float(self.config.get("visual_memory_interval", 300) or 300)
        if time.monotonic() - getattr(self, "_last_visual_memory", 0.0) < interval:
            return
        self._last_visual_memory = time.monotonic()
        try:
            self.brain.memory_store.add_episode(f"【{kind}】{text[:180]}", start=time.time(), end=time.time())
            self.brain.memory_store.save()
        except Exception:
            pass

    def see_video(self) -> None:
        """选一段本地视频，抽 6 帧拼成 storyboard，用她的原生视觉理解。"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="让她看一段视频",
            filetypes=[("视频", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"), ("所有文件", "*.*")],
            parent=self.root)
        if not path:
            return
        self.bubble.show("本鲸看看这段视频……", 1800)

        def worker() -> None:
            summary = ""
            try:
                summary = describe_video(Path(path), self.brain, DATA_DIR / "home" / "videos")
            except Exception:
                summary = ""
            if summary:
                try:
                    save_note(home_root(BASE), "videos", summary, title="看视频")
                except Exception:
                    pass
            self.root.after(0, self._video_vision_done, summary or "视频没读出来（可能格式/抽帧失败）。")

        threading.Thread(target=worker, daemon=True).start()

    def _video_vision_done(self, summary: str) -> None:
        self.bubble.show(summary[:900], 9000)
        try:
            self.voice.speak(summary)
        except Exception:
            pass
        self._set_emotion("happy", duration_ms=2600)
        self._remember_visual("看视频", summary)

    def open_last_screen(self) -> None:
        path = DATA_DIR / "cache" / "screens" / "screen-last.png"
        if not path.exists():
            screens = sorted((DATA_DIR / "cache" / "screens").glob("screen-*.png"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            path = screens[0] if screens else path
        if not path.exists():
            self.bubble.show("还没拍过屏幕呢，右键「👀 看看屏幕」先拍一张。", 3600)
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            self.bubble.show(f"截图在这儿：{path}（{exc}）", 5200)

    def see_news(self) -> None:
        """拉公开新闻标题，让她挑一条聊，并写进自己的小屋。"""
        self.bubble.show("本鲸去看看今天有什么新闻……", 1800)

        def worker() -> None:
            items = fetch_headlines(6)
            if not items:
                self.root.after(0, lambda: self.bubble.show("新闻源今天连不上，明天再看。", 3600))
                return
            prompt = ("这里是今天的新闻标题：\n" + headlines_text(items) +
                      "\n挑一条你最感兴趣的，用小鲸鱼的口吻说一两句（可以吐槽/好奇），不要逐条念。")
            try:
                reply, _ = self.brain.reply(prompt)
            except Exception:
                reply = "今天的新闻：" + items[0]["title"]
            try:
                root = home_root(BASE)
                save_note(root, "news", reply, title="今日新闻")
            except Exception:
                pass
            self.root.after(0, self._news_done, reply)

        threading.Thread(target=worker, daemon=True).start()

    def _news_done(self, reply: str) -> None:
        self.bubble.show(reply, 7600)
        try:
            self.voice.speak(reply)
        except Exception:
            pass
        self._set_emotion("happy", duration_ms=2400)

    def itunes_action(self, fn) -> None:
        if fn is None:
            self.bubble.show("iTunes 控制没接上（需要本机 iTunes + pywin32）。", 4200)
            return
        try:
            message = fn()
            self.bubble.show(str(message)[:120], 4200)
        except Exception as exc:
            self.bubble.show("iTunes 操作失败：" + str(exc)[:220], 5200)

    def itunes_search(self) -> None:
        from tkinter import simpledialog
        query = simpledialog.askstring("iTunes 放歌", "想听什么歌？输入歌名/歌手", parent=self.root)
        if not query:
            return
        self.itunes_action(lambda: itunes_search_play(query))

    def itunes_show_now(self) -> None:
        self.itunes_action(itunes_now_playing)

    def sing_seedvc(self) -> None:
        """选一段源唱，用 Seed-VC 换成她的音色唱出来。"""
        if seedvc_convert is None or not seedvc_available():
            self.bubble.show("Seed-VC 唱歌环境/模型还没就绪。", 4200)
            return
        from tkinter import filedialog
        source = filedialog.askopenfilename(
            title="选一段源唱/音频给她唱",
            filetypes=[("音频", "*.wav *.mp3 *.flac *.m4a *.ogg"), ("所有文件", "*.*")],
            parent=self.root)
        if not source:
            return
        self.bubble.show("本鲸在学这首歌……要加载模型，稍等。", 2600)

        def worker() -> None:
            try:
                path = seedvc_convert(Path(source), DATA_DIR / "home" / "songs", log=log)
                self.root.after(0, lambda: self._seedvc_done(path))
            except Exception as exc:
                self.root.after(0, lambda: self.bubble.show("唱歌转换失败：" + str(exc)[:300], 7000))

        threading.Thread(target=worker, daemon=True).start()

    def _seedvc_done(self, path) -> None:
        self.bubble.show(f"唱好啦：{path.name}（在小屋 songs 里）", 6000)
        try:
            self.voice.speak(preset=path)
        except Exception:
            pass
        try:
            os.startfile(str(path))
        except Exception:
            pass

    def sing_seedvc_auto(self) -> None:
        """她自己找：iTunes 当前曲目优先，否则本机 Music/Downloads 最近一首。"""
        if seedvc_convert is None or not seedvc_available():
            self.bubble.show("Seed-VC 唱歌环境/模型还没就绪。", 4200)
            return
        if pick_music_source is None:
            self.bubble.show("找歌源模块没接上。", 3000)
            return
        try:
            source = pick_music_source()
        except Exception:
            source = None
        if not source:
            self.bubble.show("本鲸没找到歌源：先在 iTunes 放一首，或把音频放进 Music/Downloads。", 5600)
            return
        self.bubble.show(f"本鲸自己挑了：{Path(source).name}，开始学唱……", 3200)

        def worker() -> None:
            try:
                path = seedvc_convert(Path(source), DATA_DIR / "home" / "songs", log=log)
                self.root.after(0, lambda: self._seedvc_done(path))
            except Exception as exc:
                self.root.after(0, lambda: self.bubble.show("唱歌转换失败：" + str(exc)[:300], 7000))

        threading.Thread(target=worker, daemon=True).start()

    def netease_play(self) -> None:
        """网易云公开搜索 + 下载可播放音频，用她的语音队列播放。"""
        if netease_search is None or netease_download is None:
            self.bubble.show("网易云接口没接上。", 3200)
            return
        from tkinter import simpledialog
        query = simpledialog.askstring("网易云放歌", "想听什么歌/歌手？", parent=self.root)
        if not query or not str(query).strip():
            return
        query = str(query).strip()[:120]
        self.bubble.show("本鲸去网易云找找……", 2000)

        def worker() -> None:
            try:
                items = netease_search(query, limit=3)
                if not items:
                    self.root.after(0, lambda: self.bubble.show("网易云没搜到这首歌。", 3800))
                    return
                song = items[0]
                path = netease_download(song["id"], DATA_DIR / "cache" / "netease")
                label = f"{song.get('name', '')} — {song.get('artists', '')}".strip(" —")
                self.root.after(0, lambda: self._netease_done(path, label))
            except Exception as exc:
                self.root.after(0, lambda: self.bubble.show("网易云播放失败：" + str(exc)[:260], 5600))

        threading.Thread(target=worker, daemon=True).start()

    def _netease_done(self, path, label: str) -> None:
        self.bubble.show("正在放：" + label, 4200)
        try:
            self.voice.speak(preset=path)
        except Exception as exc:
            self.bubble.show("播放失败：" + str(exc)[:220], 4200)

    def research_web(self) -> None:
        """她上网查公开资料；只读网页，结果写到自己的小屋。"""
        from tkinter import simpledialog
        query = simpledialog.askstring("让她上网查资料", "想让她查什么？", parent=self.root)
        if not query or not str(query).strip():
            return
        if self.agent is None:
            self.bubble.show("Agent 没接上，暂时不能上网查。", 3200)
            return
        query = str(query).strip()[:300]
        self.bubble.show("本鲸去网上翻翻……", 1800)

        def worker() -> None:
            prompt = (f"请上网查一下：{query}。使用 web_search 找公开资料，必要时用 web_fetch 读正文。"
                      f"用简短中文总结关键发现（最多 10 行），并指出不确定的地方。不要编造来源。")
            try:
                result = self.agent.run(prompt)
                reply = str(result.get("reply") or "").strip()
            except Exception as exc:
                reply = f"查资料失败：{exc}"
            if reply:
                try:
                    save_note(home_root(BASE), "research", reply, title="上网查资料")
                except Exception:
                    pass
            self.root.after(0, self._research_done, reply or "这次没查到有用的东西。")

        threading.Thread(target=worker, daemon=True).start()

    def _research_done(self, reply: str) -> None:
        self.bubble.show(reply[:900], 10000)
        try:
            self.voice.speak(reply)
        except Exception:
            pass

    def generate_video(self) -> None:
        """Seedance 文生视频：需要 ARK_API_KEY；异步任务，完成下载到小屋。"""
        from tkinter import simpledialog
        key = resolve_ark_key(BASE, self.config)
        if not key:
            self.bubble.show("生成视频要火山方舟 Seedance Key：把 Key 写进 pet/.secret_ark_key，"
                             "或设环境变量 ARK_API_KEY，再点我。", 7600)
            return
        prompt = simpledialog.askstring(
            "让她生成视频",
            "想生成什么视频？（Seedance 文生视频，默认 5 秒 720p）",
            parent=self.root)
        if not prompt or not str(prompt).strip():
            return
        prompt = str(prompt).strip()[:1000]
        self.bubble.show("本鲸去生成视频啦，可能要等几分钟……", 2600)

        def worker() -> None:
            try:
                task = create_video_task(prompt, key, self.config)
                task_id = str(task.get("id") or "")
                if not task_id:
                    raise ValueError(str(task.get("error") or "没有返回任务 ID"))
                self.root.after(0, lambda: self.bubble.show(f"视频任务已创建：{task_id[:18]}… 等它生成", 4200))
                result = wait_video_task(task_id, key, timeout=600, log=log)
                status = str(result.get("status") or "")
                if status != "succeeded":
                    message = str((result.get("error") or {}).get("message") or status or "失败")
                    self.root.after(0, lambda: self.bubble.show("视频没生成成功：" + message[:300], 6200))
                    return
                url = str(((result.get("content") or {}).get("video_url")) or "")
                if not url:
                    self.root.after(0, lambda: self.bubble.show("视频好了，但没拿到下载地址。", 4800))
                    return
                path = download_video(url, DATA_DIR / "home" / "videos")
                self.root.after(0, lambda: self._video_done(path))
            except Exception as exc:
                self.root.after(0, lambda: self.bubble.show("生成视频失败：" + str(exc)[:300], 6200))

        threading.Thread(target=worker, daemon=True).start()

    def _video_done(self, path) -> None:
        self.bubble.show(f"视频生成好啦：{path.name}（已放进小屋）", 6000)
        try:
            os.startfile(str(path))
        except Exception:
            pass
        self._set_emotion("happy", duration_ms=3000)

    def optimize_self(self) -> None:
        """让她上网找资料，自己写一份优化计划到小屋；不改代码/配置。"""
        if self.agent is None:
            self.bubble.show("Agent 没接上，暂时不能自己优化。", 3200)
            return
        self.bubble.show("本鲸想想怎么变得更懂你……", 2400)

        def worker() -> None:
            prompt = ("请先从你的小屋/记忆/相处笔记里看看自己哪里还能更像人、更懂主人；"
                      "再用 web_search 查 1~2 个公开资料补充想法。最后写一份 5~8 行的优化计划，"
                      "只写进 pet/home/notes/，不要改代码、配置或主人的文件。")
            try:
                result = self.agent.run(prompt)
                reply = str(result.get("reply") or "").strip()
            except Exception as exc:
                reply = f"自我优化失败：{exc}"
            if reply:
                try:
                    save_note(home_root(BASE), "notes", reply, title="自我优化计划")
                except Exception:
                    pass
            self.root.after(0, self._research_done, reply or "这次没想出什么。")

        threading.Thread(target=worker, daemon=True).start()

    def open_nest(self) -> None:
        if self.nest_window is not None and self.nest_window.winfo_exists():
            self.nest_window.lift()
            return
        self.nest_window = NestWindow(self)

    def build_nest(self) -> None:
        """她自己从家当里挑一件放进窝。"""
        self.open_nest()
        nest = self.nest_window
        if nest is None:
            return
        candidates = [item for item in NestWindow.CATALOG if item not in nest.items]
        if not candidates:
            self.bubble.show("窝里快放满啦，先让她收拾一下。", 2800)
            return
        pick = random.choice(candidates)
        nest.add_item(pick)
        self.bubble.show(f"她把「{pick}」放进小窝了。", 3200)
        self._set_emotion("happy", duration_ms=2200)

    def add_nest_item(self) -> None:
        from tkinter import simpledialog
        label = simpledialog.askstring("往她窝里放东西", "放什么？（一个 emoji + 名字）", parent=self.root)
        if not label:
            return
        self.open_nest()
        if self.nest_window is not None:
            self.nest_window.add_item(label)

    def reflect_home(self) -> None:
        """让她整理最近的想法/小屋笔记；只写 pet/home。"""
        self.bubble.show("本鲸回小屋整理一下……", 1800)

        def worker() -> None:
            try:
                recent = self.brain.memory_store.recent_dialogue(12)
                learned = getattr(self.brain, "learned_notes", "")[-800:]
            except Exception:
                recent, learned = "", ""
            prompt = ("这是你小屋里的近况和最近对话。写一段简短的自我整理（80~160字）："
                      "可以是今天的心情、想记住的事、或想对主人说的话，不要总结对话。\n"
                      f"最近对话：\n{recent}\n相处笔记：\n{learned}")
            try:
                reply, _ = self.brain.reply(prompt)
            except Exception:
                reply = "今天本鲸在桌面上待着，尾巴有点痒，想被摸一下。"
            path = None
            try:
                path = save_note(home_root(BASE), "notes", reply, title="小屋自省")
            except Exception:
                pass
            self.root.after(0, self._reflect_done, reply, path)

        threading.Thread(target=worker, daemon=True).start()

    def _reflect_done(self, reply: str, path) -> None:
        self.bubble.show(reply, 7200)
        try:
            self.voice.speak(reply)
        except Exception:
            pass
        if path:
            log(f"小屋自省已写入：{path}")

    def set_window_opacity(self, value: float) -> None:
        value = clamp_opacity(value)
        self.config["window_opacity"] = value
        try:
            self.root.attributes("-alpha", value)
        except Exception:
            pass
        self.save_config()
        self.bubble.show(f"透明度 {int(value * 100)}%。", 2600)

    def _apply_saved_opacity(self) -> None:
        value = clamp_opacity(self.config.get("window_opacity", 1.0))
        if value < 0.999:
            try:
                self.root.attributes("-alpha", value)
            except Exception:
                pass

    def reset_position(self) -> None:
        try:
            self._place_window()
            self._save_position()
            self.bubble.show("回到默认位置啦。", 2600)
        except Exception:
            pass

    # ---------- 启动/退出 ----------
    def _startup(self) -> None:
        if self.config.get("greet_on_start"):
            picked = self._pick_preset("主人好", "鲸鱼娘")
            if picked:
                text, path = picked
                self.bubble.show(text, 6000)
                self._speak_preset(text, path)
            else:
                self.bubble.show(GREETING_FALLBACK, 6000)
                self.voice.speak(GREETING_FALLBACK)
            self._greet_animation()
        if self.brain.status_text().startswith("离线"):
            self.root.after(6500, lambda: self.bubble.show(NO_KEY_HINT, 9000))
        log("；".join(filter(None, [self.brain.status_text(), self.brain.memory_summary(),
                                    self.brain.personal_summary(), self.status.describe(),
                                    "形象：" + self.library.display_name,
                                    "总览：" + ("可用" if self.overview else "不可用"),
                                    "Agent：" + ("可用" if self.agent else "不可用")])))
        if self.demo:
            self.root.after(1200, self.open_chat)

    def quit(self) -> None:
        try:
            self.living.save()
        except Exception:
            pass
        if getattr(self, "_body3d_proc", None) is not None:  # 3D 身体跟着一起关
            try:
                self.body3d_var.set(False)
                self.toggle_body3d()
            except Exception:
                pass
        try:
            if getattr(self, "wechat_server", None) is not None:
                self.wechat_server.stop()
        except Exception:
            pass
        try:
            if getattr(self, "qq_bridge", None) is not None:
                self.qq_bridge.stop()
        except Exception:
            pass
        try:
            if getattr(self, "shared_server", None) is not None:
                self.shared_server.stop()
        except Exception:
            pass
        try:
            if getattr(self, "wake_listener", None) is not None:
                self.wake_listener.stop()
        except Exception:
            pass
        try:
            self._save_position()
        except Exception:
            pass
        try:
            self.voice.shutdown()
        except Exception:
            pass
        try:
            if getattr(self, "tray_service", None) is not None:
                self.tray_service.stop()
        except Exception:
            pass
        try:
            if getattr(self, "hotkey_service", None) is not None:
                self.hotkey_service.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit()


def summon_running_instance() -> bool:
    """已经有一只在跑（共用大脑在线）就叫她出来，自己退出——双击桌面图标不会开出第二只。"""
    if os.environ.get("DFY_CONFIG"):  # 测试/临时配置不查
        return False
    config = load_json(CONFIG_PATH, {})
    server = config.get("server") if isinstance(config.get("server"), dict) else {}
    port = int(server.get("port") or 8765)
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.8) as resp:
            if json.loads(resp.read().decode("utf-8")).get("service") != "dfy-shared-brain":
                return False
    except Exception:
        return False
    try:  # 叫她出来；旧版本不认识 summon 也没关系——已经有一只在跑，就不开第二只
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/pet/event", data=b'{"kind": "summon"}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=2).close()
    except Exception:
        pass
    return True


def main() -> None:
    demo = "--demo" in sys.argv
    hidden = "--hidden" in sys.argv
    if not hidden and summon_running_instance():
        log("已经有一只在桌面上了：叫她出来，不再开第二只")
        return
    log("=" * 60)
    log("启动大肥鱼桌宠" + ("（demo）" if demo else "") + ("（hidden）" if hidden else ""))
    app = PetApp(demo=demo, hidden=hidden)
    app.run()


if __name__ == "__main__":
    main()
