# -*- coding: utf-8 -*-
"""把桌宠和本机的「DSH 抖音助手」接起来：读它的人格卡/日报，双向同步人格。

数据目录（DSH 抖音插件）：
  %USERPROFILE%/.dsh/douyin-browser/memory/persona/role-card.md
  %USERPROFILE%/.dsh/douyin-browser/memory/persona/state.json
  %USERPROFILE%/.dsh/douyin-browser/memory/reports/videos/*.md
本模块只读 + 在用户点「同步人设」时写 role-card（写前备份 .bak），
不碰登录、Cookie、私信和许可名单。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SYNC_START = "<!-- MINGYUE_PERSONA_SYNC:START -->"
SYNC_END = "<!-- MINGYUE_PERSONA_SYNC:END -->"
GEN_START = "<!-- DOUYIN_PERSONA_GENERATED:START -->"


class DouyinBridge:
    def __init__(self, root: Path | None = None, log=print):
        home = Path.home()
        self.root = Path(root) if root else home / ".dsh" / "douyin-browser"
        self.log = log if callable(log) else print
        self.role_path = self.root / "memory" / "persona" / "role-card.md"
        self.state_path = self.root / "memory" / "persona" / "state.json"
        self.reports_dir = self.root / "memory" / "reports" / "videos"

    @property
    def available(self) -> bool:
        return self.root.exists()

    def latest_report(self) -> Path | None:
        if not self.reports_dir.exists():
            return None
        files = sorted(self.reports_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None

    def latest_observation(self) -> dict | None:
        """最新一条她实际看过的作品（来自抖音助手共享 state.json）。"""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            observations = data.get("observations") or []
            if observations:
                item = observations[-1]
                return {
                    "title": str(item.get("title") or "")[:160],
                    "author": str(item.get("author") or ""),
                    "url": str(item.get("url") or ""),
                }
        except Exception:
            pass
        return None

    def context_line(self, max_chars: int = 220) -> str:
        """给桌宠聊天用的「她刚才在抖音看了什么」；主人问为什么看时按这个回答。"""
        obs = self.latest_observation()
        if not obs:
            return ""
        line = f"你刚在抖音看了《{obs['title']}》"
        if obs["author"]:
            line += f"（作者{obs['author']}）"
        line += "；如果主人问你为什么刷这个，就用你的兴趣/当时判断如实说，不要编造没看过的内容。"
        return line[:max_chars]

    def report_summary(self, max_chars: int = 220) -> str:
        path = self.latest_report()
        if path is None:
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        # 取日报里最有信息量的两行
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith(("<!--", "#"))]
        body = "；".join(lines[:3])
        body = re.sub(r"\s+", " ", body)
        return f"{path.stem}：{body[:max_chars]}"

    def status_text(self) -> str:
        if not self.available:
            return "抖音助手目录还没出现（插件可能没装/没运行过）"
        pieces = ["抖音助手：已接入"]
        report = self.report_summary(120)
        if report:
            pieces.append(report)
        else:
            pieces.append("还没有日报")
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            pieces.append(f"兴趣 {len(data.get('interests') or [])} 条")
        except Exception:
            pass
        return "｜".join(pieces)

    def sync_persona(self, persona_path: Path) -> tuple[bool, str]:
        """把桌宠的人设摘要写进抖音助手的 role-card（保留它自己的生成区和用户文字）。"""
        if not persona_path.exists():
            return (False, "找不到桌宠的人设文件")
        try:
            persona = persona_path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception as exc:
            return (False, f"读人设失败：{exc}")
        block = (f"{SYNC_START}\n## 共享人格：小鲸鱼（来自桌宠）\n\n"
                 + persona[:1800]
                 + f"\n{SYNC_END}\n")
        try:
            old = self.role_path.read_text(encoding="utf-8", errors="replace") if self.role_path.exists() else ""
            if SYNC_START in old and SYNC_END in old:
                current = re.search(re.escape(SYNC_START) + r"(.*?)" + re.escape(SYNC_END), old, flags=re.S)
                if current and current.group(1).strip() == block.strip().split("-->", 1)[-1].strip():
                    return (True, "人设已经是最新的，不用重复同步")
        except Exception:
            pass
        try:
            self.role_path.parent.mkdir(parents=True, exist_ok=True)
            old = self.role_path.read_text(encoding="utf-8", errors="replace") if self.role_path.exists() else ""
            if self.role_path.exists():
                try:
                    self.role_path.with_suffix(".md.bak").write_text(old, encoding="utf-8")
                except Exception:
                    pass
            if SYNC_START in old and SYNC_END in old:
                old = re.sub(re.escape(SYNC_START) + r".*?" + re.escape(SYNC_END),
                             block.strip(), old, flags=re.S)
            elif GEN_START in old:
                old = old.replace(GEN_START, block + "\n" + GEN_START, 1)
            else:
                old = old.rstrip() + "\n\n" + block
            self.role_path.write_text(old, encoding="utf-8")
            return (True, f"已同步人设到抖音助手（{self.role_path}）")
        except Exception as exc:
            return (False, f"写 role-card 失败：{exc}")
