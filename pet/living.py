# -*- coding: utf-8 -*-
"""让大肥鱼「住」在桌面上：站在窗口顶边和任务栏上，走、跳、掉、偷看、打盹、自己找事做。

分工：
- desktop.py  看世界（窗口平台、主人在不在、在用什么）；
- life.py     过日子（饿/困/无聊/想主人 → 挑下一件事）；
- 本文件       把两者接到桌宠窗口上：每 50ms 管物理和走路，每 10~25s 做一次决定。

不打扰原则：主人在操作（20 秒内有键鼠输入）时只做安静的事；DSH 在干活、她在说话/吃东西/被拖时不做决定。
写东西只写她自己的小窝（pet/home/），翻主人的文件只读、只看 agent.allowed_roots 里的、跳过密钥类文件。
"""
from __future__ import annotations

import math
import os
import re
import random
import threading
import time
from pathlib import Path

import desktop
from desktop import landing_below, platform_at
from life import LifeState

GRAVITY = 1800.0
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".js", ".ts", ".tex", ".csv", ".yml", ".yaml", ".html", ".css"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "cache", "logs", ".ccx", "home"}
SECRET_HINTS = ("secret", ".env", "token", "password", "credential", ".key", "id_rsa", "memory.json")
# 她「翻文件」时绝不挑这些：运行状态和个人数据（片段会发给模型），以及备份（living-4）
BROWSE_EXCLUDE = {"config.json", "life.json", "appetite.json", "reminders.json", "memory.json", "overview.json",
                  "learned_notes.md", "partner.md", "工作总览.html", "memory.vectors.json", "voice.json"}
_HEX_NAME = re.compile(r"^[0-9a-f]{32,}")


def browse_excluded(name: str) -> bool:
    low = name.lower()
    return (low in BROWSE_EXCLUDE or ".bak" in low or low.startswith(".") or bool(_HEX_NAME.match(low))
            or any(h in low for h in SECRET_HINTS))


def browse_candidates(roots, cutoff: float, budget: int = 4000) -> list[tuple[float, Path]]:
    """roots：[(目录, 最大深度)]。返回 [(修改时间, 路径)]，最近改过、文本、不在排除名单里的。"""
    found: list[tuple[float, Path]] = []
    for root, max_depth in roots:
        stack = [(Path(root), 0)]
        while stack and budget > 0:
            folder, depth = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                budget -= 1
                if browse_excluded(entry.name):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if depth < max_depth and entry.name not in SKIP_DIRS:
                            stack.append((Path(entry.path), depth + 1))
                    elif Path(entry.name.lower()).suffix in TEXT_SUFFIXES:
                        info = entry.stat()
                        if info.st_mtime >= cutoff and info.st_size < 200_000:
                            found.append((info.st_mtime, Path(entry.path)))
                except OSError:
                    continue
    return found

SNACK_LINES = ("嘿嘿，偷吃一口没人看见吧～", "肚子咕咕叫了，本鲸自己找点吃的。", "这块是本鲸藏的，不分给你。")
CALL_LINES = ("主人～理理本鲸嘛。", "主人主人，你还在吗？", "本鲸好无聊，陪我说句话呗。", "（扯扯主人衣角）在忙吗？")
PEEK_LINES = ("（从屏幕边探头）……被发现了？", "本鲸只是路过，绝对没有偷看。", "（偷看）")
NAP_LINES = ("呼……本鲸眯一会儿。", "（抱着碗打盹）", "困了困了，就睡五分钟。")
DANCE_LINES = ("嘿嘿，跳个舞！", "♪ 本鲸今天心情好～")
CLIMB_LINES = ("爬上来看看风景～", "这里视野好！", "本鲸占领这个窗口了。")
CHASE_LINES = ("等等本鲸——", "抓到你的鼠标了！", "嘿嘿，跑不掉的～")
STARE_LINES = ("（被盯着看）……看、看什么看。", "主人的鼠标一直停在本鲸身上……", "唔，要摸头就直接点嘛。")
STARTLE_LINES = ("哇！吓本鲸一跳。", "嗖的一下，什么东西飞过去了？")
FG_LINES = ("主人在{label}呀，本鲸不吵你。", "{label}中……本鲸在旁边陪着。")
FOODS = ("rice", "fish", "cake", "tangyuan")
JUMP_SOUNDS = ("嘿咻！", "起跳～", "看本鲸的！")
OUCH_SOUNDS = ("哎哟！", "呜哇——", "痛痛痛……")
HUM_SOUNDS = ("哼哼～", "啦啦啦～", "嘿咻嘿咻。")
SNORE_SOUNDS = ("呼……呼……", "嗯……白饭……")


class Living:
    TICK_MS = 50

    def __init__(self, app, life_path: Path, home_dir: Path, log=print):
        self.app = app
        self.log = log
        cfg = app.config.setdefault("life", {})
        if not isinstance(cfg, dict):
            cfg = {}
            app.config["life"] = cfg
        cfg.setdefault("enabled", True)
        cfg.setdefault("gravity", True)
        cfg.setdefault("walk_speed", 70)
        cfg.setdefault("file_peeks_per_day", 3)
        cfg.setdefault("diary", True)
        self.cfg = cfg
        self.path = Path(life_path)
        self.home = Path(home_dir)
        self.state = LifeState.load(self.path, time.time())
        self.state.advance(time.time(), time.localtime().tm_hour)
        self.platforms: list = []
        self._platforms_at = 0.0
        self.platform = None
        self._platform_rect = None
        self.mode = "stand"           # stand / walk / fall / jump
        self.vx = self.vy = 0.0
        self.target_x: float | None = None
        self._jump: dict | None = None
        self.activity = "idle"
        self.next_decide = time.time() + 8
        self._last = time.monotonic()
        self._last_save = time.time()
        self._fg_label = ""
        self._fg_said = 0.0
        self._fg_checked = 0.0
        self._peeks_day = ""
        self._peeks = 0
        self._job = None
        self.foot_pad = 10 + app.pet_h * 0.025
        self._chase_until = 0.0
        self._step_phase = 0.0
        self._mouse_last = None      # (x, y, t)
        self._stare_since = 0.0
        self._stare_said = 0.0
        self._startle_said = 0.0
        self._mouse_checked = 0.0

    # ---------------------------------------------------------- 开关
    def enabled(self) -> bool:
        if self.app.config.get("free_placement", True):
            return False
        return bool(self.cfg.get("enabled", True)) and not self.app.hidden

    def start(self) -> None:
        if self._job is None:
            self._job = self.app.root.after(1500, self._tick)

    def save(self) -> None:
        try:
            self.state.advance(time.time(), time.localtime().tm_hour)
            self.state.save(self.path)
        except Exception as exc:
            self.log(f"生活状态保存失败：{exc}")

    # ---------------------------------------------------------- 坐标
    def foot(self) -> tuple[float, float]:
        root = self.app.root
        return root.winfo_x() + self.app.W / 2, root.winfo_y() + self.app.H - self.foot_pad

    def place_foot(self, fx: float, fy: float) -> None:
        x = round(fx - self.app.W / 2)
        y = round(fy - (self.app.H - self.foot_pad))
        root = self.app.root
        if (x, y) != (root.winfo_x(), root.winfo_y()):
            root.geometry(f"+{x}+{y}")

    def work_area(self) -> tuple[int, int, int, int]:
        area = self.app._work_area()
        if area:
            return area
        return 0, 0, self.app.root.winfo_screenwidth(), self.app.root.winfo_screenheight() - 48

    def refresh_platforms(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._platforms_at < 1.5:
            return
        self._platforms_at = now
        try:
            self.platforms = desktop.compute_platforms(desktop.list_windows(), self.work_area())
        except Exception as exc:
            self.log(f"看桌面失败：{exc}")
            left, top, right, bottom = self.work_area()
            self.platforms = [desktop.Platform(left, right, bottom, 0, "地面")]

    # ---------------------------------------------------------- 外部事件
    def _has_walk(self) -> bool:
        return "walk" in getattr(self.app.library, "states", {})

    def busy(self) -> bool:
        app = self.app
        return bool(app._drag or app._flight is not None or app._eating or getattr(app, "_entering", False))

    def toss(self, vx: float, vy: float) -> None:
        """被甩出去：交给这里的物理，能落在窗口上。"""
        self.mode, self.vx, self.vy = "fall", vx, vy
        self.target_x = None
        self._jump = None
        self.state.on_event("toss")

    def dropped(self) -> None:
        """被拖到半空松手：开了重力就掉下去。"""
        self.refresh_platforms(force=True)
        fx, fy = self.foot()
        if self.cfg.get("gravity", True) and platform_at(self.platforms, fx, fy, 10) is None:
            self.mode, self.vx, self.vy = "fall", 0.0, 0.0
        else:
            self.mode = "stand"

    def event(self, kind: str) -> None:
        self.state.on_event(kind)
        if kind in ("pat", "chat") or kind.startswith("fed:"):
            self.next_decide = max(self.next_decide, time.time() + 20)  # 刚被理过，先别乱跑

    # ---------------------------------------------------------- 主循环
    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self._last))
        self._last = now
        try:
            if self.enabled() and not self.busy():
                self.refresh_platforms()
                self._physics(dt)
                if time.time() >= self.next_decide and self._quiet():
                    self.decide()
                self._watch_foreground()
                self._watch_mouse()
            if time.time() - self._last_save > 60:
                self._last_save = time.time()
                self.save()
        except Exception as exc:
            self.log(f"桌面生活出错：{exc}")
        self._job = self.app.root.after(self.TICK_MS, self._tick)

    def _quiet(self) -> bool:
        app = self.app
        return (self.mode == "stand" and not app._busy and not app._speaking and not app._eating
                and app._dsh_state in ("idle", "disconnected") and time.time() - app._last_interaction > 8)

    def _physics(self, dt: float) -> None:
        fx, fy = self.foot()
        left, top, right, bottom = self.work_area()
        if self.mode == "jump" and self._jump:
            self._jump_step(dt)
            return
        if self.mode == "fall":
            prev_y = fy
            self.vy += GRAVITY * dt
            fx += self.vx * dt
            fy += self.vy * dt
            if fx < left + 20 or fx > right - 20:  # 撞屏幕边反弹
                fx = min(max(fx, left + 20), right - 20)
                self.vx = -self.vx * 0.5
            if fy < top:
                fy, self.vy = top, abs(self.vy) * 0.4
            self.vy = min(self.vy, 2500.0)  # 限速，免得一帧穿过平台
            land = landing_below(self.platforms, fx, prev_y) if self.vy > 0 else None
            if land is None and fy > bottom:  # 已经在地面以下（比如被放到任务栏下面）：直接站回地面
                land = next((q for q in self.platforms if q.hwnd == 0), None)
            if land is not None and fy >= land.y:
                fy = land.y
                if self.vy > 900:  # 摔得重：弹一下，喊一声
                    if self.vy > 1300:
                        self._sound(OUCH_SOUNDS)
                    self.vy = -self.vy * 0.3
                    self.vx *= 0.6
                else:
                    self._land(land)
            self.place_foot(fx, fy)
            return
        p = platform_at(self.platforms, fx, fy, 10)
        if p is None and self.platform is not None and self.platform.hwnd:
            rect = desktop.window_rect(self.platform.hwnd)
            if rect and self._platform_rect and rect[1] > top:
                dx, dy = rect[0] - self._platform_rect[0], rect[1] - self._platform_rect[1]
                if (dx or dy) and rect[0] <= fx + dx <= rect[2]:
                    self._platform_rect = rect  # 窗口被挪：跟着走
                    self.place_foot(fx + dx, rect[1])
                    return
        if p is None:
            if self.cfg.get("gravity", True):
                self.mode, self.vx, self.vy = "fall", 0.0, 0.0
                self.platform = None
            return
        if p != self.platform:
            self.platform = p
            self._platform_rect = desktop.window_rect(p.hwnd) if p.hwnd else None
        if abs(fy - p.y) > 0.5:
            fy = p.y
            self.place_foot(fx, fy)
        if self.mode == "walk" and time.time() < self._chase_until:  # 追鼠标：目标一直跟着鼠标走
            cur = desktop.cursor_pos()
            if cur:
                self.target_x = min(max(cur[0], p.x1 + 30), p.x2 - 30)
                self.app.animator.set_facing_left(self.target_x < fx)
        if self.mode == "walk" and self.target_x is not None:
            speed = float(self.cfg.get("walk_speed", 70)) * (2.2 if time.time() < self._chase_until else 1.0)
            step = speed * dt
            if abs(self.target_x - fx) <= step:
                fx = self.target_x
                self._arrive()
            else:
                fx += step if self.target_x > fx else -step
            if fx < p.x1 - 2 or fx > p.x2 + 2:  # 走出边缘：掉下去
                self.mode, self.vx, self.vy = "fall", (40 if fx > p.x2 else -40), 0.0
            self._step_phase += dt * 9
            hop = 0.0 if self.mode != "walk" or self._has_walk() else abs(math.sin(self._step_phase)) * 7
            self.place_foot(fx, fy - hop)  # 角色包没有走路动画：一蹦一蹦地走，看得出在动

    def _land(self, platform) -> None:
        self.mode, self.vx, self.vy = "stand", 0.0, 0.0
        self.platform = platform
        self._platform_rect = desktop.window_rect(platform.hwnd) if platform.hwnd else None
        app = self.app
        app.animator.play(app._state_for("idle"))
        app._save_position()

    # ---------------------------------------------------------- 动作
    def _play(self, *names: str, once: bool = False) -> None:
        app = self.app
        state = app.library.best(*names, "idle") if app.library.is_dfy else app._state_for(names[0])
        app.animator.play(state, once=once, then=app.library.best("idle") if once else None)

    def voice_ok(self) -> bool:
        """自己做事时出声：没静音、life.voice 没关、不在安静时段（沿用主动搭话的 quiet_hours）。"""
        if self.app.config.get("mute") or not self.cfg.get("voice", True):
            return False
        quiet = (self.app.config.get("proactive") or {}).get("quiet_hours") or [1, 8]
        try:
            start, end = int(quiet[0]), int(quiet[1])
        except (TypeError, ValueError, IndexError):
            return True
        hour = time.localtime().tm_hour
        in_quiet = (start <= hour < end) if start < end else (hour >= start or hour < end)
        return not in_quiet

    def _say(self, lines, ms: int = 3600, voice: bool = True) -> None:
        text = random.choice(lines) if isinstance(lines, (tuple, list)) else str(lines)
        if text:
            self.app.bubble.show(text, ms)
        if voice and self.voice_ok():
            self.app.voice.speak(text)

    def _sound(self, lines) -> None:
        """只出声不弹气泡的小音效（嘿咻、哎哟、哼歌），太频繁会吵，所以调用处都控制了概率。"""
        if self.voice_ok():
            self.app.voice.speak(random.choice(lines))

    def walk_to(self, target_x: float) -> None:
        fx, _ = self.foot()
        self.target_x = target_x
        self.mode = "walk"
        self.app.animator.set_facing_left(target_x < fx)
        self._play("walk", "sneak", "happy")

    def _arrive(self) -> None:
        self.mode = "stand"
        self.target_x = None
        if self.activity == "peek_edge":
            self._play("sneak", "shy")
            self._say(PEEK_LINES, 3000)
        else:
            self._play("idle")

    def jump_to(self, platform, target_x: float) -> None:
        fx, fy = self.foot()
        height = max(90.0, fy - platform.y + 80.0)
        self._jump = {"x0": fx, "y0": fy, "x1": target_x, "y1": float(platform.y), "h": height,
                      "t": 0.0, "dur": 0.9 + min(0.6, abs(target_x - fx) / 1500.0), "platform": platform}
        self.mode = "jump"
        self.app.animator.set_facing_left(target_x < fx)
        self._play("jump", "happy", once=False)
        self._sound(JUMP_SOUNDS)

    def _jump_step(self, dt: float) -> None:
        j = self._jump
        j["t"] = min(1.0, j["t"] + dt / j["dur"])
        t = j["t"]
        x = j["x0"] + (j["x1"] - j["x0"]) * t
        y = j["y0"] + (j["y1"] - j["y0"]) * t - j["h"] * 4 * t * (1 - t)
        self.place_foot(x, y)
        if t >= 1.0:
            self._jump = None
            self.refresh_platforms(force=True)
            fx, fy = self.foot()
            landed = platform_at(self.platforms, fx, fy, 12)
            if landed is None:  # 窗口在半路被挪走了：掉下去
                self.mode, self.vx, self.vy = "fall", 0.0, 0.0
            else:
                self._land(landed)
                self._say(CLIMB_LINES, 2600)

    # ---------------------------------------------------------- 决定做什么
    def decide(self) -> None:
        now = time.time()
        hour = time.localtime(now).tm_hour
        self.state.advance(now, hour)
        user_active = desktop.user_idle_seconds() < 20
        activity = self.state.choose(now, hour, user_active)
        self.do(activity)
        self.next_decide = now + random.uniform(6, 14) * (4 if activity == "nap" else 1)

    def do(self, activity: str) -> None:
        self.activity = activity
        now = time.time()
        fx, fy = self.foot()
        p = self.platform or platform_at(self.platforms, fx, fy, 10)
        left, top, right, bottom = self.work_area()
        app = self.app
        if activity == "stroll" and p is not None:
            lo, hi = p.x1 + 40, p.x2 - 40
            if hi - lo > 80:
                far = [x for x in (random.uniform(lo, hi) for _ in range(6)) if abs(x - fx) >= 160]
                self.walk_to(far[0] if far else random.uniform(lo, hi))  # 尽量走远一点，别原地挪两步
                if random.random() < 0.3:
                    self._sound(HUM_SOUNDS)
        elif activity == "climb_window":
            options = [q for q in self.platforms if q.hwnd and q.width >= 220 and q != p]
            if options:
                fg = desktop.foreground_title()
                options.sort(key=lambda q: (q.title != fg, random.random()))
                target = options[0]
                self.jump_to(target, random.uniform(target.x1 + 60, target.x2 - 60))
            else:
                activity = "stroll"
                if p is not None and p.width > 200:
                    self.walk_to(random.uniform(p.x1 + 40, p.x2 - 40))
        elif activity == "peek_edge":
            if p is not None and p.hwnd == 0:
                side_left = fx < (left + right) / 2
                self.walk_to(left + 6 if side_left else right - 6)  # 一半身子出屏幕，探头
            else:
                activity = "idle"
        elif activity == "nap":
            self._play("sleepy")
            if random.random() < 0.4:
                self._say(NAP_LINES, 3000)
            elif random.random() < 0.5:
                self._sound(SNORE_SOUNDS)
        elif activity == "snack":
            food = random.choice(FOODS)
            self._say(SNACK_LINES, 3000)
            app._start_eating(food)
        elif activity == "dance":
            self._play("dance", "happy")
            if random.random() < 0.4:
                self._say(DANCE_LINES, 2600)
        elif activity == "browse_file":
            threading.Thread(target=self._browse_worker, daemon=True).start()
        elif activity == "write_diary":
            if self.cfg.get("diary", True):
                threading.Thread(target=self._diary_worker, daemon=True).start()
        elif activity == "chase_mouse":
            cur = desktop.cursor_pos()
            if cur and p is not None and p.x1 - 50 <= cur[0] <= p.x2 + 50:
                self._chase_until = time.time() + 6
                self.walk_to(min(max(cur[0], p.x1 + 30), p.x2 - 30))
                if random.random() < 0.5:
                    self._say(CHASE_LINES, 2400)
            else:
                activity = "idle"
        elif activity == "call_master":
            self._play("wave", "happy", once=True)
            self._say(CALL_LINES, 4200)
        else:
            self._play("idle")
        self.state.mark_done(activity, now)
        self.log(f"桌面生活：{activity}（{self.state.summary()}）")

    # ---------------------------------------------------------- 看鼠标
    def _watch_mouse(self) -> None:
        """每 0.2 秒看一眼鼠标：停在她身上久了会害羞，从身边嗖地划过会吓一跳。只读位置。"""
        now = time.time()
        if now - self._mouse_checked < 0.2:
            return
        self._mouse_checked = now
        cur = desktop.cursor_pos()
        if not cur:
            return
        app = self.app
        root = app.root
        body_left = root.winfo_x() + (app.W - app.pet_w) / 2
        body_top = root.winfo_y() + app.H - app.pet_h
        on_body = (body_left <= cur[0] <= body_left + app.pet_w and body_top <= cur[1] <= body_top + app.pet_h)
        last = self._mouse_last
        self._mouse_last = (cur[0], cur[1], now)
        if on_body:
            if not self._stare_since:
                self._stare_since = now
            elif now - self._stare_since > 1.5 and now - self._stare_said > 180 and self._quiet():
                self._stare_said = now
                self._play("shy")
                self._say(STARE_LINES, 2800, voice=False)
            return
        self._stare_since = 0.0
        if last and now - last[2] < 0.5:  # 划得很快、离她很近：吓一跳
            speed = ((cur[0] - last[0]) ** 2 + (cur[1] - last[1]) ** 2) ** 0.5 / max(0.05, now - last[2])
            cx, cy = body_left + app.pet_w / 2, body_top + app.pet_h / 2
            near = ((cur[0] - cx) ** 2 + (cur[1] - cy) ** 2) ** 0.5 < app.pet_w * 0.8
            if speed > 4000 and near and now - self._startle_said > 120 and self._quiet():
                self._startle_said = now
                self._play("surprised")
                self._say(STARTLE_LINES, 2200, voice=False)

    # ---------------------------------------------------------- 看主人在干嘛
    def _watch_foreground(self) -> None:
        now = time.time()
        if now - self._fg_checked < 2:
            return
        self._fg_checked = now
        label = desktop.guess_activity(desktop.foreground_title())
        if label and label != self._fg_label:
            self._fg_label = label
            if (now - self._fg_said > 30 * 60 and desktop.user_idle_seconds() < 10
                    and self._quiet() and random.random() < 0.3):
                self._fg_said = now
                self._say(tuple(line.format(label=label) for line in FG_LINES), 3200, voice=False)  # 主人在忙，不出声

    # ---------------------------------------------------------- 翻文件 / 写日记（后台线程）
    def browse_roots(self) -> list[tuple[Path, int]]:
        """能翻的目录：默认只有项目的 docs/、topics/ 和 pet/ 顶层源码，不翻个人规则目录（life.browse_roots 可改）。"""
        project = Path(__file__).resolve().parent.parent
        custom = self.cfg.get("browse_roots")
        if isinstance(custom, list) and custom:
            return [(Path(os.path.expandvars(os.path.expanduser(str(p)))), 3) for p in custom]
        return [(project / "docs", 2), (project / "topics", 3), (project / "pet", 0)]

    def pick_recent_file(self) -> Path | None:
        found = browse_candidates(self.browse_roots(), time.time() - 3 * 86400)
        if not found:
            return None
        found.sort(reverse=True)
        return random.choice(found[:20])[1]

    def _budget_ok(self) -> bool:
        today = time.strftime("%Y-%m-%d")
        if self._peeks_day != today:
            self._peeks_day, self._peeks = today, 0
        return self._peeks < int(self.cfg.get("file_peeks_per_day", 3))

    def _browse_worker(self) -> None:
        path = self.pick_recent_file()
        if path is None:
            return
        try:
            snippet = path.read_text(encoding="utf-8", errors="ignore")[:1200]
        except OSError:
            return
        line = None
        if self._budget_ok():
            self._peeks += 1
            line = self.app.brain.remark(
                f"你在主人电脑上偷偷翻到了文件《{path.name}》，用一句话（20～40 字）嘀咕一下你看到了什么，可以吐槽或夸，别复述原文。",
                snippet)
        if not line:
            line = random.choice((f"本鲸偷看了一眼 {path.name}……字好多，看不懂但很厉害。",
                                  f"{path.name} 最近改了好几次呢，主人辛苦啦。"))
        self._append_home("看过的文件.md", f"- {time.strftime('%m-%d %H:%M')} {path.name}：{line}")
        self.app.root.after(0, lambda: (self._play("thinking", "confused"), self._say(line, 5200)))

    def _diary_worker(self) -> None:
        brain = self.app.brain
        try:
            recent = "；".join(brain.memory_store.recent_dialogue(6))[:500]
        except Exception:
            recent = ""
        material = f"今天的状态：{self.state.summary()}。最近和主人聊过：{recent or '没怎么聊'}。"
        text = brain.remark("写今天的日记：第一人称、口语、60～120 字，记一两件今天的小事和心情，结尾一句想对主人说的话。",
                            material, max_tokens=200)
        if not text:
            text = f"今天{self.state.summary()}。主人{'陪本鲸聊了天' if recent else '好像很忙'}，本鲸乖乖待在桌面上。明天也要一起努力哦。"
        day = time.strftime("%Y-%m-%d")
        self._append_home(f"diary/{day}.md", f"## {time.strftime('%H:%M')}\n\n{text}\n")
        self.app.root.after(0, lambda: self._say("本鲸写完今天的日记啦（在小窝里，不给看）。", 3600))

    def _append_home(self, rel: str, text: str) -> None:
        path = self.home / rel
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text.rstrip() + "\n")
        except OSError as exc:
            self.log(f"小窝写入失败：{exc}")
