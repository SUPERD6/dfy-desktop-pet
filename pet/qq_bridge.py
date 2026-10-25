# -*- coding: utf-8 -*-
"""通到 QQ：用 OneBot 11 HTTP 后端（NapCat / SnowLuma）让桌宠和 QQ 双向聊天。

思路参考 Yuuyuko-uu/dsh-qq-bridge（MIT）的「不用 BOT、只走本机 OneBot HTTP」做法：
- QQ → 桌宠：轮询 get_friend_msg_history / get_group_msg_history，取新消息交给 Brain.reply；
- 桌宠 → QQ：把回复用 send_private_msg / send_group_msg 发回去；
- 只聊天，**不调用 Agent 工具**，所以 QQ 侧不能操作电脑；
- 默认关闭，白名单为空时不回任何人（先配置再用）。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:3000"
MAX_MESSAGE_CHARS = 1200


class QQError(RuntimeError):
    pass


def extract_text(message) -> str:
    """把 OneBot 的消息（字符串 CQ 码或分段数组）压成纯文本；图片/表情转成占位符。"""
    parts: list[str] = []
    if isinstance(message, str):
        raw = message
        # 去掉 CQ 码，保留参数里的可读内容（如图片就写 [图片]）
        import re
        pieces = re.split(r"(\[CQ:[^\]]+\])", raw)
        for piece in pieces:
            if not piece:
                continue
            if piece.startswith("[CQ:"):
                if piece.startswith("[CQ:image") or piece.startswith("[CQ:mface"):
                    parts.append("[图片]")
                elif piece.startswith("[CQ:face"):
                    parts.append("[表情]")
                elif piece.startswith("[CQ:at"):
                    parts.append("@")
            else:
                parts.append(piece)
        return "".join(parts).strip()
    if isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict):
                continue
            kind = str(segment.get("type") or "")
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
            if kind == "text":
                parts.append(str(data.get("text") or ""))
            elif kind in ("image", "mface"):
                parts.append("[图片]")
            elif kind == "face":
                parts.append("[表情]")
            elif kind == "at":
                parts.append("@" + str(data.get("qq") or ""))
        return "".join(parts).strip()
    return ""


class QQBridge:
    """一个最小可用的 OneBot 11 HTTP 客户端 + 轮询循环。"""

    def __init__(self, config: dict, on_message=None, log=print):
        cfg = config.get("qq") or {}
        self.base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.token = str(cfg.get("token") or "")
        self.poll_seconds = max(2.0, float(cfg.get("poll_seconds") or 4))
        self.private_user = str(cfg.get("private_user") or "").strip()
        self.group_id = str(cfg.get("group_id") or "").strip()
        self.whitelist = {str(item).strip() for item in (cfg.get("whitelist") or []) if str(item).strip()}
        self.on_message = on_message
        self.log = log if callable(log) else print
        self.self_id = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._seen: set[str] = set()
        self._primed = False
        self.last_error = ""

    # ---------- 对外 ----------
    @property
    def running(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def start(self) -> tuple[bool, str]:
        if self.running:
            return (True, "已经在轮询了")
        info = self._api("get_login_info", {})
        self.self_id = str(info.get("user_id") or "")
        if not self.self_id:
            return (False, "连上了后端，但没拿到登录 QQ（get_login_info 为空）")
        self._stop.clear()
        self._primed = False
        self._thread = threading.Thread(target=self._loop, name="qq-bridge", daemon=True)
        self._thread.start()
        return (True, f"已连上 QQ：{info.get('nickname') or ''}（{self.self_id}）")

    def stop(self) -> None:
        self._stop.set()

    def send_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS - 1] + "…"
        if self.group_id and not self.private_user:
            self._api("send_group_msg", {"group_id": self._as_int(self.group_id), "message": text})
        elif self.private_user:
            self._api("send_private_msg", {"user_id": self._as_int(self.private_user), "message": text})
        else:
            raise QQError("没有配置 private_user / group_id，不知道发给谁")

    # ---------- 内部 ----------
    @staticmethod
    def _as_int(value: str) -> int | str:
        try:
            return int(str(value))
        except Exception:
            return str(value)

    def _api(self, action: str, payload: dict, timeout: float = 10.0):
        url = f"{self.base_url}/{action}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            self.last_error = f"{action} 失败：{exc}"
            raise QQError(self.last_error) from exc
        if str(data.get("status") or "").lower() not in ("ok", "async"):
            raise QQError(f"{action} 返回异常：{str(data)[:160]}")
        return data.get("data") or {}

    def _is_allowed(self, sender_id: str) -> bool:
        if not sender_id or sender_id == self.self_id:
            return False
        if self.whitelist:
            return sender_id in self.whitelist
        return bool(self.private_user and sender_id == self.private_user)

    def _fetch(self) -> list[dict]:
        messages: list[dict] = []
        if self.private_user:
            data = self._api("get_friend_msg_history",
                             {"user_id": self._as_int(self.private_user), "count": 20})
            for item in data.get("messages") or []:
                item = dict(item)
                item.setdefault("_chat", "private")
                messages.append(item)
        if self.group_id:
            data = self._api("get_group_msg_history",
                             {"group_id": self._as_int(self.group_id), "count": 30})
            for item in data.get("messages") or []:
                item = dict(item)
                item.setdefault("_chat", "group")
                messages.append(item)
        return messages

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                for item in self._fetch():
                    message_id = str(item.get("message_id") or item.get("id") or "")
                    if not message_id or message_id in self._seen:
                        continue
                    self._seen.add(message_id)
                    if len(self._seen) > 500:
                        self._seen = set(list(self._seen)[-300:])
                    if not self._primed:
                        continue  # 第一轮只记旧的，不回历史消息
                    sender = str((item.get("sender") or {}).get("user_id") or item.get("user_id") or "")
                    if not self._is_allowed(sender):
                        continue
                    text = extract_text(item.get("message") or item.get("raw_message"))
                    if not text:
                        continue
                    self.log(f"QQ 收到 {sender}：{text[:80]}")
                    try:
                        reply = self.on_message(text) if callable(self.on_message) else ""
                    except Exception as exc:
                        self.log(f"QQ 回复生成失败：{exc}")
                        reply = "呜……本鲸刚刚走神了，再说一遍嘛。"
                    if reply:
                        try:
                            self.send_text(str(reply))
                        except Exception as exc:
                            self.log(f"QQ 发送失败：{exc}")
                self._primed = True
            except Exception as exc:
                self.log(f"QQ 轮询失败：{exc}")
            self._stop.wait(self.poll_seconds)
