# -*- coding: utf-8 -*-
"""在独立进程里跑一轮自主进化，避免 GUI/主线程被长工具链卡住。

用法：python tools/autonomy_runner.py [topic]
由 pet/app.py 在后台启动；读写项目自己的文件，使用 MAX 模型，默认 12 步。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PET = ROOT / "pet"
sys.path.insert(0, str(PET))

from agent import AgentRunner  # noqa: E402
from appetite import Appetite  # noqa: E402

TOPICS = {
    "Agent 能力/工具/权限": (
        "只做一个很小的能力改进：在 pet/tests/test_appetite.py 里新增一个 unittest，"
        "验证 Appetite.set_allowance(321) 会把 allowance 正好设为 321。"
        "先读这个小文件，再用 replace_in_file 把测试加到文件末尾，最后运行 "
        "python -m pytest -q pet/tests/test_appetite.py。10 步内完成。"),
    "语音模型/声音/听写": (
        "检查 pet/asr.py 的本地 Whisper 路径/失败回退，只修一个真实小问题或补一个测试；"
        "只读相关文件，10 步内完成。"),
    "外形/2D/3D/动作": (
        "只调整一个很小的外形/动作表现（例如某个动画时长或状态映射），跑相关测试；"
        "不要重写素材，10 步内完成。"),
    "大脑/记忆/人格": (
        "只改进一个很小的记忆/说话行为并补一个测试；只读相关文件，10 步内完成。"),
    "启动/性能/桌面体验": (
        "只修一个可复现的小启动/性能/菜单问题并跑测试；只读相关文件，10 步内完成。"),
    "屏幕位置感/活人感": (
        "只改进一处位置感或活人感行为，优先修改 _position_summary/live_context 附近并补测试；"
        "只读相关文件，10 步内完成。"),
}


def main() -> int:
    topic = sys.argv[1] if len(sys.argv) > 1 else "Agent 能力/工具/权限"
    cfg = json.loads((PET / "config.json").read_text(encoding="utf-8"))
    goal = TOPICS.get(topic, TOPICS["Agent 能力/工具/权限"])
    appetite = Appetite(PET / "appetite.json", daily_snack=int(
        ((cfg.get("life") or {}).get("daily_snack_tokens") or 4000)), log=print)
    max_steps = max(1, min(80, int(cfg.get("autonomy_max_steps", 24) or 24)))
    prompt = (
        "你是小鲸鱼，在主人授权下自主优化你自己。你可以上网查资料、读/写本项目代码（包括 app.py 这类大文件）、"
        "2D/3D 素材、语音/听写/模型相关文件，可以跑 PowerShell 备份和测试。\n"
        f"这一轮只做一个具体、能验证的目标：{goal}\n"
        "流程：\n"
        "1) 读 pet/home/SELF_DIRECTION.md 和最多两个目标文件；不要读整个项目。\n"
        "2) 改前把文件备份到 pet/home/backups/<时间戳>/；第 6 步前必须用 replace_in_file 写一次。\n"
        f"3) 第 {max(10, max_steps - 2)} 步前跑相关测试；失败就恢复并如实说明。\n"
        "4) 不读/改 个人配置、密钥、其他个人数据；不外发、不 git push。\n"
        "5) 最后用中文写 5~10 行：改了什么、怎么验证、下一步。\n"
        "大文件只改局部：用 search_files 定位、read_file 读局部、replace_in_file 打补丁；不要整文件重写。"
    )
    runner = AgentRunner(PET, cfg, personal=None, overview=None, log=print,
                         confirm=lambda *args: True)
    runner.appetite = appetite
    result = runner.run(prompt, purpose="self_improve",
                        model=(str(cfg.get("autonomy_model") or "").strip() or None),
                        max_steps=max_steps)
    reply = str((result or {}).get("reply") or "").strip()
    steps = (result or {}).get("steps") or []
    mutated = any(str(s.get("tool") or "") in ("write_file", "replace_in_file") and s.get("ok")
                  for s in steps)
    print("AUTONOMY_REPLY", reply)
    print("AUTONOMY_MUTATED", mutated)
    print("AUTONOMY_STEPS", len(steps))
    for i, step in enumerate(steps, 1):
        print("AUTONOMY_STEP", i, step.get("tool"), step.get("ok"),
              str(step.get("detail") or "")[:300].replace("\n", " "))
    notes = PET / "home" / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M")
    out = notes / f"{stamp}-自主进化-{topic.replace('/', '-')}.md"
    out.write_text(f"# 自主进化：{topic}\n\n{time.strftime('%Y-%m-%d %H:%M')}\n\n"
                   f"写入：{mutated}；步数：{len(steps)}\n\n{reply or '（没有报告）'}\n",
                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
