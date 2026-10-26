# -*- coding: utf-8 -*-
"""可选的视觉模型（OpenAI 兼容 chat/completions 多模态）。

默认关闭；只有用户在「视觉模型设置」里填了端点/模型/Key，才会把截图发给
用户自己指定的服务。Key 不进 config.json：优先 pet/.secret_vision_key，
其次环境变量 DFY_VISION_API_KEY，最后复用聊天 Key（同一端点时）。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path


def _clean(value) -> str:
    return str(value or "").strip()


def resolve_vision(config: dict, base_dir: Path) -> dict:
    cfg = config.get("vision") if isinstance(config.get("vision"), dict) else {}
    base_url = _clean(cfg.get("base_url")).rstrip("/")
    model = _clean(cfg.get("model"))
    key = _clean(cfg.get("api_key"))
    source = "config"
    if not key:
        try:
            key = (Path(base_dir) / ".secret_vision_key").read_text(encoding="utf-8").strip()
            source = "密钥文件 .secret_vision_key"
        except OSError:
            key = ""
    if not key:
        env_key = _clean(os.environ.get("DFY_VISION_API_KEY"))
        if env_key:
            key, source = env_key, "环境变量 DFY_VISION_API_KEY"
    if not key:
        try:
            key = (Path(base_dir) / ".secret_api_key").read_text(encoding="utf-8").strip()
            source = "聊天密钥 .secret_api_key"
        except OSError:
            key = ""
    return {"enabled": bool(cfg.get("enabled", False)), "base_url": base_url,
            "model": model, "api_key": key, "key_source": source,
            "timeout_s": int(cfg.get("timeout_s") or 60),
            "max_tokens": int(cfg.get("max_tokens") or 400),
            "ready": bool(base_url and model and key)}


def build_payload(image_bytes: bytes, prompt: str, model: str, max_tokens: int = 400) -> dict:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }


def describe_image(path: Path, prompt: str, config: dict, base_dir: Path, log=print) -> str:
    """调用视觉模型描述截图；未配置/失败返回空字符串，由调用方回退。"""
    info = resolve_vision(config, base_dir)
    if not info["ready"]:
        return ""
    try:
        image_bytes = Path(path).read_bytes()
        payload = build_payload(image_bytes, prompt, info["model"], info["max_tokens"])
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            info["base_url"].rstrip("/") + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + info["api_key"]},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=info["timeout_s"]) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        choices = data.get("choices") or []
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
            return str(content or "").strip()
    except Exception as exc:
        try:
            log("视觉模型调用失败：" + type(exc).__name__)
        except Exception:
            pass
    return ""
