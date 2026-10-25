# -*- coding: utf-8 -*-
"""隐藏窗口的冒烟测试：验证 app 能启动、DSH 状态桥能触发、说话动画/气泡不炸。

全程不显示窗口、不发声（hidden + mute + 关掉主动搭话），只跑 Tk 主循环约 9 秒（含投喂、抛出落地、心声气泡）。
用法：python tools/smoke_app.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PET = BASE / "pet"
TMP = Path(tempfile.mkdtemp(prefix="dfy_smoke_"))
STATUS = TMP / "status.json"

CONFIG = {
    "greet_on_start": False,
    "mute": True,
    "always_on_top": False,
    "llm": {"enabled": False, "api_key": ""},
    "proactive": {"enabled": False},
    "dsh_status": {"enabled": True, "path": str(STATUS), "poll_ms": 250},
}
CONFIG_PATH = TMP / "config.json"
CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False), encoding="utf-8")
os.environ["DFY_CONFIG"] = str(CONFIG_PATH)

sys.path.insert(0, str(PET))
import app as petapp  # noqa: E402


def write_status(state: str, **extra) -> None:
    payload = {
        "ok": True,
        "state": state,
        "lastTool": extra.pop("lastTool", None),
        "doneCount": 0,
        "errorCount": 0,
        "approvalOpen": False,
        "updatedAt": int(time.time() * 1000),
        "version": 1,
    }
    payload.update(extra)
    STATUS.write_text(json.dumps(payload), encoding="utf-8")


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def main() -> int:
    real_config = PET / "config.json"
    real_before = real_config.read_bytes() if real_config.exists() else b""
    application = petapp.PetApp(hidden=True, demo=False)
    errors: list[str] = []

    def guard(label: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # 冒烟测试只关心有没有异常
            errors.append(f"{label}: {exc}")

    schedule = [
        (400, lambda: write_status("running")),
        (900, lambda: write_status("tool", lastTool="bash")),
        (1500, lambda: write_status("success", doneCount=1, lastTool="bash")),
        (2200, lambda: write_status("failure", doneCount=1, errorCount=1)),
        (2900, lambda: write_status("approval", approvalOpen=True)),
        (3300, lambda: guard("perform(happy)", lambda: application.perform("happy"))),
        (3500, lambda: guard("perform(shy)", lambda: application.perform("shy"))),
        (3700, lambda: guard("perform(surprised)", lambda: application.perform("surprised"))),
        (3900, lambda: guard("tool prop", lambda: application._show_tool_prop("bash"))),
        (4100, lambda: guard("clear prop", lambda: application._clear_tool_prop())),
        (4300, lambda: guard("bubble", lambda: application.bubble.show("冒烟测试：气泡正常", 800))),
        (4600, lambda: guard("menu vars", lambda: (application.proactive_var.get(),
                                                   application.dsh_var.get()))),
        (4700, lambda: guard("feed all", lambda: [application.feed(food) for food in
                                                  ("rice", "fish", "cake", "tangyuan", "diamond")])),
        (4900, lambda: guard("inner voice bubble", lambda: application.bubble.show(
            "冒烟测试：心声", 800, inner_voice="这用户发的啥啊，"))),
        (5000, lambda: guard("toss", lambda: application._start_flight(1400, -900))),
        (8500, lambda: guard("toss landed", lambda: _assert(application._flight is None, "抛出后没落地"))),
        (8700, lambda: (application.voice.shutdown(), application.root.destroy())),
    ]
    for delay, callback in schedule:
        application.root.after(delay, callback)
    application.root.mainloop()

    log_text = (PET / "logs" / "app.log").read_text(encoding="utf-8", errors="replace")
    tail = log_text.splitlines()[-30:]
    print("---- app.log tail ----")
    print("\n".join(tail))
    print("---- errors ----")
    print("\n".join(errors) if errors else "无")
    states_seen = all(token in log_text for token in ("running", "tool", "success", "failure", "approval"))
    real_after = real_config.read_bytes() if real_config.exists() else b""
    if real_after != real_before:
        errors.append("冒烟测试改动了真正的 pet/config.json（应只写 DFY_CONFIG 临时配置）")
        print(errors[-1])
    ok = (not errors) and states_seen
    print("SMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
