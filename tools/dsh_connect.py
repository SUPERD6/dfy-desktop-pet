# -*- coding: utf-8 -*-
"""把大肥鱼接进 DeepSeek Harness（DSH）的聊天：在 DSH 里选她这个模型，说的话直接交给她的大脑。

原理：DSH 的「DeepSeek API Key」路由（配置项 llm-deepseek，provider = deepseek-official）可以改服务地址。
指到桌宠的共用大脑 http://127.0.0.1:8765 后，DSH 发来的 Anthropic Messages 请求由 pet/api_server.py 的
/v1/messages 接住：只取最后一句用户的话，用她自己的人设和记忆回答，聊的内容进她同一份记忆。
（实测本机 DSH 0.2.1-rc.3 走 Messages 格式；已用它的解析代码跑通流式与压缩请求。）

代价：这条 API Key 路由之后专给大肥鱼用，不能再用它连 DeepSeek 官方；你平时用的账号路由（deepseek-account）不受影响。

用法：
    python tools/dsh_connect.py            # 只检查，不改
    python tools/dsh_connect.py --apply    # 备份后追加配置（幂等、无 BOM）
    python tools/dsh_connect.py --undo     # 去掉这段配置
改完要重启 DSH；还要在 DSH 设置里给「DeepSeek API Key」填一个任意的本机 key（例如 sk-dafeiyu-local），
因为这条路由没有 key 不发请求，而桌宠的接口不校验它。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

PROFILE = Path.home() / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
BEGIN = "# >>> dafeiyu-pet: DSH 聊天接入大肥鱼（tools/dsh_connect.py 管理，--undo 可去掉）"
END = "# <<< dafeiyu-pet"


def display_name() -> str:
    """模型在 DSH 里显示的名字：用桌宠配置里的 display_name，没有就叫大肥鱼。"""
    try:
        import json
        config = json.loads((Path(__file__).resolve().parents[1] / "pet" / "config.json").read_text(encoding="utf-8"))
        return str(config.get("display_name") or "大肥鱼")
    except Exception:
        return "大肥鱼"


def block(base_url: str, name: str) -> str:
    return "\n".join([
        BEGIN,
        "- id: llm-deepseek",
        "  name: '@deepseek-ai/dsh-llm-deepseek-api-key'",
        "  config:",
        f"    baseURL: {base_url}",
        "    models:",
        "      - id: dfy-companion",
        f"        name: {name}（桌宠）",
        END,
    ])


def read(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, newline


def strip_block(text: str) -> str:
    start = text.find(BEGIN)
    if start < 0:
        return text
    end = text.find(END, start)
    end = len(text) if end < 0 else end + len(END)
    return (text[:start].rstrip() + "\n" + text[end:].lstrip("\r\n")).rstrip() + "\n"


def write(path: Path, text: str, newline: str) -> None:
    backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-dafeiyu")
    backup.write_bytes(path.read_bytes())
    data = text.replace("\r\n", "\n").replace("\n", newline).encode("utf-8")  # 不写 BOM：DSH 读到 BOM 会起不来
    path.write_bytes(data)
    print(f"已改：{path}（备份 {backup.name}）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--profile", type=Path, default=PROFILE)
    args = parser.parse_args()
    if not args.profile.exists():
        print(f"没找到 DSH 配置：{args.profile}")
        return 1
    text, newline = read(args.profile)
    present = BEGIN in text
    if args.undo:
        if not present:
            print("没有接入配置，不用撤销。")
            return 0
        write(args.profile, strip_block(text), newline)
        print("已撤销。重启 DSH 后 API Key 路由回到 DeepSeek 官方。")
        return 0
    if args.apply:
        new = strip_block(text).rstrip() + "\n" + block(args.base_url, display_name()) + "\n"
        if new == text:
            print("已经接好了，没有改动。")
            return 0
        write(args.profile, new, newline)
        print("下一步：1) 重启 DSH；2) DSH 设置里给「DeepSeek API Key」填一个任意本机 key（如 sk-dafeiyu-local）；"
              f"3) 新开对话，模型选「{display_name()}（桌宠）」；桌宠要开着（共用大脑在 8765）。")
        return 0
    print("状态：" + ("已接入" if present else "未接入") + f"（{args.profile}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
