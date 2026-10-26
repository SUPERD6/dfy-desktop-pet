# -*- coding: utf-8 -*-
"""只读接入用户的「个人库」，把背景/偏好/长期记忆压缩成桌宠能用的小块文本。

设计原则（务必保持）：

- **只读**：本模块永远不写个人库里的任何文件，也不会把个人库正文打印到日志。
- **优雅降级**：个人库不存在、只有部分文件、某一行 JSON 坏掉时都不抛异常，
  只给出人话 ``error``，其余功能照常跑。
- **短**：注入 system prompt 的 ``prompt_block`` 有字符上限（默认 1600），
  人格提示里只放「背景/偏好/最近 3 条 preference 记忆」，技术坑（topic=pitfall）
  一律不进提示，避免把一次性排错经验当成用户人设。

个人库结构（路径由 config.personal.library_path 指定，默认 ``~/.dfy/personal``）::

    context/profile.md                      # 姓名/学校/目标/合作偏好
    给Claude导入的个人背景-精简版.md          # 经历与偏好
    preferences.json                        # 语言/推理强度等偏好
    memory/journal.jsonl                    # 跨 agent 长期记忆，一行一条 JSON
                                            # 字段 seq/ts/by/topic/text/source
    memory/journal-meta.jsonl               # 元数据，忽略

对外的接口（app.py / brain.py / agent.py 会用到）：

- ``personal.is_available`` / ``personal.error`` / ``personal.loaded_at``
- ``personal.reload(force=False)``
- ``personal.prompt_block(query="")``
- ``personal.quick_recap()``
- ``personal.find(query, limit=5)``
- ``personal.summary()``
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

# 默认个人库目录（真实用户机器上的位置；测试里一律传 path 覆盖）
# 没配置 library_path 时找这里；目录不存在只会提示，不影响聊天
DEFAULT_LIBRARY_PATH = Path.home() / ".dfy" / "personal"

PROFILE_REL = "context/profile.md"
BACKGROUND_REL = "给Claude导入的个人背景-精简版.md"
PREFERENCES_REL = "preferences.json"
JOURNAL_REL = "memory/journal.jsonl"

# 单个文件最多读这么多字节：个人库不大，但防止误指到大文件把桌宠卡住
MAX_FILE_BYTES = 512 * 1024
# journal 最多保留最近这么多条到内存（真实环境只有个位数）
MAX_JOURNAL_ENTRIES = 500

PROMPT_TAIL = "以上是背景参考：不要逐字复述，不确定的信息不要编造，也不要补充没看到过的内容。"
PROMPT_HEADER = "【用户个人档案（来自本地个人库，只读，仅作背景参考）】"

# prompt_block 里最多放几条 preference 记忆（需求：最近 3 条）
RECENT_PREFERENCE_COUNT = 3

# 中文/英文混合检索时，太常见的虚词不单独算分
_STOP_2GRAMS = {
    "用户", "我们", "他们", "这个", "那个", "就是", "可以", "需要", "不要",
    "一个", "什么", "因为", "所以", "如果", "然后", "已经", "还是", "不是",
    "没有", "自己", "现在", "这些", "那些", "这样", "那样",
}

_ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+#.\-]{1,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s+")
_NAME_RE = re.compile(
    r"^(?:[-*+]\s*)?([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,2})[。，,；;:：\s]"
)
_NAME_CN_RE = re.compile(r"姓名[：:]\s*([^\s，。；,;]{2,20})")
_UNIVERSITY_RE = re.compile(
    r"([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,3}\s+(?:University|College|Institute))"
)
_CN_UNIVERSITY_RE = re.compile(r"([\u4e00-\u9fff]{2,12}大学)")
_GOAL_RE = re.compile(r"目标(?:是|为|：|:)\s*([^。；\n]{3,90})")
_DIRECTION_RE = re.compile(r"(?:更偏好|偏好|方向(?:是|为|：|:))\s*([^。；\n]{2,90})")
_COLLAB_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*.*(?:合作|协作|配合|工作方式)")


def _clip(text: str, limit: int, ellipsis: str = "…") -> str:
    """按字符数截断（中文按字算），超长时补省略号，结果长度不超过 limit。"""
    text = str(text or "").strip()
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text
    if limit <= len(ellipsis):
        return ellipsis[:limit]
    return text[: limit - len(ellipsis)] + ellipsis


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_limited(path: Path) -> str:
    """读文本文件，最多 MAX_FILE_BYTES 字节；失败返回空串（调用方自己判空）。"""
    try:
        raw = path.read_bytes()[:MAX_FILE_BYTES]
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")


class PersonalLibrary:
    """用户个人库的只读视图：重载、摘要、检索、设置界面信息。"""

    def __init__(self, path=None, enabled: bool = True, max_chars: int = 1600, log=print):
        self.path = Path(path).expanduser() if path else DEFAULT_LIBRARY_PATH
        self.enabled = bool(enabled)
        try:
            self.max_chars = max(200, int(max_chars))
        except (TypeError, ValueError):
            self.max_chars = 1600
        self.log = log if callable(log) else print

        # 以下都是「公开可读」的解析结果，不含整个文件正文
        self.fields: dict = {}
        self.profile_lines: list[str] = []
        self.background_lines: list[str] = []
        self.preferences: list[tuple[str, str]] = []
        self.journal_entries: list[dict] = []
        self.preference_entries: list[dict] = []

        self.is_available = False
        self.error: str | None = None
        self.loaded_at: str | None = None
        self._signature: tuple | None = None
        self._last_logged_signature: tuple | None = None
        self._files_found: list[str] = []

        self.reload(force=True)

    # ------------------------------------------------------------------ #
    # 加载
    # ------------------------------------------------------------------ #
    def _file_signature(self) -> tuple:
        """五个关注文件的 (相对路径, 是否存在, mtime_ns, size)，用于判断是否需要重载。"""
        rels = (PROFILE_REL, BACKGROUND_REL, PREFERENCES_REL, JOURNAL_REL)
        parts = []
        for rel in rels:
            item = self.path / rel
            try:
                stat = item.stat()
            except OSError:
                parts.append((rel, False, 0, 0))
            else:
                parts.append((rel, True, stat.st_mtime_ns, stat.st_size))
        return tuple(parts)

    def reload(self, force: bool = False) -> bool:
        """按 mtime/size 变化重载；返回是否真的读了文件。绝不抛异常、绝不写文件。"""
        if not self.enabled:
            self.error = "个人库已关闭"
            self.is_available = False
            self.loaded_at = None
            return False

        signature = self._file_signature()
        if not force and signature == self._signature:
            return False

        profile_text = _read_limited(self.path / PROFILE_REL)
        background_text = _read_limited(self.path / BACKGROUND_REL)
        preferences_text = _read_limited(self.path / PREFERENCES_REL)
        journal_text = _read_limited(self.path / JOURNAL_REL)

        self.profile_lines = self._useful_lines(profile_text)
        self.background_lines = self._useful_lines(background_text)
        self.preferences = self._parse_preferences(preferences_text)
        self.journal_entries, bad_journal_lines = self._parse_journal(journal_text)
        self.preference_entries = [e for e in self.journal_entries if e.get("topic") == "preference"]
        self.fields = self._extract_fields(profile_text, background_text)

        self._files_found = [
            rel
            for rel, exists, _mtime, _size in signature
            if exists
        ]

        errors: list[str] = []
        if not self.path.is_dir():
            errors.append(f"没找到个人库目录：{self.path}（不影响聊天，可在设置里改 library_path）")
        else:
            if not self._files_found:
                errors.append("个人库目录里没找到 profile.md / preferences.json / journal.jsonl（不影响聊天）")
            if not profile_text.strip():
                errors.append("没读到个人库背景 profile.md（用其余资料继续）")
            if bad_journal_lines:
                errors.append(f"journal.jsonl 有 {bad_journal_lines} 行不是合法 JSON，已跳过")

        self.error = "；".join(errors) if errors else None
        self._signature = signature
        self.is_available = bool(
            self.enabled and (profile_text.strip() or background_text.strip() or self.preferences or self.journal_entries)
        )
        # loaded_at 表示「最近一次成功加载」；完全读不到内容时保持 None
        self.loaded_at = _now_text() if self.is_available else None

        # 只在「首次加载 / 文件变化 / 错误变化」时写一条不包含正文的日志
        log_key = (signature, self.error)
        if self._last_logged_signature != log_key:
            self._last_logged_signature = log_key
            self._log_load_result(errors)
        return True

    def _log_load_result(self, errors: list[str]) -> None:
        """日志只写状态与计数，绝不写个人库正文。"""
        try:
            if errors:
                self.log("个人库：" + "；".join(errors))
            elif self.is_available:
                self.log(
                    f"个人库：已加载（背景 {len(self.profile_lines)} 行，"
                    f"偏好 {len(self.preferences)} 项，记忆 {len(self.journal_entries)} 条），"
                    f"更新时间 {self.loaded_at}"
                )
        except Exception:  # log 回调自己出问题也不能拖垮桌宠
            pass

    @staticmethod
    def _useful_lines(text: str) -> list[str]:
        """把 markdown 拆成适合检索的短行：去空行/标题标记，保留正文。"""
        lines: list[str] = []
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith(("```", "---", "===")):
                continue
            if _HEADING_RE.match(line):
                continue
            line = _BULLET_RE.sub("", line).strip()
            if len(line) < 4:
                continue
            lines.append(line[:400])
            if len(lines) >= 200:
                break
        return lines

    @staticmethod
    def _parse_preferences(text: str) -> list[tuple[str, str]]:
        if not text.strip():
            return []
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return []
        if not isinstance(data, dict):
            return []

        out: list[tuple[str, str]] = []

        def walk(obj, prefix: str = "") -> None:
            if len(out) >= 80:
                return
            if isinstance(obj, dict):
                for key, value in obj.items():
                    walk(value, f"{prefix}.{key}" if prefix else str(key))
            elif isinstance(obj, (list, tuple)):
                value = "、".join(str(item) for item in obj[:5])
                if prefix:
                    out.append((prefix, value))
            elif obj is not None:
                if prefix:
                    out.append((prefix, str(obj)))

        walk(data)
        return out

    def _parse_journal(self, text: str) -> tuple[list[dict], int]:
        entries: list[dict] = []
        bad = 0
        for raw in (text or "").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except (ValueError, TypeError):
                bad += 1
                continue
            if not isinstance(item, dict):
                bad += 1
                continue
            body = str(item.get("text") or "").strip()
            if not body:
                continue
            entries.append({
                "seq": self._as_int(item.get("seq")),
                "ts": str(item.get("ts") or ""),
                "by": str(item.get("by") or ""),
                "topic": str(item.get("topic") or "").strip().lower(),
                "text": body[:1000],
                "source": str(item.get("source") or ""),
            })
        entries.sort(key=lambda e: (e["seq"], e["ts"]))
        return entries[-MAX_JOURNAL_ENTRIES:], bad

    @staticmethod
    def _as_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------ #
    # 从 profile / 精简版背景里提炼几个字段
    # ------------------------------------------------------------------ #
    def _extract_fields(self, profile_text: str, background_text: str) -> dict:
        fields: dict = {}
        combined = "\n".join(part for part in (profile_text, background_text) if part)

        # 姓名：优先 profile 每条 bullet 开头的人名，其次中文字段，最后「我是 XXX」
        for line in (profile_text or "").splitlines():
            if not line.strip():
                continue
            match = _NAME_RE.match(line.strip())
            if match:
                fields["name"] = match.group(1).strip()
                break
        if not fields.get("name"):
            match = _NAME_CN_RE.search(profile_text) or _NAME_CN_RE.search(background_text)
            if match:
                fields["name"] = match.group(1).strip()
        if not fields.get("name"):
            match = re.search(r"我是\s*([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)", combined)
            if match:
                fields["name"] = match.group(1).strip()

        # 学校：英文 University/College/Institute，其次中文「XX大学」
        match = _UNIVERSITY_RE.search(combined)
        if match:
            fields["school"] = re.sub(r"\s+", " ", match.group(1)).strip()
        else:
            match = _CN_UNIVERSITY_RE.search(combined)
            if match:
                fields["school"] = match.group(1)

        # 阶段：优先「硕士在读 / 博士在读」这类短语
        for pattern in (r"([\u4e00-\u9fff]{2,12}(?:硕士|博士)(?:在读|研究生|学位))",
                        r"((?:硕士|博士)在读)",
                        r"((?:机械|电气|电子|计算机)?工程方向(?:硕士|博士))"):
            match = re.search(pattern, combined)
            if match:
                fields["degree"] = match.group(1)
                break

        match = _GOAL_RE.search(combined)
        if match:
            fields["goal"] = _clip(match.group(1), 90)

        # 方向：先找「更偏好……」，退化到含 LLM/Agent/科研 的短句
        for pattern in (r"(?:更偏好|偏好)\s*([^。；\n]{2,90})",
                        r"((?:构建|做|研究)[^。；\n]{0,60}(?:Agent|LLM|AI)[^。；\n]{0,40})"):
            match = re.search(pattern, combined)
            if match:
                fields["direction"] = _clip(match.group(1), 90)
                break

        collaboration = self._extract_collaboration(profile_text)
        if collaboration:
            fields["collaboration"] = collaboration
        return fields

    @staticmethod
    def _extract_collaboration(profile_text: str) -> list[str]:
        """取 profile 里「怎样合作最有效」一节的 bullet（最多 3 条），没有就找含「偏好」的行。"""
        lines = (profile_text or "").splitlines()
        bullets: list[str] = []
        in_section = False
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if _HEADING_RE.match(line):
                in_section = bool(_COLLAB_HEADING_RE.match(line))
                continue
            if in_section:
                body = _BULLET_RE.sub("", line).strip()
                if body:
                    bullets.append(_clip(body, 90).rstrip("。；;，,、 "))
                if len(bullets) >= 3:
                    return bullets
        if bullets:
            return bullets
        fallback = [
            _clip(_BULLET_RE.sub("", line).strip(), 90).rstrip("。；;，,、 ")
            for line in lines
            if "偏好" in line and _BULLET_RE.match(line.strip())
        ]
        return fallback[:3]

    # ------------------------------------------------------------------ #
    # 对外：给 system prompt 的短摘要
    # ------------------------------------------------------------------ #
    def _field_lines(self) -> list[str]:
        lines: list[str] = []
        fields = self.fields or {}
        if fields.get("name"):
            lines.append(f"姓名：{fields['name']}")
        school = fields.get("school", "")
        degree = fields.get("degree", "")
        if school or degree:
            text = school + ("，" + degree if degree else "")
            lines.append("学校/阶段：" + text.strip("，"))
        if fields.get("goal"):
            lines.append(f"目标：{fields['goal']}")
        if fields.get("direction"):
            lines.append(f"方向/兴趣：{fields['direction']}")
        if fields.get("collaboration"):
            lines.append("合作偏好：" + "；".join(fields["collaboration"]))
        return lines

    def _preferences_short(self, limit: int = 6) -> list[str]:
        return [f"{key}={_clip(value, 50)}" for key, value in self.preferences[:limit]]

    def prompt_block(self, query: str = "") -> str:
        """给 system prompt 注入的短摘要（≤ max_chars），不含技术坑原文。"""
        if not self.enabled:
            return ""
        self.reload()
        if not self.is_available:
            return ""

        core = [PROMPT_HEADER]
        core.extend(self._field_lines())

        extra: list[str] = []
        prefs = self._preferences_short()
        if prefs:
            extra.append("偏好设置：" + "；".join(prefs))

        recent = list(self.preference_entries[-RECENT_PREFERENCE_COUNT:])
        if recent:
            extra.append("最近偏好记忆：" + "；".join(_clip(e["text"], 70) for e in recent))

        if str(query or "").strip():
            related = []
            seen = set()
            for item in self.find(query, limit=3):
                text = item.get("text") or ""
                if not text or text in seen:
                    continue
                if any(text in line for line in core):
                    continue
                seen.add(text)
                related.append(_clip(text, 70))
            if related:
                extra.append("和当前话题相关：" + "；".join(related))

        if not core[1:]:
            # 只有 journal 的个人库：至少给出一条最近偏好，避免空块
            if recent:
                core.append("最近偏好记忆：" + "；".join(_clip(e["text"], 60) for e in recent))
            else:
                return ""

        def assemble(parts: list[str]) -> str:
            return "\n".join(parts) + "\n" + PROMPT_TAIL

        # 超长时依次删除可选段落；还不够就截断正文（保留结尾的「不要编造」）
        optional = list(extra)
        while optional and len(assemble(core + optional)) > self.max_chars:
            optional.pop()
        text = assemble(core + optional)
        if len(text) <= self.max_chars:
            return text
        allowed = self.max_chars - len(PROMPT_TAIL) - 1
        body = _clip("\n".join(core), allowed)
        if not body:
            return _clip(PROMPT_TAIL, self.max_chars)
        return body + "\n" + PROMPT_TAIL

    def quick_recap(self) -> str:
        """离线兜底用的一句话「我记得你：……」，≤80 字。"""
        if not self.enabled:
            return ""
        self.reload()
        if not self.is_available:
            return ""
        fields = self.fields or {}
        parts: list[str] = []
        if fields.get("name"):
            parts.append(fields["name"])
        if fields.get("school"):
            parts.append(fields["school"])
        elif fields.get("degree"):
            parts.append(fields["degree"])
        if fields.get("goal"):
            parts.append("目标" + fields["goal"])
        if fields.get("direction"):
            parts.append("方向" + _clip(fields["direction"], 30))
        if not parts:
            recent = self.preference_entries[-1] if self.preference_entries else None
            if recent:
                return _clip("我记得你：" + recent["text"], 80)
            return ""
        text = "我记得你：" + "，".join(parts) + "。"
        return _clip(text, 80)

    # ------------------------------------------------------------------ #
    # 对外：检索
    # ------------------------------------------------------------------ #
    def _candidates(self) -> list[dict]:
        items: list[dict] = []
        for line in self._field_lines():
            items.append({"text": line, "source": "profile"})
        for line in self.profile_lines:
            items.append({"text": line, "source": "profile"})
        for line in self.background_lines:
            items.append({"text": line, "source": "background"})
        for key, value in self.preferences:
            items.append({"text": f"{key}={value}", "source": "preferences"})
        for entry in self.preference_entries:
            items.append({"text": entry["text"], "source": "journal"})
        # 去重，保留首次出现的来源
        seen: set[str] = set()
        unique: list[dict] = []
        for item in items:
            text = item["text"].strip()
            if not text or text in seen:
                continue
            seen.add(text)
            unique.append({"text": text, "source": item["source"]})
        return unique

    @staticmethod
    def _query_tokens(query: str) -> tuple[set[str], set[str]]:
        """返回 (中文 2-gram 集合, 英文/数字词集合)。"""
        text = str(query or "").strip().lower()
        if not text:
            return set(), set()
        grams: set[str] = set()
        for run in _CJK_RUN_RE.findall(text):
            if len(run) == 1:
                continue
            for i in range(len(run) - 1):
                gram = run[i:i + 2]
                if gram not in _STOP_2GRAMS:
                    grams.add(gram)
        words = {
            word.lower()
            for word in _ASCII_WORD_RE.findall(text)
            if len(word) >= 2
        }
        return grams, words

    def find(self, query: str = "", limit: int = 5) -> list[dict]:
        """按 2-gram + 关键词检索；只查 profile/背景/偏好/最近 preference 记忆。

        返回 ``[{"text": str, "source": "profile|background|preferences|journal"}]``，
        JSON 友好，供 Agent 工具直接返回。技术坑（pitfall）不参与检索。
        """
        if not self.enabled:
            return []
        self.reload()
        try:
            limit = max(1, min(20, int(limit)))
        except (TypeError, ValueError):
            limit = 5
        candidates = self._candidates()

        query = str(query or "").strip()
        if not query:
            return candidates[:limit]

        grams, words = self._query_tokens(query)
        lowered_query = query.lower()
        scored: list[tuple[float, int, dict]] = []
        for order, item in enumerate(candidates):
            lowered = item["text"].lower()
            score = 0.0
            if lowered_query and lowered_query in lowered:
                score += 3.0
            for gram in grams:
                if gram in lowered:
                    score += 1.0
            for word in words:
                if word in lowered:
                    score += 2.0
            if score <= 0:
                continue
            scored.append((score, -order, item))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [dict(item) for _score, _order, item in scored[:limit]]

    # ------------------------------------------------------------------ #
    # 对外：设置界面信息
    # ------------------------------------------------------------------ #
    def summary(self) -> str:
        """设置界面显示用：路径、条数、更新时间；不输出任何正文。"""
        if not self.enabled:
            return "个人库：已关闭（不影响离线聊天）"
        parts = [f"个人库：{self.path}"]
        if not self.path.is_dir():
            parts.append("未找到目录")
            if self.error:
                parts.append(self.error)
            return "｜".join(parts)
        if not self.is_available:
            parts.append("未加载到可用内容")
            if self.error:
                parts.append(self.error)
            return "｜".join(parts)
        preference_count = len(self.preference_entries)
        other_count = max(0, len(self.journal_entries) - preference_count)
        background_count = len(self.profile_lines) + len(self.background_lines)
        parts.append("已加载")
        parts.append(f"背景 {background_count} 行")
        parts.append(f"偏好 {len(self.preferences)} 项")
        parts.append(f"记忆 {len(self.journal_entries)} 条（偏好 {preference_count}，其他 {other_count}）")
        if self.loaded_at:
            parts.append(f"更新 {self.loaded_at}")
        if self.error:
            parts.append(self.error)
        return "｜".join(parts)


__all__ = ["PersonalLibrary", "DEFAULT_LIBRARY_PATH", "PROMPT_TAIL"]
