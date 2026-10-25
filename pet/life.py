# -*- coding: utf-8 -*-
"""大肥鱼的「生活」：几项需求随时间变化，她自己挑下一件事做（纯逻辑，可单测）。

不调模型、不花 token：需求衰减 + 日程 + 效用打分 + 一点随机。
状态存进 life.json，关掉再开时按离开的时长补算（最多补 12 小时），
所以她的生活是连续的：你一晚上没开电脑，第二天她会饿、会想你。

需求（0..100）：
    hunger  饿（0 饱 → 100 饿）      energy  精力（100 满 → 0 困死）
    mood    心情                     boredom 无聊
    social  想主人
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

ACTIVITIES = ("idle", "stroll", "climb_window", "peek_edge", "nap", "snack",
              "dance", "browse_file", "write_diary", "call_master")

DEFAULT_NEEDS = {"hunger": 30.0, "energy": 80.0, "mood": 70.0, "boredom": 20.0, "social": 30.0}
MAX_OFFLINE_MINUTES = 12 * 60
CALL_COOLDOWN_S = 40 * 60       # 喊主人：至少隔 40 分钟
BROWSE_COOLDOWN_S = 25 * 60     # 翻文件：至少隔 25 分钟
SNACK_COOLDOWN_S = 20 * 60      # 偷吃零食：至少隔 20 分钟

# 每分钟变化量（醒着）
RATES_AWAKE = {"hunger": 0.12, "energy": -0.09, "boredom": 0.35, "social": 0.10}
# 睡着：精力回、饿得慢、不无聊
RATES_ASLEEP = {"hunger": 0.04, "energy": 0.6, "boredom": -0.5, "social": 0.03}

EVENTS = {
    "pat": {"mood": 8, "social": -15, "boredom": -10},
    "chat": {"mood": 4, "social": -20, "boredom": -15},
    "toss": {"mood": 3, "boredom": -20, "energy": -3},
    "snack": {"hunger": -25, "mood": 4},
}
FOOD_FILL = {"rice": 35, "fish": 20, "cake": 25, "tangyuan": 20, "diamond": 5}


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def is_night(hour: int) -> bool:
    return hour >= 22 or hour < 7


def near_meal(hour: int) -> bool:
    return hour in (6, 7, 11, 12, 17, 18)


class LifeState:
    def __init__(self, needs: dict | None = None, last_update: float | None = None, extra: dict | None = None):
        self.needs = dict(DEFAULT_NEEDS)
        for key, value in (needs or {}).items():
            if key in self.needs:
                try:
                    self.needs[key] = _clamp(value)
                except (TypeError, ValueError):
                    pass
        self.last_update = float(last_update or time.time())
        extra = extra or {}
        self.last_done: dict[str, float] = {k: float(v) for k, v in (extra.get("last_done") or {}).items()
                                            if isinstance(v, (int, float))}
        self.diary_day: str = str(extra.get("diary_day") or "")
        self.recent: list[str] = [a for a in (extra.get("recent") or []) if a in ACTIVITIES][-4:]
        self.asleep: bool = bool(extra.get("asleep", False))

    # ---------- 存取 ----------
    @classmethod
    def load(cls, path, now: float) -> "LifeState":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a dict")
            state = cls(data.get("needs"), data.get("last_update") or now, data)
        except Exception:
            return cls(None, now)
        if state.last_update > now:  # 时钟回拨之类，别补负数
            state.last_update = now
        return state

    def save(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"needs": {k: round(v, 2) for k, v in self.needs.items()}, "last_update": self.last_update,
                "last_done": self.last_done, "diary_day": self.diary_day, "recent": self.recent,
                "asleep": self.asleep}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    # ---------- 时间流逝 ----------
    def advance(self, now: float, hour: int, asleep: bool | None = None) -> float:
        """按流逝的分钟数更新需求；返回实际补算的分钟数（封顶 12 小时）。"""
        if asleep is not None:
            self.asleep = bool(asleep)
        minutes = max(0.0, (now - self.last_update) / 60.0)
        minutes = min(minutes, MAX_OFFLINE_MINUTES)
        self.last_update = now
        if minutes <= 0:
            return 0.0
        rates = dict(RATES_ASLEEP if self.asleep else RATES_AWAKE)
        if near_meal(hour) and not self.asleep:
            rates["hunger"] *= 2.0
        if is_night(hour) and not self.asleep:
            rates["energy"] *= 2.0
        for key, rate in rates.items():
            self.needs[key] = _clamp(self.needs[key] + rate * minutes)
        # 心情慢慢被饿、困、无聊、想主人拖着走
        drag = (max(0, self.needs["hunger"] - 60) + max(0, 40 - self.needs["energy"])
                + max(0, self.needs["boredom"] - 60) + max(0, self.needs["social"] - 60)) / 100.0
        self.needs["mood"] = _clamp(self.needs["mood"] + (0.05 - 0.4 * drag) * minutes)
        return minutes

    def on_event(self, kind: str) -> None:
        kind = str(kind or "")
        if kind.startswith("fed:"):
            fill = FOOD_FILL.get(kind.split(":", 1)[1], 20)
            self._apply({"hunger": -fill, "mood": 6, "social": -8})
            return
        if kind in EVENTS:
            self._apply(EVENTS[kind])
        if kind in ("pat", "chat", "toss") and self.asleep:
            self.asleep = False  # 被弄醒了

    def _apply(self, delta: dict) -> None:
        for key, value in delta.items():
            self.needs[key] = _clamp(self.needs[key] + value)

    def mark_done(self, activity: str, now: float, day: str = "") -> None:
        """app 真的做完/开始做某件事后调用：记冷却、记最近、日记记日期。"""
        self.last_done[activity] = now
        self.recent = (self.recent + [activity])[-4:]
        if activity == "write_diary":
            self.diary_day = day or time.strftime("%Y-%m-%d", time.localtime(now))
        if activity == "snack":
            self.on_event("snack")
        if activity == "nap":
            self.asleep = True
        elif activity != "idle":
            self.asleep = False
        if activity in ("stroll", "climb_window", "peek_edge", "dance", "browse_file"):
            self._apply({"boredom": -25, "energy": -2})
        if activity == "call_master":
            self._apply({"social": -5})

    # ---------- 挑事做 ----------
    def scores(self, now: float, hour: int, user_active: bool) -> dict[str, float]:
        n = self.needs
        night = is_night(hour)
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        s = {
            "idle": 18.0,
            "stroll": 10.0 + n["boredom"] * 0.35,
            "climb_window": n["boredom"] * 0.45,
            "peek_edge": n["boredom"] * 0.30,
            "nap": max(0.0, 70 - n["energy"]) * 1.2 + (35 if night else 0),
            "snack": max(0.0, n["hunger"] - 45) * 1.3,
            "dance": max(0.0, n["mood"] - 60) * 0.5 + n["boredom"] * 0.15,
            "browse_file": n["boredom"] * 0.35,
            "write_diary": 0.0,
            "call_master": max(0.0, n["social"] - 55) * 1.4,
        }
        if 20 <= hour <= 23 and self.diary_day != today:
            s["write_diary"] = 45.0
        cooldowns = {"call_master": CALL_COOLDOWN_S, "browse_file": BROWSE_COOLDOWN_S, "snack": SNACK_COOLDOWN_S}
        for activity, cooldown in cooldowns.items():
            if now - self.last_done.get(activity, 0.0) < cooldown:
                s[activity] = 0.0
        if night:
            for activity in ("dance", "climb_window", "peek_edge", "call_master"):
                s[activity] *= 0.3
        if self.asleep and n["energy"] < 90:
            s = {k: (v if k in ("nap", "idle") else v * 0.1) for k, v in s.items()}
            s["nap"] += 40
        if user_active:  # 主人在忙：少折腾、不叫人、不翻文件
            for activity in ("climb_window", "peek_edge", "dance", "browse_file", "call_master", "write_diary"):
                s[activity] = 0.0
            s["idle"] += 15
        for back, activity in enumerate(reversed(self.recent)):  # 刚做过的降权
            if activity in s and activity != "idle":
                s[activity] *= 0.35 + 0.15 * back
        return {k: max(0.0, v) for k, v in s.items()}

    def choose(self, now: float, hour: int, user_active: bool, rng: random.Random | None = None) -> str:
        rng = rng or random
        scores = self.scores(now, hour, user_active)
        # 效用加权随机：分数平方让「最想做的」明显占优，但不是每次都一样
        weights = [(activity, scores[activity] ** 2) for activity in ACTIVITIES]
        total = sum(w for _, w in weights)
        if total <= 0:
            return "idle"
        pick = rng.random() * total
        for activity, weight in weights:
            pick -= weight
            if pick <= 0:
                return activity
        return "idle"

    # ---------- 给人看 / 给模型看 ----------
    def mood_word(self) -> str:
        n = self.needs
        if n["hunger"] >= 70:
            return "饿了"
        if n["energy"] <= 25 or self.asleep:
            return "困了"
        if n["social"] >= 70:
            return "想主人"
        if n["boredom"] >= 70:
            return "无聊"
        if n["mood"] >= 70:
            return "开心"
        return "还行"

    def summary(self) -> str:
        n = self.needs
        parts = []
        parts.append("很饿" if n["hunger"] >= 75 else "有点饿" if n["hunger"] >= 50 else "不饿")
        parts.append("困得不行" if n["energy"] <= 20 else "有点困" if n["energy"] <= 45 else "精神不错")
        if n["social"] >= 60:
            parts.append("很想主人" if n["social"] >= 80 else "有点想主人")
        if n["boredom"] >= 65:
            parts.append("闲得发慌")
        parts.append("心情好" if n["mood"] >= 70 else "心情一般" if n["mood"] >= 40 else "心情低落")
        return "，".join(parts)[:40]
