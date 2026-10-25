# -*- coding: utf-8 -*-
"""约定与提醒：她答应「到时候提醒你」，就要真的提醒（前瞻记忆）。

- parse_when()：本地解析中文时间说法（不花 token）：今天/明天/后天、周几/下周几、几月几号、
  几点几分/几点半、上午下午晚上、N 小时后 / N 分钟后；中文数字也认。
- ReminderBook：提醒存 reminders.json（个人数据）。只有日期没有钟点的事（如「下周三补考」）
  会排三次：前一天 21:00「早点睡」、当天 08:30「今天有…」、第二天 10:00 追问「…怎么样了」。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "1": 0, "2": 1,
            "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}
_NUM = r"[0-9零〇一二两三四五六七八九十]+"
ASK_RE = re.compile(r"提醒我|叫我|喊我|记得.{0,8}(提醒|叫|喊)|别让我忘")


def cn_number(text: str) -> int | None:
    """「十二」「二十五」「两」「8」→ 整数。"""
    text = str(text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    value = 0
    for ch in text:
        if ch not in _CN_DIGITS:
            return None
        value = value * 10 + _CN_DIGITS[ch]
    return value


def parse_when(text: str, now: datetime | None = None) -> tuple[datetime | None, bool]:
    """返回 (时间, 是否带了具体钟点)。认不出返回 (None, False)。"""
    now = now or datetime.now()
    s = str(text or "")
    m = re.search(rf"({_NUM}|半)\s*个?\s*(小时|分钟)\s*(以)?后", s)
    if m:
        n = 0.5 if m.group(1) == "半" else cn_number(m.group(1))
        if n is not None:
            delta = timedelta(hours=n) if m.group(2) == "小时" else timedelta(minutes=n)
            return now + delta, True

    day = None
    if re.search(r"大后天", s):
        day = now.date() + timedelta(days=3)
    elif "后天" in s:
        day = now.date() + timedelta(days=2)
    elif "明天" in s or "明早" in s or "明晚" in s:
        day = now.date() + timedelta(days=1)
    elif "今天" in s or "今晚" in s or "今早" in s:
        day = now.date()
    m = re.search(r"(下下|下|这|本)?\s*(?:个)?(?:周|星期|礼拜)\s*([一二三四五六日天1-7])", s)
    if m and day is None:
        target = _WEEKDAY[m.group(2)]
        monday = now.date() - timedelta(days=now.weekday())
        weeks = {"下": 1, "下下": 2}.get(m.group(1) or "", 0)
        day = monday + timedelta(weeks=weeks, days=target)
        if not m.group(1) and day < now.date():  # 「周三」已经过了就是下周三
            day += timedelta(weeks=1)
    m = re.search(rf"({_NUM})\s*月\s*({_NUM})\s*[日号]", s)
    if m and day is None:
        month, dom = cn_number(m.group(1)), cn_number(m.group(2))
        if month and dom and 1 <= month <= 12 and 1 <= dom <= 31:
            try:
                day = now.replace(month=month, day=dom).date()
                if day < now.date():
                    day = day.replace(year=day.year + 1)
            except ValueError:
                day = None

    hour = minute = None
    m = re.search(rf"({_NUM})\s*[点:：时]\s*(半|({_NUM})\s*分?)?", s)
    if m:
        hour = cn_number(m.group(1))
        minute = 30 if m.group(2) == "半" else (cn_number(m.group(3)) if m.group(3) else 0)
        if hour is not None:
            afternoon = re.search(r"下午|晚上|今晚|明晚|傍晚|夜里", s)
            if afternoon and hour < 12:
                hour += 12
            elif re.search(r"中午", s) and hour < 6:
                hour += 12
            if not (0 <= hour <= 23 and 0 <= (minute or 0) <= 59):
                hour = None
    if day is None and hour is None:
        return None, False
    if day is None:  # 只有钟点：今天的这个点，过了就明天
        day = now.date()
        if (hour, minute or 0) <= (now.hour, now.minute):
            day += timedelta(days=1)
    if hour is None:
        return datetime(day.year, day.month, day.day, 9, 0), False
    return datetime(day.year, day.month, day.day, hour, minute or 0), True


def event_name(text: str) -> str:
    """从「下周三我要补考，记得提醒我」里取出事情本身（去掉时间词和「提醒我」）。"""
    s = re.sub(r"(记得|到时候|麻烦|帮我|请)?.{0,2}(提醒我|叫我|喊我|别让我忘了?)", "", str(text or ""))
    s = re.sub(rf"(下下|下|这|本)?个?(周|星期|礼拜)[一二三四五六日天1-7]|大?后天|明天|今天|今晚|明晚|明早|今早"
               rf"|{_NUM}\s*月\s*{_NUM}\s*[日号]|(上午|下午|晚上|中午|早上|傍晚)?\s*{_NUM}\s*[点:：时]\s*(半|{_NUM}\s*分?)?"
               rf"|({_NUM}|半)\s*个?\s*(小时|分钟)\s*(以)?后", "", s)
    s = re.sub(r"^[，,。\s我要得还]+|[，,。！!？?\s啊呀哦嘛吧了]+$", "", s)
    return s[:30] or "那件事"


class ReminderBook:
    def __init__(self, path, log=print):
        self.path = Path(path)
        self.log = log
        self._lock = threading.RLock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.items: list[dict] = [i for i in data.get("items", []) if isinstance(i, dict)]
        except Exception:
            self.items = []

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"items": self.items[-200:]}, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as exc:
                self.log(f"提醒保存失败：{exc}")

    def add_from_text(self, text: str, now: datetime | None = None) -> list[dict] | None:
        """主人让她提醒：解析出时间就排好提醒并返回；时间没听懂返回 None（让她去问）。"""
        when, has_clock = parse_when(text, now)
        if when is None:
            return None
        now = now or datetime.now()
        what = event_name(text)
        plans: list[tuple[datetime, str, str]] = []  # (时间, 到点说的话, 类型)
        if has_clock:
            plans.append((when, f"到点啦：{what}", "clock"))
        else:
            eve = datetime(when.year, when.month, when.day, 21, 0) - timedelta(days=1)
            if eve > now:
                plans.append((eve, f"明天要{what}，今晚早点睡，别熬夜。", "eve"))
            plans.append((datetime(when.year, when.month, when.day, 8, 30), f"今天要{what}，加油！", "day"))
            plans.append((datetime(when.year, when.month, when.day, 10, 0) + timedelta(days=1),
                          f"昨天的{what}怎么样了？", "followup"))
        added = []
        with self._lock:
            for at, message, kind in plans:
                if at <= now:
                    continue
                item = {"at": at.timestamp(), "text": message, "event": what, "kind": kind,
                        "source": str(text)[:80], "created": time.time(), "done": False}
                self.items.append(item)
                added.append(item)
            self.items.sort(key=lambda i: i["at"])
        self.save()
        return added

    def due(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        with self._lock:
            ready = [i for i in self.items if not i.get("done") and i["at"] <= now]
            for item in ready:
                item["done"] = True
        if ready:
            self.save()
        return ready

    def upcoming(self, limit: int = 3, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return [i for i in self.items if not i.get("done") and i["at"] > now][:limit]

    @staticmethod
    def describe(item: dict) -> str:
        """给她看的说明：时间 + 这一条是干什么的（不给原话，免得她把「明天要…」当成明天）。"""
        label = {"eve": "前一晚催主人早睡", "day": "当天早上提醒", "followup": "事后问结果",
                 "clock": "准点提醒"}.get(item.get("kind"), "提醒")
        return f"{time.strftime('%m-%d %H:%M', time.localtime(item['at']))} {label}"

    def summary(self, limit: int = 3) -> str:
        items = self.upcoming(limit)
        if not items:
            return ""
        return "；".join(f"{self.describe(i)}（{i.get('event', '')}）" for i in items)
