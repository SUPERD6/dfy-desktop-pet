# -*- coding: utf-8 -*-
"""她吃的是 token：每次调模型的真实用量就是她的饭量，余额是她的饭碗。

- eat()：从接口返回的 usage 里记下这一口吃了多少（缓存命中的单独记，那是「剩饭热一热」，便宜）。
- 零食额度：她自己做事（翻文件嘀咕、写日记、主动搭话、整理记忆）花的 token 从额度里扣；
  每天早上发一份，主人投喂再加。额度见底她就只做不花钱的事，饿了会喊。
  主人主动找她聊天（chat / agent）不受额度限制，但同样算她吃饭。
- 账本存 appetite.json（本机个人数据，不进 Git）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

# 她自己发起的、要从零食额度里扣的用途
AUTONOMOUS = {"remark", "diary", "proactive", "memory"}
# 投喂 = 给零食额度（token）
FOOD_TOKENS = {"rice": 1500, "fish": 800, "cake": 2000, "tangyuan": 1000, "diamond": 8000}
DAILY_SNACK = 4000


def _today(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now or time.time()))


def usage_tokens(usage) -> tuple[int, int]:
    """接口 usage → (总 token, 缓存命中 token)。DeepSeek 有 prompt_cache_hit_tokens；OpenAI 风格在 prompt_tokens_details。"""
    if not isinstance(usage, dict):
        return 0, 0
    try:
        total = int(usage.get("total_tokens") or (int(usage.get("prompt_tokens") or 0)
                                                   + int(usage.get("completion_tokens") or 0)))
    except (TypeError, ValueError):
        total = 0
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") if isinstance(
            usage.get("prompt_tokens_details"), dict) else 0
    try:
        hit = int(hit or 0)
    except (TypeError, ValueError):
        hit = 0
    return max(0, total), max(0, min(hit, total))


class Appetite:
    def __init__(self, path, daily_snack: int = DAILY_SNACK, log=print):
        self.path = Path(path)
        self.daily_snack = int(daily_snack)
        self.log = log
        self._lock = threading.RLock()
        self.listeners: list = []  # 每吃一口回调 (tokens, purpose)，app 用来让她「饱」一点
        self.data = self._load()
        self.rollover()

    # ---------- 存取 ----------
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as exc:
                self.log(f"饭量账本保存失败：{exc}")

    def rollover(self, now: float | None = None) -> None:
        """跨天：昨天的饭量进历史，发今天的零食额度（剩的额度不清零，但至少补到每日份）。"""
        day = _today(now)
        with self._lock:
            if self.data.get("day") == day:
                return
            if self.data.get("day"):
                history = self.data.setdefault("history", [])
                history.append({"day": self.data["day"], "eaten": self.eaten_today(), "hit": self.data.get("hit", 0)})
                del history[:-30]
            self.data.update(day=day, eaten={}, hit=0,
                             allowance=max(int(self.data.get("allowance", 0)), self.daily_snack))
            self.save()

    # ---------- 吃 ----------
    def eat(self, usage, purpose: str = "chat") -> int:
        total, hit = usage_tokens(usage)
        if total <= 0:
            return 0
        self.rollover()
        with self._lock:
            eaten = self.data.setdefault("eaten", {})
            eaten[purpose] = int(eaten.get(purpose, 0)) + total
            self.data["hit"] = int(self.data.get("hit", 0)) + hit
            if purpose in AUTONOMOUS:
                self.data["allowance"] = max(0, int(self.data.get("allowance", 0)) - total)
            self.save()
        for callback in list(self.listeners):
            try:
                callback(total, purpose)
            except Exception:
                pass
        return total

    def can_afford(self, purpose: str, estimate: int = 600) -> bool:
        """她自己想花 token 前问一句：零食额度够不够。主人找她的不拦。"""
        if purpose not in AUTONOMOUS:
            return True
        self.rollover()
        return int(self.data.get("allowance", 0)) >= estimate

    def feed(self, food: str) -> int:
        """主人投喂 = 给零食额度。返回加了多少 token。"""
        grant = FOOD_TOKENS.get(food, 1000)
        self.rollover()
        with self._lock:
            self.data["allowance"] = int(self.data.get("allowance", 0)) + grant
            self.data["fed_total"] = int(self.data.get("fed_total", 0)) + grant
            self.save()
        return grant

    # ---------- 看 ----------
    def eaten_today(self) -> int:
        return sum(int(v) for v in (self.data.get("eaten") or {}).values())

    @property
    def allowance(self) -> int:
        return int(self.data.get("allowance", 0))

    def hungry_for_tokens(self) -> bool:
        return self.allowance < 600

    def summary(self) -> str:
        eaten = self.eaten_today()
        hit = int(self.data.get("hit", 0))
        rate = f"，其中 {hit * 100 // eaten}% 是缓存热的剩饭" if eaten and hit else ""
        return f"今天吃了 {fmt(eaten)} token{rate}；零食额度还剩 {fmt(self.allowance)}"


def fmt(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)
