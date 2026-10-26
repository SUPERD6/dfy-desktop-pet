# -*- coding: utf-8 -*-
"""Seedance 文生/图生视频（火山方舟 Ark API）。

需要用户自己的 ARK_API_KEY（写在 pet/.secret_ark_key 或环境变量）。
这是异步任务：创建 → 轮询 → 下载 mp4；没有 Key 时只提示，不会偷偷调用。
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"
MAX_DOWNLOAD = 400 * 1024 * 1024


def resolve_key(base_dir: Path, config: dict | None = None) -> str:
    cfg = (config or {}).get("video_gen") if isinstance(config, dict) else {}
    key = str((cfg or {}).get("api_key") or "").strip()
    if key:
        return key
    try:
        key = (Path(base_dir) / ".secret_ark_key").read_text(encoding="utf-8").strip()
        if key:
            return key
    except OSError:
        pass
    return str(os.environ.get("ARK_API_KEY") or "").strip()


def _model(config: dict | None = None) -> str:
    cfg = (config or {}).get("video_gen") if isinstance(config, dict) else {}
    return str((cfg or {}).get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _request(url: str, key: str, data: dict | None = None, method: str = "GET",
             timeout: float = 60.0) -> dict:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def create_task(prompt: str, key: str, config: dict | None = None,
                image_path: Path | None = None, first_frame: str = "") -> dict:
    """创建视频任务，返回 Ark 原始响应（含 id）。"""
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("视频提示词不能为空")
    content = [{"type": "text", "text": prompt[:2000]}]
    if image_path and Path(image_path).is_file():
        import base64

        raw = Path(image_path).read_bytes()
        suffix = Path(image_path).suffix.lower().lstrip(".") or "png"
        data_url = f"data:image/{suffix};base64," + base64.b64encode(raw).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": data_url}, "role": "first_frame"})
    elif first_frame:
        content.append({"type": "image_url", "image_url": {"url": first_frame}, "role": "first_frame"})
    cfg = (config or {}).get("video_gen") if isinstance(config, dict) else {}
    cfg = cfg or {}
    payload = {
        "model": _model(config),
        "content": content,
        "ratio": str(cfg.get("ratio") or ("adaptive" if image_path or first_frame else "16:9")),
        "duration": int(cfg.get("duration") or 5),
        "resolution": str(cfg.get("resolution") or "720p"),
        "generate_audio": bool(cfg.get("generate_audio", True)),
    }
    return _request(BASE_URL + "/contents/generations/tasks", key, payload, method="POST")


def get_task(task_id: str, key: str) -> dict:
    return _request(BASE_URL + "/contents/generations/tasks/" + urllib.parse.quote(task_id), key)


def wait_task(task_id: str, key: str, timeout: float = 600.0, interval: float = 10.0,
              log=print) -> dict:
    """轮询到 succeeded/failed/expired；超时返回最后一次状态。"""
    deadline = time.monotonic() + max(30.0, timeout)
    last: dict = {}
    while time.monotonic() < deadline:
        last = get_task(task_id, key)
        status = str(last.get("status") or "")
        if status in ("succeeded", "failed", "expired"):
            return last
        try:
            log(f"Seedance：{status or 'queued'}…")
        except Exception:
            pass
        time.sleep(max(3.0, interval))
    return last


def download(video_url: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"seedance-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    req = urllib.request.Request(video_url, headers={"User-Agent": "MingyuePet/1.0"})
    with urllib.request.urlopen(req, timeout=180) as response, target.open("wb") as handle:
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD:
                raise ValueError("视频超过下载上限")
            handle.write(chunk)
    return target
