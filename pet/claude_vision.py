# -*- coding: utf-8 -*-
"""用本机 Claude Code CLI 读截图（不需要额外 API Key）。

思路与 modlens 的 claude-cli 后端一致：让 Claude Code 用只读 Read 工具打开
本地图片，返回结构化 JSON 的 result。只读、只传用户主动「看看屏幕」的截图；
每次调用会消耗 Claude 订阅额度。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def find_claude() -> str:
    candidates = [
        Path.home() / ".local" / "bin" / "claude.exe",
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
    ]
    for item in candidates:
        if item.is_file():
            return str(item)
    for name in ("claude.exe", "claude"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def claude_see(image_path: Path, prompt: str = "", timeout: float = 90.0,
               model: str = "haiku", log=print) -> str:
    """读本地截图，返回中文描述；不可用/失败返回空字符串。"""
    path = Path(image_path)
    if not path.is_file():
        return ""
    exe = find_claude()
    if not exe:
        return ""
    ask = (prompt or "读取这张屏幕截图，用中文客观描述：主人在用什么程序、在做什么、"
                      "屏幕上值得注意的文字或界面。不要编造看不清的内容，不要复述隐私全文。")
    # 用相对文件名 + cwd，避免提示里塞绝对路径导致 Claude 找错位置
    task = f"{ask}\n\n图片文件在当前目录：{path.name}。请用 Read 工具打开它再回答，只输出描述。"
    try:
        proc = subprocess.run(
            [exe, "-p", task, "--allowedTools", "Read", "--output-format", "json",
             "--model", model],
            cwd=str(path.parent), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=max(20.0, float(timeout)),
            creationflags=CREATE_NO_WINDOW,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 有些版本会先打印日志行，取最后一个 JSON 对象
            lines = [line for line in raw.splitlines() if line.strip().startswith("{")]
            if not lines:
                return ""
            data = json.loads(lines[-1])
        if data.get("is_error"):
            return ""
        result = data.get("result")
        return str(result or "").strip()
    except Exception as exc:
        try:
            log("Claude 视觉读取失败：" + type(exc).__name__)
        except Exception:
            pass
        return ""
