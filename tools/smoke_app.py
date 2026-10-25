# -*- coding: utf-8 -*-
"""隐藏窗口的冒烟测试：验证 app 能启动、DSH 状态桥能触发、说话动画/气泡不炸。

全程不显示窗口、不发声（hidden + mute + 关掉主动搭话），只跑 Tk 主循环约 11 秒（含投喂、抛出落地、心声气泡）。
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
os.environ["DFY_DATA_DIR"] = str(TMP)  # 饭量账本、生活状态、小窝都写临时目录

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
    # 正在运行的桌宠会写 life.json / memory.json，这两个不在这里比，改为下面「临时目录里有」的正向检查
    real_files = [PET / name for name in ("config.json", "appetite.json")]
    real_before = {f: (f.read_bytes() if f.exists() else None) for f in real_files}
    log_path = PET / "logs" / "app.log"
    log_offset = log_path.stat().st_size if log_path.exists() else 0  # 只看这次运行新写的日志
    application = petapp.PetApp(hidden=True, demo=False)
    errors: list[str] = []

    def guard(label: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # 冒烟测试只关心有没有异常
            errors.append(f"{label}: {exc}")

    # DSH 状态间隔 1 秒：比状态桥轮询慢得多，保证每个状态都能被看到
    schedule = [
        (500, lambda: write_status("running")),
        (1500, lambda: write_status("tool", lastTool="bash")),
        (2500, lambda: write_status("success", doneCount=1, lastTool="bash")),
        (3500, lambda: write_status("failure", doneCount=1, errorCount=1)),
        (4500, lambda: write_status("approval", approvalOpen=True)),
        (5000, lambda: guard("perform(happy)", lambda: application.perform("happy"))),
        (5200, lambda: guard("perform(shy)", lambda: application.perform("shy"))),
        (5400, lambda: guard("perform(surprised)", lambda: application.perform("surprised"))),
        (5600, lambda: guard("tool prop", lambda: application._show_tool_prop("bash"))),
        (5800, lambda: guard("clear prop", lambda: application._clear_tool_prop())),
        (6000, lambda: guard("bubble", lambda: application.bubble.show("冒烟测试：气泡正常", 800))),
        (6200, lambda: guard("menu vars", lambda: (application.proactive_var.get(),
                                                   application.dsh_var.get()))),
        (6300, lambda: guard("feed all", lambda: [application.feed(food) for food in
                                                  ("rice", "fish", "cake", "tangyuan", "diamond")])),
        (6500, lambda: guard("inner voice bubble", lambda: application.bubble.show(
            "冒烟测试：心声", 800, inner_voice="这用户发的啥啊，"))),
        (6550, lambda: guard("reply with action", lambda: application._show_reply("（尾巴一甩）好呀", "llm", None))),
        (6580, lambda: guard("sticker", lambda: application.send_sticker("happy", force=True))),
        (6600, lambda: guard("toss", lambda: application._start_flight(1400, -900))),
        (6700, lambda: guard("memory window", lambda: (application.show_memory(), application.memory_window.destroy()))),
        (9900, lambda: guard("eating finished", lambda: _assert(not application._eating, "吃完没复位"))),
        (10100, lambda: guard("toss landed", lambda: _assert(application._flight is None, "抛出后没落地"))),
        (10300, lambda: (application.voice.shutdown(), application.root.destroy())),
    ]
    for delay, callback in schedule:
        application.root.after(delay, callback)
    application.root.mainloop()

    with log_path.open("rb") as handle:
        handle.seek(log_offset)
        log_text = handle.read().decode("utf-8", errors="replace")
    tail = log_text.splitlines()[-30:]
    print("---- app.log tail ----")
    print("\n".join(tail))
    print("---- errors ----")
    print("\n".join(errors) if errors else "无")
    states_seen = all(f"→ {state}" in log_text for state in ("running", "tool", "success", "failure", "approval"))
    if not states_seen:
        print("DSH 状态没全部看到：", [s for s in ("running", "tool", "success", "failure", "approval")
                                    if f"→ {s}" not in log_text])
    for f, before in real_before.items():
        if (f.read_bytes() if f.exists() else None) != before:
            errors.append(f"冒烟测试改动了真实的 pet/{f.name}（应只写临时目录）")
            print(errors[-1])
    for name in ("appetite.json",):  # life.json 要正常退出才存盘，冒烟是直接销毁窗口
        if not (TMP / name).exists():
            errors.append(f"{name} 没写进临时目录（DFY_DATA_DIR 没生效）")
            print(errors[-1])
    ok = (not errors) and states_seen
    print("SMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
