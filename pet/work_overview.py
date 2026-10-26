# -*- coding: utf-8 -*-
"""自动工作总览：把「最近做过 / 正在做 / 待办」抓成本地 HTML + 短摘要。

数据全部取自本机，只读、不上传：

a) 用户个人库（由 :class:`personal.PersonalLibrary` 提供，只取短摘要，不读正文给外部）；
b) ``config.overview.roots`` 下各工作区「二级目录」（项目）最近 7 天的文件改动数与最新 mtime；
c) ``~/.dsh/sessions`` 一级目录（目录名编码了工作区路径）最近 3 天有更新的前 6 个；
   只取「路径 + 时间」，**不读会话正文**；
d) ``~/.dsh/whale-bridge/status.json`` 的 DSH 实时状态（state/lastTool/doneCount...）；
e) 工作区与项目根下的 ``TODO.md / todo.md / NEXT.md / TASKS.md / 待办.md``（最多 3 个，
   每个最多 30 行摘要）；
f) 可选 AI「下一步建议」：只有配置里能找到 Key 时才调用 OpenAI 兼容接口，
   20 秒超时、失败静默回退 3 条模板建议。**发出去的提示不包含个人库正文**，
   只有工作区/项目名、待办线索和 DSH 状态。

输出（每次真正刷新时覆盖，原子写）：

- ``pet/overview.json``：结构化数据 + generated_at；
- ``docs/工作总览.html``：自包含白底页面，无外部脚本/样式，标注数据来源与生成时间。

对外接口（app.py / agent.py 会用到）：

- ``overview.refresh(force=False) -> bool``
- ``overview.text_summary(max_chars=500) -> str``
- ``overview.is_stale() -> bool``
- ``overview.data`` / ``overview.html_path`` / ``overview.json_path``
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

VERSION = 1
DEFAULT_ROOTS: list[str] = []  # 要扫哪些工作区写进 config.overview.roots，默认不扫
DEFAULT_MIN_INTERVAL_MINUTES = 10
DEFAULT_RECENT_DAYS = 7
DEFAULT_SESSION_DAYS = 3
DEFAULT_SESSION_LIMIT = 6
DEFAULT_PROJECT_LIMIT = 8
DEFAULT_STATUS_PATH = "~/.dsh/whale-bridge/status.json"
DEFAULT_SESSIONS_DIR = "~/.dsh/sessions"

TODO_NAMES = ("TODO.md", "todo.md", "NEXT.md", "TASKS.md", "待办.md")
TODO_MAX_FILES = 3
TODO_MAX_LINES = 30

# 扫描时跳过的目录名（大小写不敏感）：依赖、虚拟环境、缓存、构建产物、应用安装目录
SKIP_DIR_NAMES = {
    "node_modules", ".git", ".hg", ".svn", ".venv", "venv", "env", "runtimes",
    ".harness-cache", "pkg", "缓存", "cache", "__pycache__", "site-packages",
    "dist", "build", "target", ".next", ".nuxt", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "coverage", ".idea", ".vscode", "logs",
    "resources", "app.asar", "app.asar.unpacked", "locales",
}
# 每个项目最多 stat 这么多文件，超过就标记 truncated（防止大仓库把桌宠卡住）
PROJECT_FILE_BUDGET = 6000
# 单个项目最多花这么多秒
PROJECT_TIME_BUDGET = 0.6
# 整个 refresh 的「扫文件」时间预算（秒）
SCAN_TIME_BUDGET = 8.0
# DSH 状态文件超过这个秒数没更新就视为断连（插件每 5 秒心跳）
STATUS_STALE_SECONDS = 120.0

_VALID_DSH_STATES = ("idle", "running", "tool", "success", "failure", "approval")
_DSH_STATE_NAMES = {
    "idle": "空闲",
    "running": "思考/干活中",
    "tool": "调用工具",
    "success": "刚完成一轮",
    "failure": "刚出错",
    "approval": "等待审批",
}
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s*")


def _clip(text: str, limit: int, ellipsis: str = "…") -> str:
    text = str(text or "").strip()
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text
    if limit <= len(ellipsis):
        return ellipsis[:limit]
    return text[: limit - len(ellipsis)] + ellipsis


def _iso_from_ts(ts: float | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def _num_text(value) -> str:
    """7.0 → 「7」，7.5 → 「7.5」；给 HTML 显示用，避免出现 7.0 天这种别扭写法。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return f"{number:g}"


def _epoch_ms(value) -> float | None:
    """把 status.json 里的 updatedAt 归一成毫秒时间戳（认秒/毫秒/ISO 字符串）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return None
        return number * 1000.0 if number < 1e12 else number
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return parsed.timestamp() * 1000.0
            except ValueError:
                return None
        if number <= 0:
            return None
        return number * 1000.0 if number < 1e12 else number
    return None


def _as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def decode_session_dir(name: str) -> str:
    """把 DSH 会话一级目录名解码成工作区路径。

    规则（实测）：目录名形如 ``--D-Work-DeepSeek-~5927~80A5~9C7C--``；
    去掉首尾各两个 ``-`` 后，``~XXXX`` 是 Unicode 码点，``-`` 是路径分隔符，
    驱动器字母后面补 ``:``。解不出来就原样返回，绝不抛异常。
    """
    text = str(name or "").strip()
    if not text:
        return ""
    raw = text
    if raw.startswith("--"):
        raw = raw[2:]
    if raw.endswith("--"):
        raw = raw[:-2]
    if not raw:
        return text

    def replace_codepoint(match: re.Match) -> str:
        try:
            return chr(int(match.group(1), 16))
        except (ValueError, OverflowError):
            return match.group(0)

    decoded = re.sub(r"~([0-9A-Fa-f]{4,6})", replace_codepoint, raw)
    decoded = decoded.replace("~", "")  # 兼容「~XXXX~」写法残留的分隔符
    decoded = decoded.replace("-", "/")
    decoded = re.sub(r"/{2,}", "/", decoded).strip("/")
    match = re.match(r"^([A-Za-z])/(.*)$", decoded)
    if match:
        decoded = f"{match.group(1)}:/{match.group(2)}"
    return decoded or text


def _is_skipped_dir(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    if not lowered or lowered.startswith("."):
        return True
    return lowered in SKIP_DIR_NAMES


def _is_temp_path(path: str) -> bool:
    """判断解码后的会话路径是否在系统临时目录（Agent scratch / 自测目录）。

    这类会话仍算 DSH 活动，但不是用户的真实工作区，默认不放进工作总览。
    """
    lowered = str(path or "").replace("\\", "/").lower()
    if not lowered:
        return False
    return (
        "/appdata/local/temp/" in lowered
        or "/temp/" in lowered
        or lowered.startswith("/tmp/")
        or lowered.startswith("tmp/")
    )


class WorkOverview:
    """收集本机工作痕迹，生成 ``pet/overview.json`` 与 ``docs/工作总览.html``。"""

    def __init__(self, base_dir: Path, config: dict, personal=None, log=print):
        self.base_dir = Path(base_dir).expanduser()
        self.config = config if isinstance(config, dict) else {}
        self.personal = personal
        self.log = log if callable(log) else print

        cfg = self.config.get("overview") or {}
        if not isinstance(cfg, dict):
            cfg = {}
        self._cfg = cfg

        roots = cfg.get("roots") or DEFAULT_ROOTS
        if isinstance(roots, str):
            roots = [roots]
        self.roots = [Path(str(item)).expanduser() for item in roots if str(item).strip()]

        try:
            self.min_interval_minutes = max(0.0, float(cfg.get("min_interval_minutes", DEFAULT_MIN_INTERVAL_MINUTES)))
        except (TypeError, ValueError):
            self.min_interval_minutes = float(DEFAULT_MIN_INTERVAL_MINUTES)
        try:
            self.recent_days = max(0.1, float(cfg.get("recent_days", DEFAULT_RECENT_DAYS)))
        except (TypeError, ValueError):
            self.recent_days = float(DEFAULT_RECENT_DAYS)
        try:
            self.session_days = max(0.1, float(cfg.get("session_days", DEFAULT_SESSION_DAYS)))
        except (TypeError, ValueError):
            self.session_days = float(DEFAULT_SESSION_DAYS)
        self.project_limit = max(1, min(30, _as_int(cfg.get("project_limit", DEFAULT_PROJECT_LIMIT)) or DEFAULT_PROJECT_LIMIT))
        self.session_limit = max(1, min(30, _as_int(cfg.get("session_limit", DEFAULT_SESSION_LIMIT)) or DEFAULT_SESSION_LIMIT))
        self.sessions_dir = Path(str(cfg.get("sessions_dir") or DEFAULT_SESSIONS_DIR)).expanduser()
        self.status_path = Path(str(cfg.get("status_path") or DEFAULT_STATUS_PATH)).expanduser()
        # 默认跳过系统临时目录里的会话：那是 Agent 的 scratch 工作区，不是用户的工作区，
        # 否则 DSH 自测产生的 tmp/scab 会话会把真正的项目会话挤出前 6。
        self.include_temp_sessions = bool(cfg.get("include_temp_sessions", False))

        self.json_path = self.base_dir / "overview.json"
        self.html_path = self.base_dir.parent / "docs" / "工作总览.html"

        self.data: dict | None = None
        self._last_generated_ts: float | None = None
        # app.py 会在后台线程里定时刷新，也可能被菜单手动触发：同一时刻只允许一个刷新
        self._refresh_lock = threading.Lock()
        self._load_existing()

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def _load_existing(self) -> None:
        """启动时先读旧 overview.json，避免第一次打开设置界面时一片空白。"""
        try:
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        try:
            generated_ts = float(payload.get("generated_ts") or 0)
        except (TypeError, ValueError):
            generated_ts = 0.0
        if not generated_ts:
            generated_ts = _parse_iso_ts(payload.get("generated_at"))
        self.data = payload
        self._last_generated_ts = generated_ts or None

    def is_stale(self) -> bool:
        """数据不存在或超过 ``min_interval_minutes`` 没更新，就认为该刷新了。"""
        if not isinstance(self.data, dict) or not self.data.get("generated_at"):
            return True
        if not self._last_generated_ts:
            return True
        return (time.time() - self._last_generated_ts) >= self.min_interval_minutes * 60.0

    # ------------------------------------------------------------------ #
    # 刷新
    # ------------------------------------------------------------------ #
    def refresh(self, force: bool = False) -> bool:
        """按节流规则收集并写盘；返回是否真的刷新了（没过节流就返回 False）。"""
        # 后台定时刷新和手动刷新可能撞在一起：拿不到锁就说明别人正在刷新，
        # 直接返回 False，避免重复扫描 / 重复调用模型。
        if not self._refresh_lock.acquire(blocking=False):
            return False
        try:
            return self._refresh_locked(force)
        finally:
            self._refresh_lock.release()

    def _refresh_locked(self, force: bool) -> bool:
        now = time.time()
        if not force and not self._should_refresh(now):
            return False
        started = time.monotonic()
        try:
            data = self._collect(now, started)
        except Exception as exc:  # 总览只是锦上添花，绝不能把桌宠搞崩
            self.log("工作总览生成本次失败（不影响聊天）：" + self._redact(f"{type(exc).__name__}: {exc}"))
            return False
        try:
            self._write_outputs(data)
        except OSError as exc:
            self.log("工作总览写文件失败（不影响聊天）：" + self._redact(str(exc)))
            return False
        self.data = data
        self._last_generated_ts = now
        personal_note = "含个人库摘要" if (data.get("personal") or {}).get("available") else "无个人库"
        self.log(
            f"工作总览已更新：{self.html_path}（项目 {len(data['recent_projects'])} 个，"
            f"会话 {len(data['sessions'])} 个，待办文件 {len(data['todos']['files'])} 个，"
            f"建议来源 {data['suggestions']['source']}，{personal_note}）"
        )
        return True

    def _should_refresh(self, now: float) -> bool:
        if not isinstance(self.data, dict):
            return True
        if not self._last_generated_ts:
            return True
        if int(self.data.get("version") or 0) != VERSION:
            return True
        return (now - self._last_generated_ts) >= self.min_interval_minutes * 60.0

    def _collect(self, now: float, started: float) -> dict:
        recent_projects = self._collect_recent_projects(now)
        sessions = self._collect_session_activity(now)
        dsh = self._read_dsh_status(now)
        todos = self._collect_todos(recent_projects)
        personal = self._collect_personal()
        suggestions = self._make_suggestions(recent_projects, sessions, dsh, todos)
        elapsed = round(max(0.0, time.monotonic() - started), 2)
        return {
            "version": VERSION,
            "generated_at": _iso_from_ts(now),
            "generated_ts": round(now, 3),
            "elapsed_s": elapsed,
            "recent_days": self.recent_days,
            "roots": [str(root) for root in self.roots],
            "sources": self._source_labels(),
            "personal": personal,
            "recent_projects": recent_projects,
            "sessions": sessions,
            "dsh": dsh,
            "todos": todos,
            "suggestions": suggestions,
        }

    def _source_labels(self) -> list[str]:
        labels = [
            "个人库（只读，仅本地使用）：" + str(self._personal_path()),
            f"工作区文件修改时间：{', '.join(str(root) for root in self.roots)} 下最近 {self.recent_days:g} 天",
            f"DSH 会话目录（只取目录名与时间，不读正文）：{self.sessions_dir}"
            + ("（含系统临时目录会话）" if self.include_temp_sessions else "（已跳过系统临时目录会话）"),
            f"DSH 实时状态：{self.status_path}",
        ]
        if self._llm_settings()[0]:
            labels.append("下一步建议：本机配置的 LLM（只发送工作区/待办/状态，不含个人库正文）")
        else:
            labels.append("下一步建议：本地模板（未配置 API Key）")
        return labels

    def _personal_path(self) -> str:
        personal = self.personal
        try:
            return str(getattr(personal, "path", "") or "未接入")
        except Exception:
            return "未接入"

    # ------------------------------------------------------------------ #
    # b) 最近工作区改动：roots 下各工作区的二级目录
    # ------------------------------------------------------------------ #
    def _collect_recent_projects(self, now: float) -> list[dict]:
        cutoff = now - self.recent_days * 86400.0
        deadline = time.monotonic() + SCAN_TIME_BUDGET
        targets: list[tuple[str, Path]] = []

        for root in self.roots:
            try:
                if not root.is_dir():
                    continue
                workspaces = [
                    entry for entry in root.iterdir()
                    if entry.is_dir() and not entry.is_symlink() and not _is_skipped_dir(entry.name)
                ]
            except OSError as exc:
                self.log(f"总览：读取工作区 {root} 失败，已跳过（{exc.__class__.__name__}）")
                continue
            for workspace in sorted(workspaces, key=lambda p: p.name.lower()):
                try:
                    subdirs = [
                        entry for entry in workspace.iterdir()
                        if entry.is_dir() and not entry.is_symlink() and not _is_skipped_dir(entry.name)
                    ]
                except OSError:
                    subdirs = []
                if subdirs:
                    targets.extend((workspace.name, sub) for sub in subdirs)
                else:
                    targets.append((root.name, workspace))

        # 优先扫描当前项目（pet 的上级目录），保证它不会因为预算用完而被挤掉
        own_project = self.base_dir.parent.resolve()
        def priority(item: tuple[str, Path]) -> tuple[int, str]:
            try:
                is_own = item[1].resolve() == own_project
            except OSError:
                is_own = False
            return (0 if is_own else 1, item[1].name.lower())

        projects: list[dict] = []
        seen: set[str] = set()
        for workspace_name, target in sorted(targets, key=priority):
            if time.monotonic() > deadline:
                break
            try:
                if not target.is_dir() or target.is_symlink():
                    continue
            except OSError:
                continue
            try:
                key = str(target.resolve())
            except OSError:
                key = str(target)
            if key in seen:
                continue
            seen.add(key)
            stats = self._scan_tree(target, cutoff, deadline)
            if stats["total_files"] == 0 and stats["recent_files"] == 0:
                continue
            rel = self._relative_label(target)
            projects.append({
                "workspace": workspace_name,
                "project": target.name,
                "path": rel,
                "abs_path": str(target),
                "recent_files": stats["recent_files"],
                "total_files": stats["total_files"],
                "last_modified": _iso_from_ts(stats["latest_ts"] or None),
                "last_modified_ts": stats["latest_ts"],
                "truncated": stats["truncated"],
            })

        projects.sort(key=lambda item: (item["recent_files"], item["last_modified_ts"]), reverse=True)
        return projects[: self.project_limit]

    def _relative_label(self, path: Path) -> str:
        """展示用相对路径：优先相对某个 root；否则相对盘根，避免把用户名带进页面。"""
        for root in self.roots:
            try:
                return str(path.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
        try:
            return str(path.relative_to(path.anchor)).replace("\\", "/")
        except (ValueError, OSError):
            return path.name

    def _scan_tree(self, root: Path, cutoff: float, deadline: float) -> dict:
        """递归统计 root 下的文件数、最近 cutoff 内的文件数、最新 mtime。

        用 ``os.scandir`` + 缓存 stat，速度比 ``os.walk`` 逐文件 ``Path.stat()`` 快；
        每个项目有独立的时间/文件数上限，超大仓库只统计前一部分并标记 truncated。
        """
        recent = 0
        total = 0
        latest = 0.0
        truncated = False
        project_deadline = min(deadline, time.monotonic() + PROJECT_TIME_BUDGET)
        visited = 0
        stack: list[tuple[str, int]] = [(str(root), 0)]
        while stack:
            directory, depth = stack.pop()
            if visited >= PROJECT_FILE_BUDGET or time.monotonic() > project_deadline:
                truncated = True
                break
            try:
                entries = os.scandir(directory)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    if visited >= PROJECT_FILE_BUDGET or time.monotonic() > project_deadline:
                        truncated = True
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth < 7 and not _is_skipped_dir(entry.name):
                                stack.append((entry.path, depth + 1))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        visited += 1
                        total += 1
                        mtime = entry.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    if mtime > latest:
                        latest = mtime
                    if mtime >= cutoff:
                        recent += 1
            if truncated:
                break
        return {
            "recent_files": recent,
            "total_files": total,
            "latest_ts": latest,
            "truncated": truncated,
        }

    # ------------------------------------------------------------------ #
    # c) DSH 会话活动（只看目录名和时间）
    # ------------------------------------------------------------------ #
    def _collect_session_activity(self, now: float) -> list[dict]:
        cutoff = now - self.session_days * 86400.0
        sessions: list[dict] = []
        try:
            entries = sorted(
                (entry for entry in self.sessions_dir.iterdir() if entry.is_dir()),
                key=lambda p: p.name,
            )
        except OSError:
            return []

        for entry in entries:
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            # 一级目录本身可能不随子会话变化，再看一眼子目录的 mtime（最多 30 个）
            try:
                children = []
                for child in entry.iterdir():
                    if child.is_dir():
                        children.append(child)
                        if len(children) >= 30:
                            break
                for child in children:
                    try:
                        child_mtime = child.stat().st_mtime
                    except OSError:
                        continue
                    if child_mtime > mtime:
                        mtime = child_mtime
            except OSError:
                pass
            if mtime < cutoff:
                continue
            path = decode_session_dir(entry.name)
            if not self.include_temp_sessions and _is_temp_path(path):
                continue
            sessions.append({
                "path": path,
                "label": path.rstrip("/").rsplit("/", 1)[-1] if path else entry.name,
                "last_active": _iso_from_ts(mtime),
                "last_active_ts": mtime,
            })

        sessions.sort(key=lambda item: item["last_active_ts"], reverse=True)
        return sessions[: self.session_limit]

    # ------------------------------------------------------------------ #
    # d) DSH 实时状态
    # ------------------------------------------------------------------ #
    def _read_dsh_status(self, now: float) -> dict:
        result = {
            "available": False,
            "connected": False,
            "state": "unknown",
            "state_text": "未连接",
            "lastTool": None,
            "doneCount": 0,
            "errorCount": 0,
            "approvalOpen": False,
            "updated_at": "",
            "reason": "",
        }
        try:
            raw = self.status_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            result["reason"] = "没找到 DSH 状态文件（插件未安装 / DSH 没启动）"
            return result
        except OSError as exc:
            result["reason"] = f"读取 DSH 状态文件失败（{exc.__class__.__name__}）"
            return result
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            result["reason"] = "DSH 状态文件不是合法 JSON（可能正在原子替换，稍后重试）"
            return result
        if not isinstance(payload, dict):
            result["reason"] = "DSH 状态文件格式不对"
            return result

        state = str(payload.get("state") or "").strip().lower()
        if state not in _VALID_DSH_STATES:
            result["reason"] = f"DSH 状态文件里的 state 不认识：{state or '空'}"
            return result
        updated_ms = _epoch_ms(payload.get("updatedAt"))
        updated_ts = (updated_ms / 1000.0) if updated_ms else None
        age = (now - updated_ts) if updated_ts else None
        connected = age is not None and 0 <= age <= STATUS_STALE_SECONDS
        result.update({
            "available": True,
            "connected": connected,
            "state": state,
            "state_text": _DSH_STATE_NAMES.get(state, state),
            "lastTool": payload.get("lastTool") if isinstance(payload.get("lastTool"), str) else None,
            "doneCount": _as_int(payload.get("doneCount")),
            "errorCount": _as_int(payload.get("errorCount")),
            "approvalOpen": bool(payload.get("approvalOpen")),
            "updated_at": _iso_from_ts(updated_ts),
            "reason": "" if connected else "状态心跳已过期（DSH 可能已关闭）",
        })
        return result

    # ------------------------------------------------------------------ #
    # e) 待办线索
    # ------------------------------------------------------------------ #
    def _collect_todos(self, recent_projects: list[dict]) -> dict:
        candidate_dirs: list[Path] = [self.base_dir, self.base_dir.parent]
        candidate_dirs.extend(root for root in self.roots)
        candidate_dirs.extend(Path(item["abs_path"]) for item in recent_projects if item.get("abs_path"))
        # 再加一层：最近项目所在工作区
        for item in recent_projects:
            try:
                workspace_dir = Path(item["abs_path"]).parent
            except (TypeError, KeyError):
                continue
            if workspace_dir != Path(item["abs_path"]):
                candidate_dirs.append(workspace_dir)

        files: list[dict] = []
        seen: set[str] = set()
        for directory in candidate_dirs:
            if len(files) >= TODO_MAX_FILES:
                break
            try:
                if not directory.is_dir():
                    continue
            except OSError:
                continue
            for filename in TODO_NAMES:
                if len(files) >= TODO_MAX_FILES:
                    break
                path = directory / filename
                try:
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                try:
                    key = str(path.resolve())
                except OSError:
                    key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                files.append(self._read_todo_file(path))

        if not files:
            return {
                "files": [],
                "count": 0,
                "hint": "未发现显式 TODO 文件",
            }
        return {"files": files, "count": len(files), "hint": ""}

    def _read_todo_file(self, path: Path) -> dict:
        item = {
            "path": str(path),
            "rel": self._relative_label(path),
            "title": path.name,
            "lines": [],
            "truncated": False,
            "error": None,
        }
        try:
            raw = path.read_bytes()[:256 * 1024].decode("utf-8", errors="replace")
        except OSError as exc:
            item["error"] = f"读取失败（{exc.__class__.__name__}）"
            return item
        lines: list[str] = []
        for line in raw.splitlines():
            line = line.rstrip()
            if not line.strip():
                continue
            if line.strip().startswith(("```", "<!--")):
                continue
            lines.append(_clip(line.strip(), 160))
        item["truncated"] = len(lines) > TODO_MAX_LINES
        item["lines"] = lines[:TODO_MAX_LINES]
        return item

    # ------------------------------------------------------------------ #
    # a) 个人库摘要
    # ------------------------------------------------------------------ #
    def _collect_personal(self) -> dict:
        out = {
            "available": False,
            "recap": "",
            "block": "",
            "summary": "",
            "error": None,
        }
        personal = self.personal
        if personal is None:
            out["error"] = "未接入（启动时没有传入 PersonalLibrary）"
            return out
        try:
            out["available"] = bool(getattr(personal, "is_available", False))
            recap = getattr(personal, "quick_recap", None)
            if callable(recap):
                out["recap"] = _clip(str(recap() or ""), 120)
            block = getattr(personal, "prompt_block", None)
            if callable(block):
                out["block"] = _clip(str(block() or ""), 900)
            summary = getattr(personal, "summary", None)
            if callable(summary):
                out["summary"] = _clip(str(summary() or ""), 240)
            error = getattr(personal, "error", None)
            if error:
                out["error"] = _clip(str(error), 200)
        except Exception as exc:  # 个人库坏了也不能影响总览
            out["error"] = "读取个人库摘要失败：" + self._redact(type(exc).__name__)
        if not out["available"]:
            out["recap"] = out["recap"] or "个人库暂不可用"
        return out

    # ------------------------------------------------------------------ #
    # f) 可选 AI 建议
    # ------------------------------------------------------------------ #
    def _llm_settings(self) -> tuple[str, str, str]:
        llm = self.config.get("llm") or {}
        if not isinstance(llm, dict):
            llm = {}
        if llm.get("enabled") is False:
            # 用户在设置里关掉模型时，工作总览也不该偷偷调接口
            return "", "", ""
        key = str(llm.get("api_key") or "").strip()
        if not key:
            key = str(os.environ.get("DFY_API_KEY") or "").strip()
        if not key:
            secret = self.base_dir / ".secret_api_key"
            try:
                key = secret.read_text(encoding="utf-8").strip()
            except OSError:
                key = ""
        base_url = str(llm.get("base_url") or "https://api.deepseek.com/v1").strip().rstrip("/")
        model = str(llm.get("model") or "deepseek-chat").strip() or "deepseek-chat"
        return key, base_url, model

    def _make_suggestions(self, recent_projects, sessions, dsh, todos) -> dict:
        templates = self._template_suggestions(recent_projects, sessions, dsh, todos)
        cfg = self._cfg
        if cfg.get("ai_suggestions") is False:
            return {"items": templates, "source": "template", "note": "配置里关闭了 AI 建议"}
        key, base_url, model = self._llm_settings()
        if not key:
            return {"items": templates, "source": "template", "note": "没有 API Key，用本地模板建议"}
        try:
            items = self._ai_suggestions(key, base_url, model, recent_projects, sessions, dsh, todos)
        except Exception as exc:  # 任何失败都静默回退，绝不打印 Key / 响应正文
            self.log(f"AI 工作建议不可用（{type(exc).__name__}），已回退模板建议")
            items = None
        if items:
            return {"items": items, "source": "ai", "note": "由本机配置的 LLM 生成（不含个人库正文）"}
        return {"items": templates, "source": "template", "note": "AI 建议失败，已回退模板"}

    @staticmethod
    def _interesting_sessions(sessions: list[dict], limit: int) -> list[dict]:
        """摘要里优先展示真正工作区的会话，临时/scratch 目录会话排后面。

        只影响短摘要的顺序，不改变 ``data["sessions"]`` 里按最近活动排序的原始列表。
        """
        def rank(item: dict) -> int:
            lowered = str(item.get("path") or "").lower()
            score = 0
            if "ai_workspace" in lowered:
                score -= 4
            if "/temp/" in lowered or "\\temp\\" in lowered or "/tmp/" in lowered:
                score += 2
            if "scratch" in lowered or "/scab/" in lowered or "worktrees" in lowered:
                score += 1
            return score

        return sorted(sessions, key=rank)[:limit]

    def _template_suggestions(self, recent_projects, sessions, dsh, todos) -> list[str]:
        """离线模板建议：至少 3 条，尽量贴当前数据。"""
        items: list[str] = []
        todo_files = (todos or {}).get("files") or []
        for todo in todo_files:
            if len(items) >= 1:
                break
            lines = todo.get("lines") or []
            first = next((line for line in lines if line and not line.startswith("#")), "")
            if first:
                items.append(f"接着处理 {todo.get('title', 'TODO')} 里的第一条：{_clip(first, 60)}")
            else:
                items.append(f"打开 {todo.get('title', 'TODO')} 把待办拆成 2–3 个能今天做掉的小步")
        for project in (recent_projects or [])[:2]:
            if len(items) >= 2:
                break
            items.append(
                f"继续收尾最近在动的 {project['path']}"
                f"（近 {self.recent_days:g} 天 {project['recent_files']} 个文件改动，"
                f"最近 {project['last_modified'] or '时间未知'}）"
            )
        if dsh and dsh.get("available"):
            if not dsh.get("connected"):
                items.append("DSH 状态心跳过期了：需要时重启 DSH 或检查状态桥插件")
            elif dsh.get("state") in ("failure",) or dsh.get("errorCount", 0) > 0:
                items.append(f"先处理 DSH 最近的错误（累计 {dsh.get('errorCount', 0)} 次，最近工具 {dsh.get('lastTool') or '未知'}）")
            elif dsh.get("state") == "tool":
                items.append("等当前这轮工具调用跑完，再决定下一个任务")
            elif dsh.get("state") == "running":
                items.append("DSH 正在干活：可以先整理待办或写一段复盘，别打断它")
        if sessions and len(items) < 3:
            session = self._interesting_sessions(sessions, 1)[0]
            items.append(f"回顾最近打开过的 {session.get('label') or session.get('path')}，确认有没有没收尾的改动")
        defaults = [
            "打开 docs/工作总览.html，对照最近 7 天改动确认方向没跑偏",
            "把今天做完的事补一条到个人库 journal（只读接入，不自动写）",
            "挑一个待办，先做 25 分钟再休息一下",
        ]
        for default in defaults:
            if len(items) >= 3:
                break
            items.append(default)
        return items[:5]

    def _ai_suggestions(self, key, base_url, model, recent_projects, sessions, dsh, todos) -> list[str] | None:
        """调用 OpenAI 兼容接口生成 3–5 条建议；失败返回 None。提示里不含个人库正文。"""
        context_lines: list[str] = []
        if recent_projects:
            context_lines.append("最近 7 天有改动的项目：")
            for project in recent_projects[:6]:
                context_lines.append(
                    f"- {project['path']}：{project['recent_files']} 个文件改动，最近 {project['last_modified']}"
                )
        else:
            context_lines.append("最近 7 天没扫到明显的项目改动。")
        if todos and todos.get("files"):
            context_lines.append("显式待办文件：")
            for todo in todos["files"]:
                head = " / ".join((todo.get("lines") or [])[:3])
                context_lines.append(f"- {todo.get('title')}：{_clip(head, 120)}")
        else:
            context_lines.append("没有显式的 TODO 文件。")
        if dsh and dsh.get("available"):
            context_lines.append(
                f"DSH 状态：{dsh.get('state_text')}，最近工具 {dsh.get('lastTool') or '无'}，"
                f"完成 {dsh.get('doneCount', 0)}，错误 {dsh.get('errorCount', 0)}"
            )
        else:
            context_lines.append("DSH 状态：未连接。")
        if sessions:
            context_lines.append("最近打开过：" + "、".join(item.get("label") or item.get("path", "") for item in sessions[:3]))

        messages = [
            {
                "role": "system",
                "content": (
                    "你是用户桌面上的工作助理。根据用户本机的工作痕迹，给出 3 到 5 条具体、"
                    "可执行的「下一步建议」。要求：简体中文；每条一行，以「- 」开头；每条 12 到 40 字；"
                    "只依据给的信息，不编造个人背景；不要解释，不要输出标题。"
                ),
            },
            {"role": "user", "content": "\n".join(context_lines)},
        ]
        body = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.4,
            "max_tokens": 300,
        }).encode("utf-8")
        request = urllib.request.Request(
            base_url + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + key,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        content = ""
        try:
            content = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return None
        items: list[str] = []
        raw_lines = [line.strip() for line in str(content).splitlines() if line.strip()]
        for line in raw_lines:
            line = _BULLET_RE.sub("", line).strip()
            line = re.sub(r"^\d+[.、)]\s*", "", line).strip()
            if not line:
                continue
            if len(line) > 120:
                line = _clip(line, 120)
            if line not in items:
                items.append(line)
            if len(items) >= 5:
                break
        return items if len(items) >= 3 else None

    def _redact(self, text: str) -> str:
        """日志里抹掉 Key（含通用 sk- 形态），任何个人库正文都不会走到这里。"""
        text = str(text or "")
        key, _base_url, _model = self._llm_settings()
        if key:
            text = text.replace(key, "***")
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", text)
        text = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", text)
        return text

    # ------------------------------------------------------------------ #
    # 输出
    # ------------------------------------------------------------------ #
    def _write_outputs(self, data: dict) -> None:
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        self._atomic_write_text(self.json_path, payload + "\n")
        self.html_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(self.html_path, self._render_html(data))

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)

    # ------------------------------------------------------------------ #
    # 给桌宠气泡 / Agent 工具用的短摘要
    # ------------------------------------------------------------------ #
    def text_summary(self, max_chars: int = 500) -> str:
        """中文短摘要（≤ max_chars）：最近在做什么、待办几条、DSH 状态。"""
        return self._text_summary_from(self.data, max_chars)

    def _text_summary_from(self, data, max_chars: int = 500) -> str:
        try:
            max_chars = max(40, int(max_chars))
        except (TypeError, ValueError):
            max_chars = 500
        if not isinstance(data, dict):
            return _clip("还没有工作总览数据，先生成一次：python tools/build_overview.py --force", max_chars)

        parts: list[str] = []
        projects = data.get("recent_projects") or []
        if projects:
            head = projects[:3]
            detail = "、".join(
                f"{item.get('path')}（近 {data.get('recent_days', 7):g} 天 {item.get('recent_files', 0)} 个文件）"
                for item in head
            )
            parts.append("最近在动：" + detail)
        else:
            parts.append("最近 7 天没扫到明显的项目改动")
        sessions = data.get("sessions") or []
        if sessions:
            labels = "、".join(
                item.get("label") or item.get("path", "")
                for item in self._interesting_sessions(sessions, 2)
            )
            parts.append("最近打开过：" + labels)

        todos = data.get("todos") or {}
        todo_files = todos.get("files") or []
        if todo_files:
            names = "、".join(item.get("title", "TODO") for item in todo_files)
            first_line = ""
            for item in todo_files:
                for line in item.get("lines") or []:
                    if line and not line.startswith("#"):
                        first_line = _clip(line, 40)
                        break
                if first_line:
                    break
            text = f"待办线索：{len(todo_files)} 个文件（{names}）"
            if first_line:
                text += "，第一条：" + first_line
            parts.append(text)
        else:
            parts.append("待办线索：未发现显式 TODO 文件")

        dsh = data.get("dsh") or {}
        if dsh.get("available") and dsh.get("connected"):
            parts.append(
                f"DSH：{dsh.get('state_text') or dsh.get('state')}"
                f"（最近工具 {dsh.get('lastTool') or '无'}，完成 {dsh.get('doneCount', 0)}，"
                f"错误 {dsh.get('errorCount', 0)}）"
            )
        elif dsh.get("available"):
            parts.append(f"DSH：未连接（{dsh.get('reason') or '心跳过期'}）")
        else:
            parts.append("DSH：未连接")

        personal = data.get("personal") or {}
        if personal.get("available") and personal.get("recap"):
            parts.append(_clip(personal["recap"], 60))

        suggestions = (data.get("suggestions") or {}).get("items") or []
        if suggestions:
            parts.append("下一步建议：" + _clip(suggestions[0], 80))

        parts.append("生成于 " + (data.get("generated_at") or "未知时间"))
        return _clip("；".join(parts), max_chars)

    # ------------------------------------------------------------------ #
    # HTML（自包含、白底、无外部脚本/样式）
    # ------------------------------------------------------------------ #
    def _render_html(self, data: dict) -> str:
        esc = html.escape
        generated_at = esc(str(data.get("generated_at") or ""))
        recent_days = data.get("recent_days", 7)
        projects = data.get("recent_projects") or []
        sessions = data.get("sessions") or []
        dsh = data.get("dsh") or {}
        todos = data.get("todos") or {}
        personal = data.get("personal") or {}
        suggestions = data.get("suggestions") or {}
        sources = data.get("sources") or []

        rows = []
        for index, project in enumerate(projects, 1):
            count_prefix = "≥" if project.get("truncated") else ""
            rows.append(
                "<tr>"
                f"<td class=\"num\">{index}</td>"
                f"<td>{esc(str(project.get('path') or ''))}</td>"
                f"<td class=\"num\">{count_prefix}{int(project.get('recent_files') or 0)}</td>"
                f"<td class=\"num\">{count_prefix}{int(project.get('total_files') or 0)}</td>"
                f"<td>{esc(str(project.get('last_modified') or '未知'))}</td>"
                "</tr>"
            )
        if not rows:
            rows.append("<tr><td colspan=\"5\" class=\"empty\">最近 7 天没有扫到文件改动</td></tr>")

        session_rows = []
        for session in sessions:
            session_rows.append(
                "<tr>"
                f"<td>{esc(str(session.get('label') or ''))}</td>"
                f"<td class=\"mono\">{esc(str(session.get('path') or ''))}</td>"
                f"<td>{esc(str(session.get('last_active') or ''))}</td>"
                "</tr>"
            )
        if not session_rows:
            session_rows.append("<tr><td colspan=\"3\" class=\"empty\">最近 3 天没有 DSH 会话活动</td></tr>")

        if dsh.get("available"):
            if dsh.get("connected"):
                dsh_class = "pill ok"
                dsh_text = f"已连接：{dsh.get('state_text') or dsh.get('state')}"
            else:
                dsh_class = "pill warn"
                dsh_text = "状态文件在，但心跳已过期"
            dsh_block = (
                f"<p><span class=\"{dsh_class}\">{esc(dsh_text)}</span></p>"
                "<table><tbody>"
                f"<tr><th>状态</th><td>{esc(str(dsh.get('state') or '未知'))}</td></tr>"
                f"<tr><th>最近工具</th><td>{esc(str(dsh.get('lastTool') or '无'))}</td></tr>"
                f"<tr><th>完成 / 错误</th><td>{int(dsh.get('doneCount') or 0)} / {int(dsh.get('errorCount') or 0)}</td></tr>"
                f"<tr><th>等待审批</th><td>{'是' if dsh.get('approvalOpen') else '否'}</td></tr>"
                f"<tr><th>更新时间</th><td>{esc(str(dsh.get('updated_at') or '未知'))}</td></tr>"
                "</tbody></table>"
            )
            if dsh.get("reason"):
                dsh_block += f"<p class=\"note\">{esc(str(dsh.get('reason')))}</p>"
        else:
            dsh_block = f"<p class=\"empty\">{esc(str(dsh.get('reason') or 'DSH 状态不可用'))}</p>"

        todo_blocks = []
        for todo in todos.get("files") or []:
            lines = "\n".join(todo.get("lines") or [])
            note = "（只显示前 30 行）" if todo.get("truncated") else ""
            todo_blocks.append(
                f"<h3>{esc(str(todo.get('title') or 'TODO'))}"
                f"<span class=\"mono small\"> {esc(str(todo.get('rel') or ''))}</span></h3>"
                f"<pre>{esc(lines) if lines else '（空文件）'}</pre>"
                f"<p class=\"note\">{esc(note) if note else ''}</p>"
            )
        if not todo_blocks:
            todo_blocks.append(
                f"<p class=\"empty\">{esc(str(todos.get('hint') or '未发现显式 TODO 文件'))}</p>"
                "<p class=\"note\">说明：这个总览不会猜待办；只把明确的 TODO 文件列出来。</p>"
            )

        suggestion_items = "".join(
            f"<li>{esc(str(item))}</li>" for item in (suggestions.get("items") or [])
        ) or "<li>暂无建议</li>"

        personal_block = (
            f"<p>{esc(str(personal.get('recap') or '个人库暂不可用'))}</p>"
            "<p class=\"note\">个人库只读；页面只摘录短摘要，不嵌入原文。</p>"
        )
        if personal.get("error"):
            personal_block += f"<p class=\"note\">{esc(str(personal['error']))}</p>"

        summary_paragraph = esc(self._text_summary_from(data, 500))
        source_items = "".join(f"<li>{esc(str(item))}</li>" for item in sources)

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>大肥鱼 · 工作总览</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 28px 18px 48px; background: #ffffff; color: #1f2328;
    font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
    line-height: 1.65; font-size: 15px;
  }}
  main {{ max-width: 980px; margin: 0 auto; }}
  header {{ border-bottom: 2px solid #1f2328; padding-bottom: 12px; margin-bottom: 8px; }}
  h1 {{ font-size: 26px; margin: 0 0 6px; }}
  h2 {{ font-size: 19px; margin: 30px 0 10px; padding-left: 9px; border-left: 4px solid #4c6ef5; }}
  h3 {{ font-size: 15px; margin: 16px 0 6px; }}
  p {{ margin: 8px 0; }}
  .meta {{ color: #57606a; font-size: 13px; margin: 4px 0 0; }}
  .note {{ color: #57606a; font-size: 13px; }}
  .empty {{ color: #8b949e; }}
  .small {{ font-size: 12px; }}
  .mono {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 12.5px; word-break: break-all; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 14px; }}
  th, td {{ border: 1px solid #e4e7eb; padding: 7px 10px; text-align: left; vertical-align: top; }}
  thead th {{ background: #f6f7f9; font-weight: 600; }}
  tbody th {{ width: 130px; background: #fafbfc; font-weight: 500; }}
  .num {{ text-align: right; white-space: nowrap; }}
  pre {{
    background: #f6f8fa; border: 1px solid #e4e7eb; border-radius: 6px;
    padding: 10px 12px; overflow-x: auto; font-size: 13px; line-height: 1.5;
    white-space: pre-wrap; word-break: break-word;
  }}
  ul, ol {{ margin: 8px 0; padding-left: 24px; }}
  li {{ margin: 4px 0; }}
  .pill {{ display: inline-block; border-radius: 999px; padding: 2px 10px; font-size: 13px; }}
  .pill.ok {{ background: #e6f4ea; color: #1a7f37; }}
  .pill.warn {{ background: #fff4e5; color: #9a6700; }}
  footer {{ margin-top: 34px; padding-top: 14px; border-top: 1px solid #e4e7eb; }}
  footer h2 {{ border-left-color: #8b949e; }}
  .generated {{ color: #8b949e; font-size: 12.5px; }}
</style>
</head>
<body>
<main>
  <header>
    <h1>大肥鱼 · 工作总览</h1>
    <p class="meta">生成时间：{generated_at}　｜　数据全部来自本机，只读、不上传</p>
    <p class="meta">下一次自动刷新间隔：{esc(_num_text(self.min_interval_minutes))} 分钟（可用 <span class="mono">python tools/build_overview.py --force</span> 手动刷新）</p>
  </header>

  <section>
    <h2>一句话摘要</h2>
    <p>{summary_paragraph}</p>
  </section>

  <section>
    <h2>最近做过 / 正在做（近 {esc(_num_text(recent_days))} 天文件改动）</h2>
    <table>
      <thead><tr><th>#</th><th>项目（工作区/目录）</th><th>近 {esc(_num_text(recent_days))} 天改动文件</th><th>文件总数</th><th>最近修改</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <p class="note">统计方式是文件 mtime，不读文件内容；已跳过 node_modules / .git / 缓存 / 依赖等目录。</p>
  </section>

  <section>
    <h2>DSH 实时状态</h2>
    {dsh_block}
  </section>

  <section>
    <h2>最近 DSH 会话活动（只看路径和时间）</h2>
    <table>
      <thead><tr><th>工作区</th><th>解码后的路径</th><th>最近活动</th></tr></thead>
      <tbody>{''.join(session_rows)}</tbody>
    </table>
    <p class="note">会话目录名里编码了工作区路径；这里只解码目录名、读取目录 mtime，不打开会话正文。
      默认跳过系统临时目录里的 Agent scratch 会话（可在 config 的 overview.include_temp_sessions 打开）。</p>
  </section>

  <section>
    <h2>待办线索（{esc(str(todos.get('count') or 0))} 个文件）</h2>
    {''.join(todo_blocks)}
  </section>

  <section>
    <h2>下一步建议</h2>
    <p class="note">来源：{esc(str(suggestions.get('source') or 'template'))}
      {('，' + esc(str(suggestions.get('note')))) if suggestions.get('note') else ''}</p>
    <ol>{suggestion_items}</ol>
  </section>

  <section>
    <h2>我记得你</h2>
    {personal_block}
  </section>

  <footer>
    <h2>数据来源</h2>
    <ul>{source_items}</ul>
    <p class="generated">本页由大肥鱼桌宠自动生成，自包含 HTML（无外部脚本 / 样式 / 图片）。
      耗时 {esc(str(data.get('elapsed_s', 0)))} 秒。生成时间：{generated_at}。</p>
  </footer>
</main>
</body>
</html>
"""


def _parse_iso_ts(text) -> float:
    """把 generated_at（本地时间字符串）解析回时间戳；失败返回 0。"""
    if not text:
        return 0.0
    try:
        return datetime.strptime(str(text), "%Y-%m-%d %H:%M:%S").timestamp()
    except (ValueError, TypeError):
        return 0.0


__all__ = ["WorkOverview", "decode_session_dir"]
