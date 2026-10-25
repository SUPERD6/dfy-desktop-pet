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
import random
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk
from interactions import (DRAG_LINES, FEED_LINES, INNER_VOICE_LINES, render_food,
                          state_for_actions, step_flight)
from stickers import StickerBook

CHOMP_SOUNDS = ("啊呜～咔嚓咔嚓。", "嗷呜，吧唧吧唧……", "咔嚓咔嚓，好吃！")

BASE = Path(__file__).resolve().parent
ASSETS = BASE / "assets"
CACHE = BASE / "cache"
LOG_DIR = BASE / "logs"
CONFIG_PATH = BASE / "config.json"
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
        self._cache[key] = out
        return out

    def _tick(self) -> None:
        if not self._state:
            return
        frames = self._frames(self._state)
        if not frames:
            return
        image, duration = frames[self._index % len(frames)]
        self.canvas.itemconfigure(self.item, image=image)
        self.canvas.tag_raise(self.item)
        self._index += 1
        finished = self._index >= len(frames)
        if finished and self._once_then:
            follow = self._once_then
            self._once_then = None
            self.play(follow)
            return
        self._after_id = self.root.after(duration, self._tick)

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

    def toggle_mic(self) -> None:
        if self._listening:
            self._listening = False
            self.mic_button.configure(text="🎤 说话")
            return
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
            self.append("me", "你（语音）", text)
            self.send(text)
        else:
            self.append("sys", "系统", "没听清：" + (error or "再试一次吧。"))
            self.status_label.configure(text=self.app.brain.status_text())

    def close(self) -> None:
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


# --------------------------------------------------------------------------- 主程序
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
        self.config.setdefault("quick_chat", True)
        self.config.setdefault("chat_open_on_start", False)
        self.config.setdefault("autonomy_enabled", True)
        self.config.setdefault("self_improve_enabled", True)
        self.config.setdefault("roam_enabled", False)
        agent_cfg = self.config.setdefault("agent", {})
        if isinstance(agent_cfg, dict):
            agent_cfg.setdefault("auto_approve", False)
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
        self.bubble = Bubble(self.canvas, self.W)

        self.brain = Brain(BASE, self.config, log=log)
        self.asr = ASR(language=self.config.get("asr_language", "zh-CN"), log=log)
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
        from living import Living
        self.living = Living(self, BASE / "life.json", BASE / "home", log=log)
        self.menu = self._build_menu()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        self.animator.play(self.library.best("idle"))
        self.root.after(700, self._startup)
        self.living.start()
        self.root.after(1500, self._save_rect)
        self.root.after(1300, self._poll_dsh_status)
        self.root.after(8000, self._proactive_tick)
        self.root.after(3000, self._overview_tick)
        self.root.after(8000, self._roam_tick)
        if not hidden and self.config.get("quick_chat", True):
            self.root.after(1600, self.open_quick_chat)
        if not hidden and self.config.get("chat_open_on_start"):
            self.root.after(2000, self.open_chat)
        self.root.after(15000, self._autonomy_tick)
        self.root.after(20 * 60 * 1000, self._self_improve_tick)
        if not hidden and (self.config.get("wake") or {}).get("enabled"):
            self.root.after(2500, self.start_wake)
        if self.config.get("server", {}).get("enabled", True):
            self.root.after(1800, self.start_shared_brain)
        if (self.config.get("qq") or {}).get("enabled"):
            self.root.after(2600, self.start_qq)
        if (self.config.get("wechat") or {}).get("enabled"):
            self.root.after(3200, self.start_wechat)

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
        """「此刻」一行（≤70 字）：几点、她刚在干嘛、她的状态、主人在干嘛。让她像活在当下，而不是每次从零开始。"""
        weekday = "一二三四五六日"[time.localtime().tm_wday]
        parts = [f"周{weekday} {time.strftime('%H:%M')}"]
        living = getattr(self, "living", None)
        if living is not None:
            doing = {"stroll": "在桌面上溜达", "climb_window": "爬到窗口顶上玩", "peek_edge": "躲在屏幕边偷看",
                     "nap": "刚在打盹", "snack": "刚偷吃了零食", "dance": "刚在跳舞", "browse_file": "刚翻了主人的文件",
                     "write_diary": "刚写完日记", "call_master": "刚在喊主人"}.get(living.activity)
            if doing:
                parts.append(f"你{doing}")
            parts.append(f"你现在{living.state.summary()}")
        try:
            import desktop
            label = desktop.guess_activity(desktop.foreground_title())
            if label:
                parts.append(f"主人好像在{label}")
        except Exception:
            pass
        return "；".join(parts)[:70]

    def ask_agent(self, text: str, on_reply=None) -> None:
        self.brain.live_context = self.live_context()
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
        self.canvas.bind("<Double-Button-1>", lambda event: self.open_chat())
        self.canvas.bind("<Button-3>", self._on_context_menu)

    def _on_press(self, event) -> None:
        if self._flight_after is not None:
            try:
                self.root.after_cancel(self._flight_after)
            except Exception:
                pass
            self._flight_after = None
        self._flight = None
        self._drag = {
            "sx": event.x_root, "sy": event.y_root,
            "wx": self.root.winfo_x(), "wy": self.root.winfo_y(), "moved": False,
            "samples": [(time.monotonic(), event.x_root, event.y_root)],
        }

    def _on_motion(self, event) -> None:
        if not self._drag:
            return
        self._drag["samples"].append((time.monotonic(), event.x_root, event.y_root))
        self._drag["samples"] = self._drag["samples"][-6:]
        dx = event.x_root - self._drag["sx"]
        dy = event.y_root - self._drag["sy"]
        if abs(dx) + abs(dy) > 5:
            self._drag["moved"] = True
        if self._drag["moved"]:
            self.root.geometry(f"+{self._drag['wx'] + dx}+{self._drag['wy'] + dy}")

    def _on_release(self, event) -> None:
        drag = self._drag
        self._drag = None
        if not drag:
            return
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
            if self._living_on():
                self.living.dropped()  # 放在半空会掉到下面的窗口或任务栏上
        else:
            self.pat()

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
        menu.add_command(label="🗨️  打开聊天大窗", command=self.open_chat)
        self.quick_var = tk.BooleanVar(value=bool(self.config.get("quick_chat", True)))
        menu.add_checkbutton(label="⌨  桌面聊天面板", variable=self.quick_var, command=self.toggle_quick_chat)
        self.wake_var = tk.BooleanVar(value=bool((self.config.get("wake") or {}).get("enabled")))
        menu.add_checkbutton(label="🎙  喊名字唤醒（联网识别）", variable=self.wake_var,
                             command=self.toggle_wake)
        self.autonomy_var = tk.BooleanVar(value=bool(self.config.get("autonomy_enabled", True)))
        menu.add_checkbutton(label="🎲  让她自己动一动", variable=self.autonomy_var,
                             command=self.toggle_autonomy)
        menu.add_command(label="🧠  打开相处笔记", command=self.open_learned_notes)
        menu.add_command(label="🧾  打开 PARTNER.md", command=self.open_partner)
        menu.add_command(label="🌉  QQ 共用大脑接口", command=self.show_shared_brain)
        menu.add_command(label="💬  网页聊天（浏览器）", command=self.open_web_chat)
        self.wechat_var = tk.BooleanVar(value=bool((self.config.get("wechat") or {}).get("enabled")))
        menu.add_checkbutton(label="📮  公众号接入", variable=self.wechat_var,
                             command=self.toggle_wechat)
        menu.add_command(label="📡  公众号状态", command=self.show_wechat_status)
        menu.add_command(label="💰  API 余额", command=self.check_balance)
        menu.add_command(label="🧠  看她的记忆", command=self.show_memory)
        self.roam_var = tk.BooleanVar(value=bool(self.living.cfg.get("enabled", True)))
        menu.add_checkbutton(label="🏠  住在桌面上（自己生活）", variable=self.roam_var,
                             command=self.toggle_living)
        menu.add_command(label="📂  打开她的小窝", command=self.open_home)
        self.auto_approve_var = tk.BooleanVar(
            value=bool((self.config.get("agent") or {}).get("auto_approve")))
        menu.add_checkbutton(label="🔓  全权限托管（危险）", variable=self.auto_approve_var,
                             command=self.toggle_auto_approve)
        self.qq_var = tk.BooleanVar(value=bool((self.config.get("qq") or {}).get("enabled")))
        menu.add_checkbutton(label="📨  通到 QQ（OneBot）", variable=self.qq_var,
                             command=self.toggle_qq)
        menu.add_command(label="📡  QQ 桥状态", command=self.show_qq_status)
        menu.add_command(label="🤚  摸摸头", command=self.pat)
        feed_menu = tk.Menu(menu, tearoff=0, font=("Microsoft YaHei", 9))
        for key, label in (("rice", "🍚 白饭"), ("fish", "🐟 小鱼干"),
                           ("cake", "🍰 蛋糕"), ("tangyuan", "🍡 团子"),
                           ("diamond", "💎 钻石")):
            feed_menu.add_command(label=label, command=lambda food=key: self.feed(food))
        menu.add_cascade(label="🍽  投喂", menu=feed_menu)
        menu.add_command(label="🖼  甩个表情包",
                         command=lambda: self.send_sticker(self._emotion if self._emotion != "idle" else "happy",
                                                           force=True))
        perform_menu = tk.Menu(menu, tearoff=0, font=("Microsoft YaHei", 9))
        for key, label in (("happy", "开心"), ("shy", "害羞"), ("angry", "生气"),
                           ("sad", "难过"), ("surprised", "惊讶"), ("confused", "困惑"),
                           ("sleepy", "困倦"), ("proud", "得意"), ("hungry", "馋嘴")):
            perform_menu.add_command(label=label, command=lambda e=key: self.perform(e))
        perform_menu.add_separator()
        perform_menu.add_command(label="🎲 随机一个",
                                 command=lambda: self.perform(random.choice(DFY_EMOTIONS)))
        menu.add_cascade(label="✨  让她表演", menu=perform_menu)
        character_menu = tk.Menu(menu, tearoff=0, font=("Microsoft YaHei", 9))
        for char_id, char_name in self.available_characters():
            prefix = "✓ " if char_id == self.character else "   "
            character_menu.add_command(label=prefix + char_name,
                                       command=lambda c=char_id: self.switch_character(c))
        menu.add_cascade(label="🎭  换个形象", menu=character_menu)
        menu.add_command(label="📋  工作总览", command=self.open_overview)
        personal_menu = tk.Menu(menu, tearoff=0, font=("Microsoft YaHei", 9))
        personal_menu.add_command(label="查看接入状态", command=self.show_personal)
        personal_menu.add_command(label="刷新个人库", command=self.refresh_personal)
        personal_menu.add_command(label="打开个人库目录", command=self.open_personal_dir)
        menu.add_cascade(label="📚  个人库", menu=personal_menu)
        menu.add_separator()
        self.mute_var = tk.BooleanVar(value=bool(self.config.get("mute")))
        menu.add_checkbutton(label="🔇  静音", variable=self.mute_var, command=self.toggle_mute)
        self.topmost_var = tk.BooleanVar(value=bool(self.config.get("always_on_top", True)))
        menu.add_checkbutton(label="📌  窗口置顶", variable=self.topmost_var, command=self.toggle_topmost)
        self.dsh_var = tk.BooleanVar(value=bool((self.config.get("dsh_status") or {}).get("enabled", True)))
        menu.add_checkbutton(label="🌊  跟随 DSH 工作状态", variable=self.dsh_var,
                             command=self.toggle_dsh_follow)
        self.proactive_var = tk.BooleanVar(value=bool((self.config.get("proactive") or {}).get("enabled", True)))
        menu.add_checkbutton(label="💬  允许主动搭话", variable=self.proactive_var,
                             command=self.toggle_proactive)
        menu.add_separator()
        menu.add_command(label="⚙  设置…", command=self.open_settings)
        menu.add_command(label="❌  退出", command=self.quit)
        return menu

    def toggle_mute(self) -> None:
        self.config["mute"] = bool(self.mute_var.get())
        self.save_config()
        self.bubble.show("静音啦。" if self.config["mute"] else "声音回来啦。", 1600)

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

    def _react(self, keywords: tuple[str, ...], fallback: tuple[str, ...], emotion: str) -> None:
        picked = self._pick_preset(*keywords)
        if picked:
            text, path = picked
            self.bubble.show(text)
            self.voice.speak(preset=path)
        else:
            text = random.choice(fallback)
            self.bubble.show(text)
            self.voice.speak(text)
        self._cancel_speech_settle()
        self._set_emotion(emotion, duration_ms=3200)

    def pat(self) -> None:
        self._touch()
        self.living.event("pat")
        self._react(("摸摸", "摸头", "头", "贴贴", "抱抱"), (
            "唔……只能摸一下哦，摸多了本鲸的算力会不稳定……再摸一下也行。",
            "（鲸鳍耳像含羞草一样缩起来）才、才没有很舒服。",
            "摸头可以，摸呆毛要收费，一根白饭。",
            "尾巴是奖励，不是随便摸的……好吧，给你摸一下。",
            "哼，算你有点眼光，本鲸的头很软的。",
        ), "shy")

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

    def feed(self, food: str = "rice") -> None:
        self._touch()
        if food not in FEED_LINES:
            food = "rice"
        counts = self.config.setdefault("feed_counts", {})
        counts[food] = int(counts.get(food, 0)) + 1
        self.living.event(f"fed:{food}")
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

    def _balance_worker(self) -> None:
        try:
            from usage import query_balance
            llm = self.config.get("llm") or {}
            key, _source = self.brain._resolve_api_key()
            result = query_balance(key, str(llm.get("base_url") or "https://api.deepseek.com/v1"))
            text = result.get("text") or "余额查询没有结果。"
        except Exception as exc:
            text = f"查余额失败：{exc}"
        self.root.after(0, lambda: self.bubble.show(str(text), 6000))
        log(str(text))

    # ---------- 通到 QQ（OneBot HTTP） ----------
    def _qq_reply(self, text: str) -> str:
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

    # ---------- 自主活动 / 全权限 / 记忆查看 ----------
    def toggle_living(self) -> None:
        on = bool(self.roam_var.get())
        self.living.cfg["enabled"] = on
        self.save_config()
        if on:
            self.living.dropped()
        self.bubble.show("好呀，本鲸自己在桌面上过日子啦～" if on else "那本鲸乖乖待在这儿不动。", 2600)

    def open_home(self) -> None:
        home = BASE / "home"
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
        path = BASE / "memory.json"
        try:
            summary = self.brain.memory_store.summary()
        except Exception:
            summary = "记忆读取失败"
        self.bubble.show(summary, 6000)
        try:
            os.startfile(str(path))
        except Exception as exc:
            self.bubble.show(f"打不开记忆文件：{exc}", 3000)

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

    def _self_improve_tick(self) -> None:
        if self.config.get("self_improve_enabled", True):
            threading.Thread(target=self._self_improve_worker, daemon=True).start()
        self.root.after(20 * 60 * 1000, self._self_improve_tick)

    def _self_improve_worker(self) -> None:
        try:
            note = self.brain.self_reflect()
        except Exception as exc:
            log(f"相处笔记整理失败：{exc}")
            return
        if note:
            log(f"相处笔记更新：{note[:120]}")
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
            self.voice.speak(preset=path)
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
        self.brain.live_context = self.live_context()
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

    # ---------- 启动/退出 ----------
    def _startup(self) -> None:
        if self.config.get("greet_on_start"):
            picked = self._pick_preset("主人好", "鲸鱼娘")
            if picked:
                text, path = picked
                self.bubble.show(text, 6000)
                self.voice.speak(preset=path)
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
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit()


def main() -> None:
    demo = "--demo" in sys.argv
    hidden = "--hidden" in sys.argv
    log("=" * 60)
    log("启动大肥鱼桌宠" + ("（demo）" if demo else "") + ("（hidden）" if hidden else ""))
    app = PetApp(demo=demo, hidden=hidden)
    app.run()


if __name__ == "__main__":
    main()
