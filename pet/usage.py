# -*- coding: utf-8 -*-
"""DeepSeek 余额查询（参考 dsh 小鲸鱼记账 / DeskLLMPet 的余额气泡）。

只读：调用 https://api.deepseek.com/user/balance，不产生模型费用。
"""
from __future__ import annotations

import json
import urllib.request


def parse_balance(payload: dict) -> dict:
    infos = (payload or {}).get("balance_infos") or []
    if not infos:
        return {"available": False, "text": "余额接口没有返回余额。"}
    info = infos[0]
    currency = str(info.get("currency") or "CNY")
    try:
        total = float(info.get("total_balance") or 0)
    except Exception:
        total = 0.0
    available = bool(payload.get("is_available"))
    symbol = "¥" if currency == "CNY" else ("$" if currency == "USD" else currency + " ")
    text = f"API 余额：{symbol}{total:.2f}（{'可用' if available else '不可用'}）"
    return {"available": available, "currency": currency, "total": total,
            "text": text, "raw": payload}


def query_balance(api_key: str, base_url: str = "https://api.deepseek.com/v1",
                  timeout: float = 20.0) -> dict:
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("没有 API Key")
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    request = urllib.request.Request(
        root + "/user/balance",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return parse_balance(payload)
