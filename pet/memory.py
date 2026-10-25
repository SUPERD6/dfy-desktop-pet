# -*- coding: utf-8 -*-
"""大肥鱼的结构化长期记忆（零第三方依赖）。

数据文件：``pet/memory.json``，v2 结构示例::

    {
      "version": 2,
      "user_name": "小明",
      "facts": [
        {"kind": "like", "text": "喜欢白饭", "time": 1710000000.0,
         "ts": "10-25 11:00"}
      ],
      "history": [
        {"role": "user", "content": "你好", "time": 1710000000.0},
        {"role": "assistant", "content": "在的在的！", "time": 1710000001.0}
      ],
      "proactive_recent": ["主动搭过的一句话"],
      "stats": {"created": 1710000000.0, "updated": 1710000001.0}
    }

兼容性：旧版 memory.json 里 ``facts`` 是字符串列表、``history`` 缺字段、
没有 ``version`` 时，读取会自动自愈并升级为 v2，同时保留未知字段，不丢旧数据。

相关性检索不用向量、不联网：中文按字符 2-gram、英文按单词切词，做集合重合打分。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

# ---- 容量与默认值 ---------------------------------------------------------
HISTORY_LIMIT = 60          # 对话历史最多保留多少条（user/assistant 各算一条）
FACTS_LIMIT = 120           # 长期事实最多保留多少条（超了淘汰最旧的）
PROACTIVE_LIMIT = 20        # 主动搭话去重窗口
RECENT_HISTORY = 8          # system prompt 注入的最近对话条数
RELEVANT_LIMIT = 5          # system prompt 注入的最相关记忆条数
CONSOLIDATE_AT = 40         # 对话攒到这么多条，就把最旧的一批压成日记（早于 HISTORY_LIMIT，旧话不会被直接丢）
CONSOLIDATE_BATCH = 20      # 每次压缩多少条
EPISODES_LIMIT = 200        # 日记最多留多少条，超了把最旧的几条并成一条
EPISODE_MERGE = 8

# 常见的单字虚词：参与打分容易误命中，单字 token 直接忽略（2-gram 仍保留一部分）
_STOP_CHARS = set("我你他她它的了是在有就都也还和与跟对把被这那一个不没很太要会能吗呢吧啊哦呀嘛么什怎为何如")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RE = re.compile(r"[a-z0-9_]+")
# 去掉句末标点，让「喜欢白饭。」和「喜欢白饭」算同一条
_TRAIL_PUNCT_RE = re.compile(r"[\s。.!！?？,，、;；:：~～…]+$")

_KIND_LABEL = {
    "like": "喜欢",
    "dislike": "讨厌",
    "event": "重要的事",
    "note": "记得",
}


def _now(now: float | None = None) -> float:
    """返回时间戳；便于测试注入固定时间。"""
    return float(now) if now is not None else time.time()


def format_time(ts: float) -> str:
    """时间戳 → 「10-25 11:00」这种短格式，给 prompt / summary 用。"""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "未知时间"


def normalize_text(text: str) -> str:
    """归一化：去首尾空白与句末标点、压缩空白，用于事实去重。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return _TRAIL_PUNCT_RE.sub("", text)


def tokenize(text: str) -> set[str]:
    """关键词/字符 2-gram 切词（标准库实现，够用且快）。"""
    text = str(text or "").lower()
    tokens = set(_ASCII_RE.findall(text))
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            if run not in _STOP_CHARS:
                tokens.add(run)
            continue
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            # 两个都是虚词的双字组合（「的了吗」之类）没有区分度，跳过
            if bigram[0] in _STOP_CHARS and bigram[1] in _STOP_CHARS:
                continue
            tokens.add(bigram)
    return tokens


def relevance_score(query_tokens: set[str], item_tokens: set[str]) -> float:
    """集合重合打分：双字权重 2、单字 0.5，再按条目长度开方归一。"""
    if not query_tokens or not item_tokens:
        return 0.0
    overlap = query_tokens & item_tokens
    if not overlap:
        return 0.0
    score = sum(2.0 if len(token) > 1 else 0.5 for token in overlap)
    return score / (len(item_tokens) ** 0.5)


def similarity(a: set[str], b: set[str]) -> float:
    """「是不是同一件事换了个说法」：短的那句的双字词，有多少落在长的那句里（覆盖率）。

    不用 Jaccard：「主人喜欢猫 / 主人喜欢狗」的 Jaccard 有 0.6，会被误合并；覆盖率只有 0.75。
    「喜欢喝冰美式 / 喜欢喝冰美式咖啡」覆盖率 1.0，才算同一件事。
    """
    a2 = {t for t in a if len(t) > 1}
    b2 = {t for t in b if len(t) > 1}
    if not a2 or not b2:
        return 0.0
    return len(a2 & b2) / min(len(a2), len(b2))


def fact_keep_score(fact: dict, now: float) -> float:
    """淘汰时的保留分：重要度权重最大，其次常被用到，再其次新近（半衰期约 30 天）。"""
    importance = _as_float(fact.get("importance"), 1)
    hits = _as_float(fact.get("hits"), 0)
    age_days = max(0.0, (now - _as_float(fact.get("time"), now)) / 86400.0)
    return importance * 2.0 + min(hits, 20) ** 0.5 + 2.0 * 0.5 ** (age_days / 30.0)


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class MemoryStore:
    """memory.json 的读写门面：结构化事实 + 对话历史 + 主动搭话去重。"""

    def __init__(self, path: Path | str, log=print):
        self.path = Path(path)
        self.log = log
        self._lock = threading.RLock()
        # data 是「原始 dict」，Brain 会把它直接暴露给 app.py（兼容旧代码逐键访问）
        self.data: dict = {}
        self.dirty = False
        self._load()

    # ---------- 读取与自愈 ----------
    def _read_json(self, path: Path) -> dict | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else None
        except FileNotFoundError:
            return None
        except Exception as exc:  # 文件半截 / 编码问题 / 不是 JSON
            self.log(f"记忆文件读取失败（{path.name}）：{exc}")
            return None

    def _load(self) -> None:
        raw = self._read_json(self.path)
        needs_save = False
        if raw is None and self.path.exists():
            # 主文件坏了就试试上一次的 .bak（原子写盘时留下的）
            backup_raw = self._read_json(self._backup_path())
            if backup_raw is not None:
                raw = backup_raw
                needs_save = True
                self.log("记忆文件已损坏，已从 memory.json.bak 恢复。")
            else:
                raw = {}
                self._preserve_corrupt_file()
                self.log("记忆文件已损坏且没有可用备份，坏文件已另存为 memory.json.corrupt。")
        elif raw is not None and raw.get("version") != 2:
            needs_save = True  # 旧格式：迁移后立即落盘，避免每次都迁移
        raw = raw or {}
        self.data = self._migrate(raw)
        self.dirty = needs_save
        if needs_save:
            self.save()

    def _preserve_corrupt_file(self) -> None:
        """把半截/乱码的 memory.json 挪到 .corrupt，别直接覆盖掉。"""
        target = self.path.with_name(self.path.name + ".corrupt")
        try:
            os.replace(self.path, target)
        except Exception:
            pass

    def _migrate(self, raw: dict) -> dict:
        """旧格式 → v2；任何字段读不出来都用默认值自愈，绝不抛出。"""
        data: dict = {
            "version": 3,
            "user_name": "",
            "facts": [],
            "history": [],
            "episodes": [],
            "proactive_recent": [],
            "stats": {},
        }
        # 未知字段原样保留（向前兼容：以后加的字段不会被旧版代码抹掉）
        for key, value in raw.items():
            if key not in data and key != "version":
                data[key] = value

        user_name = raw.get("user_name")
        if isinstance(user_name, str):
            data["user_name"] = user_name.strip()

        # 事实：旧版是字符串；新版是带 kind/time 的 dict。统一去重并截断。
        facts: list[dict] = []
        seen: set[str] = set()
        raw_facts = raw.get("facts")
        for item in raw_facts if isinstance(raw_facts, list) else []:
            if isinstance(item, str):
                text, kind, ts = item, "note", 0.0
            elif isinstance(item, dict):
                text = item.get("text") or item.get("fact") or ""
                kind = str(item.get("kind") or "note")
                ts = _as_float(item.get("time"), 0.0)
            else:
                continue
            text = normalize_text(text)
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            fact = {
                "kind": kind if kind in _KIND_LABEL else "note",
                "text": text[:80],
                "time": ts,
                "ts": format_time(ts) if ts else "",
            }
            if isinstance(item, dict):  # v3 字段：命中次数、重要度（旧数据没有就用默认）
                fact["hits"] = int(_as_float(item.get("hits"), 0))
                fact["importance"] = int(min(3, max(1, _as_float(item.get("importance"), 1))))
            facts.append(fact)
        data["facts"] = facts[-FACTS_LIMIT:]

        # 情节记忆（日记）：旧对话压缩出来的，一条一句话
        episodes: list[dict] = []
        raw_episodes = raw.get("episodes")
        for item in raw_episodes if isinstance(raw_episodes, list) else []:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                episodes.append({
                    "text": str(item["text"]).strip()[:200],
                    "time": _as_float(item.get("time"), 0.0),
                    "start": _as_float(item.get("start"), 0.0),
                    "end": _as_float(item.get("end"), 0.0),
                })
        data["episodes"] = episodes

        # 历史：只认 user/assistant 的文本内容，其它角色/坏行直接跳过
        history: list[dict] = []
        raw_history = raw.get("history")
        for item in raw_history if isinstance(raw_history, list) else []:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            content = content.strip()
            if not content:
                continue
            history.append({
                "role": role,
                "content": content[:600],
                "time": _as_float(item.get("time"), 0.0),
            })
        data["history"] = history[-HISTORY_LIMIT:]

        recent = raw.get("proactive_recent")
        if isinstance(recent, list):
            data["proactive_recent"] = [str(x)[:120] for x in recent if str(x).strip()][-PROACTIVE_LIMIT:]

        stats = raw.get("stats")
        if isinstance(stats, dict):
            data["stats"] = stats
        data["stats"].setdefault("created", _now())
        data["stats"]["updated"] = _now()
        return data

    # ---------- 写盘（原子） ----------
    def _backup_path(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    def save(self) -> bool:
        """临时文件 + os.replace 原子替换；旧文件留一份 .bak 自救。"""
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.data.setdefault("version", 2)
                self.data.setdefault("stats", {})["updated"] = _now()
                payload = json.dumps(self.data, ensure_ascii=False, indent=2)
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(payload, encoding="utf-8")
                if self.path.exists():
                    try:
                        self._backup_path().write_bytes(self.path.read_bytes())
                    except Exception:
                        pass  # 备份失败不影响主流程
                os.replace(tmp, self.path)
                self.dirty = False
                return True
            except Exception as exc:
                self.log(f"记忆保存失败：{exc}")
                return False

    # ---------- 基础访问 ----------
    @property
    def user_name(self) -> str:
        value = self.data.get("user_name")
        return value.strip() if isinstance(value, str) else ""

    @user_name.setter
    def user_name(self, value: str) -> None:
        with self._lock:
            self.data["user_name"] = normalize_text(value)[:20]

    @property
    def facts(self) -> list[dict]:
        value = self.data.get("facts")
        return value if isinstance(value, list) else []

    @property
    def history(self) -> list[dict]:
        value = self.data.get("history")
        return value if isinstance(value, list) else []

    @property
    def proactive_recent(self) -> list[str]:
        value = self.data.get("proactive_recent")
        return value if isinstance(value, list) else []

    # ---------- 写入 ----------
    def add_fact(self, text: str, kind: str = "note", when: float | None = None, importance: int = 1) -> bool:
        """添加一条事实。

        完全重复 → 不加；和已有事实是同一件事（短句双字词覆盖率 ≥ 0.85）→ 用新说法更新那条（时间刷新、重要度取大），
        返回 False；满了按「重要度 + 命中次数 + 新近」综合分淘汰最不重要的，而不是最旧的。
        """
        text = normalize_text(text)[:80]
        if not text:
            return False
        kind = kind if kind in _KIND_LABEL else "note"
        importance = int(min(3, max(1, importance)))
        ts = _now(when)
        with self._lock:
            new_tokens = tokenize(text)
            for item in self.facts:
                old = normalize_text(item.get("text", ""))
                if old.lower() == text.lower():
                    item["importance"] = max(int(item.get("importance", 1)), importance)
                    return False
                if similarity(new_tokens, tokenize(old)) >= 0.85:
                    item.update(text=text, kind=kind, time=ts, ts=format_time(ts),
                                importance=max(int(item.get("importance", 1)), importance))
                    self.dirty = True
                    return False
            self.facts.append({"kind": kind, "text": text, "time": ts, "ts": format_time(ts),
                               "hits": 0, "importance": importance})
            if len(self.facts) > FACTS_LIMIT:
                now = _now()
                self.facts.sort(key=lambda f: fact_keep_score(f, now))
                del self.facts[: len(self.facts) - FACTS_LIMIT]
                self.facts.sort(key=lambda f: _as_float(f.get("time"), 0.0))
            self.dirty = True
            return True

    def mark_used(self, texts) -> None:
        """这些事实被放进了 prompt：命中次数 +1（常被用到的记得更牢）。"""
        wanted = {normalize_text(t).lower() for t in texts}
        with self._lock:
            for item in self.facts:
                if normalize_text(item.get("text", "")).lower() in wanted:
                    item["hits"] = int(item.get("hits", 0)) + 1
                    self.dirty = True

    # ---------- 情节记忆：旧对话压成日记 ----------
    @property
    def episodes(self) -> list[dict]:
        return self.data.setdefault("episodes", [])

    def needs_consolidation(self) -> bool:
        return len(self.history) >= CONSOLIDATE_AT

    def pop_oldest(self, count: int = CONSOLIDATE_BATCH) -> list[dict]:
        """取走最旧的 count 条对话（交给压缩）；调用方必须随后 add_episode，不然就丢了。"""
        with self._lock:
            taken = self.history[:count]
            del self.history[:count]
            if taken:
                self.dirty = True
            return taken

    def add_episode(self, text: str, start: float = 0.0, end: float = 0.0) -> None:
        text = re.sub(r"\s+", " ", str(text or "")).strip()[:200]
        if not text:
            return
        with self._lock:
            self.episodes.append({"text": text, "time": _now(), "start": float(start), "end": float(end)})
            while len(self.episodes) > EPISODES_LIMIT:  # 太多了：最旧的 8 条并成一条「很久以前」
                old = self.episodes[:EPISODE_MERGE]
                merged = "；".join(e["text"][:30] for e in old)[:200]
                first = old[0].get("start") or old[0].get("time") or 0.0
                last = old[-1].get("end") or old[-1].get("time") or 0.0
                del self.episodes[:EPISODE_MERGE]
                self.episodes.insert(0, {"text": f"（很久以前）{merged}", "time": _now(),
                                         "start": first, "end": last})
            self.dirty = True

    def relevant_episodes(self, query: str, limit: int = 2) -> list[dict]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scored = [(relevance_score(tokens, tokenize(e.get("text", ""))), e) for e in self.episodes]
        scored = [row for row in scored if row[0] > 0]
        scored.sort(key=lambda row: (row[0], _as_float(row[1].get("time"))), reverse=True)
        return [e for _, e in scored[:limit]]

    def add_turn(self, role: str, content: str, when: float | None = None) -> None:
        """追加一条对话历史（只保留最近 HISTORY_LIMIT 条）。"""
        if role not in ("user", "assistant"):
            return
        content = str(content or "").strip()
        if not content:
            return
        with self._lock:
            self.history.append({"role": role, "content": content[:600], "time": _now(when)})
            del self.history[:-HISTORY_LIMIT]
            self.dirty = True

    def remember_exchange(self, user_text: str, answer: str, when: float | None = None) -> None:
        """一问一答一起入历史。"""
        self.add_turn("user", user_text, when)
        self.add_turn("assistant", answer, when)

    def add_proactive(self, text: str) -> None:
        """记下主动搭话，最近 PROACTIVE_LIMIT 条内尽量不重复。

        注意：这里保留原句标点（不调用 normalize_text），否则「早呀。」和
        「早呀」会被误判成两句不同的台词，去重就失效了。
        """
        text = re.sub(r"\s+", " ", str(text or "")).strip()[:120]
        if not text:
            return
        with self._lock:
            recent = [item for item in self.proactive_recent if item != text]
            recent.append(text)
            self.data["proactive_recent"] = recent[-PROACTIVE_LIMIT:]
            self.dirty = True

    # ---------- 检索 ----------
    def recent_dialogue(self, limit: int = RECENT_HISTORY) -> list[str]:
        """最近若干条对话，格式化成「用户：…」「大肥鱼：…」。"""
        lines = []
        for item in self.history[-limit:]:
            who = "用户" if item.get("role") == "user" else "大肥鱼"
            content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
            lines.append(f"{who}：{content[:80]}")
        return lines

    def relevant(self, query: str, limit: int = RELEVANT_LIMIT) -> list[dict]:
        """按当前输入检索最相关的事实/历史，返回 [{"type","text","detail","time"}]。"""
        limit = max(0, int(limit))
        if not limit:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scored: list[tuple[float, float, dict]] = []
        for fact in self.facts:
            score = relevance_score(query_tokens, tokenize(fact.get("text", "")))
            if score > 0:
                label = _KIND_LABEL.get(fact.get("kind"), "记得")
                scored.append((score, _as_float(fact.get("time"), 0.0), {
                    "type": "fact",
                    "text": fact.get("text", ""),
                    "detail": f"[{label}] {fact.get('text', '')}",
                    "time": _as_float(fact.get("time"), 0.0),
                }))
        # 历史只取用户说过的话（助手的台词容易自己复读自己，噪声大）
        for item in self.history:
            if item.get("role") != "user":
                continue
            content = str(item.get("content") or "")
            score = relevance_score(query_tokens, tokenize(content))
            if score > 0:
                scored.append((score - 0.5, _as_float(item.get("time"), 0.0), {  # 历史略降权
                    "type": "history",
                    "text": content,
                    "detail": f"[以前聊过] {content[:60]}",
                    "time": _as_float(item.get("time"), 0.0),
                }))

        scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        result = []
        seen_text: set[str] = set()
        for _score, _time, item in scored:
            key = item["text"].lower()
            if key in seen_text:
                continue
            seen_text.add(key)
            result.append(item)
            if len(result) >= limit:
                break
        return result

    def relevant_lines(self, query: str, limit: int = RELEVANT_LIMIT) -> list[str]:
        """检索结果转成给 prompt 用的短句。"""
        return [item["detail"] for item in self.relevant(query, limit)]

    # ---------- 摘要 ----------
    def summary(self) -> str:
        """给设置界面显示的「记忆条数」摘要。"""
        facts = len(self.facts)
        turns = len(self.history)
        parts = []
        if facts or turns:
            parts.append(f"{facts} 条事实")
            parts.append(f"{turns} 条对话")
        else:
            parts.append("还没有聊天记忆")
        name = self.user_name
        parts.append("称呼：" + (name or "未设置"))
        if self.history:
            last_time = _as_float(self.history[-1].get("time"), 0.0)
            if last_time:
                parts.append("上次聊天：" + format_time(last_time))
        return " · ".join(parts)

    def summary_dict(self) -> dict:
        """结构化摘要，方便以后设置窗做更细的展示。"""
        return {
            "user_name": self.user_name,
            "facts": len(self.facts),
            "turns": len(self.history),
            "proactive_recent": len(self.proactive_recent),
            "last_chat": _as_float(self.history[-1].get("time"), 0.0) if self.history else 0.0,
        }
