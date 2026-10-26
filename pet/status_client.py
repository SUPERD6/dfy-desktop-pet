# -*- coding: utf-8 -*-
"""读取 DSH 插件「dsh-whale-bridge」写出的本地状态文件。

插件把 DSH Agent 的工作状态（思考/调用工具/成功/失败/等审批）以固定 schema
写到一个 JSON 文件（默认 `~/.dsh/whale-bridge/status.json`），本模块负责：

- 每秒 poll 一次文件；
- 校验 schema 与心跳（updatedAt 过期视为 DSH 已断开）；
- 只在「状态 / 计数 / 连接性」发生变化时返回一个事件，避免重复触发动画和语音。

文件 schema（由插件保证，隐私白名单，不含提示词/路径/输出）：
    {"ok": true, "state": "idle|running|tool|success|failure|approval",
     "lastTool": str|null, "doneCount": int, "errorCount": int,
     "approvalOpen": bool, "updatedAt": epoch_ms, "version": 1}
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

VALID_STATES = ("idle", "running", "tool", "success", "failure", "approval")


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _epoch_ms(value) -> float | None:
    """把 updatedAt 归一成毫秒时间戳；认数字（秒/毫秒）和 ISO 字符串。"""
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return None
        return number * 1000.0 if number < 1e12 else number
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return float(text) if text.replace(".", "", 1).isdigit() else None
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.timestamp() * 1000.0
        except ValueError:
            return None
    return None


class DshStatusWatcher:
    """文件轮询器：没有插件时安静地什么都不做。"""

    def __init__(self, path: str | Path, log=print, stale_after_s: float = 15.0):
        self.path = Path(path).expanduser()
        self.log = log if callable(log) else print
        self.stale_after_s = max(3.0, float(stale_after_s))
        self.connected = False
        self.state = "idle"
        self.last_tool: str | None = None
        self.done_count = 0
        self.error_count = 0
        self.approval_open = False
        self.last_payload: dict | None = None
        self._missing_warned = False
        self._bad_warned = False

    # ---------- 对外 ----------
    def poll(self) -> dict | None:
        """返回状态变化事件；无变化返回 None。

        事件格式：
            {"state": "running|...|disconnected", "previous": "idle|...",
             "connected": bool, "doneCount": int, "errorCount": int,
             "approvalOpen": bool, "lastTool": str|None, "updatedAt": float|None}
        """
        payload = self._read()
        if payload is None:
            return self._set_disconnected()
        if payload.get("_parse_error"):
            # 读到半截/坏 JSON：当成一次瞬时抖动，不断开连接（插件是原子写，正常不会发生）
            return None

        state = str(payload.get("state") or "").strip().lower()
        if state not in VALID_STATES:
            if not self._bad_warned:
                self._bad_warned = True
                self.log(f"DSH 状态文件里的 state 不认识：{state!r}，先忽略")
            return None
        self._bad_warned = False
        self._missing_warned = False

        updated_ms = _epoch_ms(payload.get("updatedAt"))
        now_ms = time.time() * 1000.0
        if updated_ms is not None and now_ms - updated_ms > self.stale_after_s * 1000.0:
            return self._set_disconnected(payload, reason="心跳过期")

        previous = (self.state, self.done_count, self.error_count)
        was_connected = self.connected
        self.connected = True
        self.state = state
        self.last_tool = payload.get("lastTool") if isinstance(payload.get("lastTool"), str) else None
        self.done_count = _as_int(payload.get("doneCount"))
        self.error_count = _as_int(payload.get("errorCount"))
        self.approval_open = bool(payload.get("approvalOpen"))
        self.last_payload = payload
        changed = (not was_connected
                   or (self.state, self.done_count, self.error_count) != previous)
        if not changed:
            return None
        return self._event(self.state, previous[0], updated_ms)

    def snapshot(self) -> dict:
        return {
            "connected": self.connected,
            "state": self.state,
            "lastTool": self.last_tool,
            "doneCount": self.done_count,
            "errorCount": self.error_count,
            "approvalOpen": self.approval_open,
        }

    def describe(self) -> str:
        if not self.connected:
            return "DSH 状态：未连接（插件未安装 / DSH 没启动）"
        names = {
            "idle": "空闲", "running": "思考/干活中", "tool": "调用工具",
            "success": "刚完成一轮", "failure": "刚出错", "approval": "等待审批",
        }
        extra = f"，最近工具 {self.last_tool}" if self.last_tool else ""
        return (f"DSH 状态：{names.get(self.state, self.state)}"
                f"（完成 {self.done_count}，错误 {self.error_count}{extra}）")

    # ---------- 内部 ----------
    def _read(self) -> dict | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if not self._missing_warned:
                self._missing_warned = True
                self.log(f"还没找到 DSH 状态文件：{self.path}（插件未安装或因重启暂时不在）")
            return None
        except OSError as exc:
            self.log(f"读取 DSH 状态文件失败：{exc}")
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {"_parse_error": True}
        return data if isinstance(data, dict) else {"_parse_error": True}

    def _set_disconnected(self, payload: dict | None = None, reason: str = "") -> dict | None:
        if not self.connected:
            return None
        previous = self.state
        self.connected = False
        self.state = "idle"
        self.approval_open = False
        if payload is not None:
            self.last_payload = payload
        if reason:
            self.log(f"DSH 状态断开：{reason}")
        return self._event("disconnected", previous, _epoch_ms((payload or {}).get("updatedAt")))

    def _event(self, state: str, previous: str, updated_ms: float | None) -> dict:
        return {
            "state": state,
            "previous": previous,
            "connected": self.connected,
            "doneCount": self.done_count,
            "errorCount": self.error_count,
            "approvalOpen": self.approval_open,
            "lastTool": self.last_tool,
            "updatedAt": updated_ms,
        }
