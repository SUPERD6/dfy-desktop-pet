# -*- coding: utf-8 -*-
"""用真实 API Key 做一次在线验证：连接测试 + 两轮对话 + 记忆 + 主动搭话。

Key 从 pet/.secret_api_key 读取，脚本只打印脱敏后的结果，不打印 Key 本身。
用法：python tools/test_live_llm.py
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PET = BASE / "pet"
sys.path.insert(0, str(PET))

from brain import Brain  # noqa: E402

KEY_FILE = PET / ".secret_api_key"


def main() -> int:
    if not KEY_FILE.exists():
        print("FAIL: 没有 pet/.secret_api_key")
        return 1
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    print("key file: 已找到（长度 %d，前 3 位 %s***）" % (len(key), key[:3]))

    config = json.loads((PET / "config.json").read_text(encoding="utf-8"))
    logs: list[str] = []
    brain = Brain(PET, config, log=lambda message: logs.append(str(message)))
    print("status:", brain.status_text())

    ok, detail = brain.test_connection()
    print("test_connection:", "OK" if ok else "FAIL", "|", detail)

    questions = [
        "你好呀，今天我是来验收桌宠的。",
        "记住：我现在在做桌面 AI 伴侣项目。",
        "我叫小林，今天有点累。",
    ]
    replies = []
    for question in questions:
        reply, source = brain.reply(question)
        replies.append((question, source, reply))
        print(f"Q: {question}")
        print(f"   [{source}] {reply}")

    print("memory:", brain.memory_summary())
    proactive = brain.proactive_line({"time_of_day": "afternoon", "idle_minutes": 40,
                                      "dsh_state": "idle"})
    print("proactive_line:", proactive)
    print("dsh success line:", brain.line_for_dsh_state("success"))

    joined = "\n".join(logs) + "\n" + (detail or "") + "\n" + "\n".join(r[2] for r in replies)
    leaked = key in joined or key[:12] in joined
    print("key leaked into logs/detail/replies:", leaked)
    llm_ok = any(source == "llm" for _q, source, _r in replies)
    print("RESULT:", "PASS" if (ok and llm_ok and not leaked) else "FAIL")
    return 0 if (ok and llm_ok and not leaked) else 1


if __name__ == "__main__":
    raise SystemExit(main())
