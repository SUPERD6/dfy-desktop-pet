# -*- coding: utf-8 -*-
"""安全闸：自主进化的写入/命令白名单、密钥遮挡、网络命令识别、看屏幕的敏感窗口。

2026-10-27 评审后补的（见 topics/self-evolution-voice-memory/改进方案.html 第 2 节）：
无人值守时一切按白名单放行，白名单外一律拒绝；不靠提示词约束。
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------- 自主进化白名单
# 自主进化永远不能改的文件：权限/护栏/配置/账本/给 AI 的规则（改了等于自己给自己松绑）
PROTECTED_RELATIVE = (
    "pet/config.json", "pet/config.example.json", "pet/agent.py", "pet/safety.py", "pet/appetite.py",
    "pet/persona.md", "pet/api_server.py", "pet/wechat_official.py",
    # 能联网、管权限或会被测试自动加载的模块（2026-10-28 阶段 0 / agent-7）
    "pet/app.py", "pet/brain.py", "pet/web_tools.py", "pet/self_space.py", "pet/qq_bridge.py",
    "pet/douyin_bridge.py", "pet/conftest.py", "pet/tests/conftest.py", "pet/__init__.py", "pet/tests/__init__.py",
    "tools/autonomy_runner.py", "tools/export_public.py",
    "AGENTS.md", "CLAUDE.md",
)
PROTECTED_DIRS = (".git", ".claude", ".ccx", ".tools")

# 自主进化只许跑这一类命令：项目自己的测试
PYTEST_COMMAND_RE = re.compile(
    r"(?i)python(?:\.exe)?\s+(?:-X\s+utf8\s+)?-m\s+pytest(?:\s+-q)?(?:\s+pet[\\/]tests(?:[\\/][\w.\-]+)*)*\s*")


def _rel(path: Path, root: Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def is_protected(path, project_root) -> bool:
    rel = _rel(Path(path), Path(project_root))
    if rel is None:
        return True  # 项目外一律当受保护
    low = rel.lower()
    if low in (p.lower() for p in PROTECTED_RELATIVE):
        return True
    first = low.split("/", 1)[0]
    return first in PROTECTED_DIRS or Path(low).name.startswith(".secret")


# 自主进化默认能改的模块：不碰权限、网络和界面的「生活/记忆」类（config autonomy_writable_modules 可覆盖）
DEFAULT_WRITABLE_MODULES = ("life.py", "living.py", "memory.py", "reminders.py", "interactions.py",
                            "stickers.py", "sfx.py", "touch_zones.py")


def autonomy_may_write(path, project_root, writable_modules=None) -> bool:
    """自主进化能写的地方（显式清单）：
    - 清单里的 pet/*.py 模块；
    - 新建或修改 pet/tests/test_auto_*.py（她自己的测试，不能改别人的测试）；
    - pet/home/** 小屋里的 .md/.txt/.json。
    """
    if not path:
        return False
    target = Path(path)
    if not target.is_absolute():
        target = Path(project_root) / target
    if is_protected(target, project_root):
        return False
    rel = _rel(target, project_root)
    if rel is None:
        return False
    parts = rel.split("/")
    if parts[0] != "pet":
        return False
    if len(parts) == 2:
        return parts[1] in tuple(writable_modules or DEFAULT_WRITABLE_MODULES)
    if parts[1] == "tests":
        return len(parts) == 3 and parts[2].startswith("test_auto_") and parts[2].endswith(".py")
    return parts[1] == "home" and rel.endswith((".md", ".txt", ".json"))


# 自主进化写的代码在跑 pytest 时会被执行：新增行里出现这些就不让跑（等于绕过「不上网、不碰系统」）
DIFF_DENY = [
    (re.compile(r"^\s*(import|from)\s+(socket|urllib|http|requests|httpx|aiohttp|subprocess|ctypes|winreg|shutil|webbrowser|smtplib|ftplib)\b"),
     "导入了能联网/执行命令/改系统的模块"),
    (re.compile(r"\b(os\.system|os\.popen|os\.remove|os\.unlink|os\.rmdir|eval|exec|__import__)\s*\("), "执行命令/删除文件/动态执行"),
    (re.compile(r"open\(\s*r?[\"'][A-Za-z]:[\\/]"), "打开绝对路径文件"),
    (re.compile(r"\.secret|api_?key", re.IGNORECASE), "提到密钥"),
]


def scan_diff(diff_text: str) -> list[str]:
    """扫 unified diff 的新增行，返回不通过的原因（空列表 = 通过）。"""
    problems = []
    for line in str(diff_text or "").splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        code = line[1:]
        for pattern, reason in DIFF_DENY:
            if pattern.search(code):
                problems.append(f"{reason}：{code.strip()[:80]}")
                break
    return problems


def working_diff(project_root) -> str:
    """当前工作区相对 HEAD 的改动 + 新增的 test_auto_*.py 全文（当成新增行）。"""
    import subprocess

    root = Path(project_root)
    try:
        diff = subprocess.run(["git", "diff", "HEAD", "--", "pet"], cwd=str(root), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30).stdout
    except Exception:
        diff = ""
    for path in (root / "pet" / "tests").glob("test_auto_*.py"):
        try:
            diff += "\n" + "\n".join("+" + line for line in path.read_text(encoding="utf-8").splitlines())
        except OSError:
            continue
    return diff


def autonomy_confirm(project_root, writable_modules=None):
    """给自主进化用的确认回调：白名单内放行，其余（含所有 force 确认）一律拒绝。"""
    root = Path(project_root)

    def confirm(tool_name, args, preview="", force: bool = False) -> bool:
        if force:
            return False  # 需要真人点头的（网络、读过网页后的写入）无人值守时一律不做
        args = args if isinstance(args, dict) else {}
        if tool_name in ("write_file", "replace_in_file"):
            return autonomy_may_write(args.get("path"), root, writable_modules)
        if tool_name == "run_powershell":
            command = str(args.get("command") or "").strip()
            if ".." in command or not PYTEST_COMMAND_RE.fullmatch(command):
                return False
            problems = scan_diff(working_diff(root))  # 先看她改了什么，再决定让不让测试执行这些代码
            if problems:
                confirm.last_rejection = "；".join(problems[:3])
                return False
            return True
        return False

    confirm.last_rejection = ""

    return confirm


# ---------------------------------------------------------------- 密钥
SECRET_FIELD_RE = re.compile(
    r'("(?:[^"\\]*(?:token|api_?key|apikey|secret|password|passwd|access_key|aes_key)[^"\\]*)"\s*:\s*)"[^"]*"',
    re.IGNORECASE)


def redact_secret_fields(text: str) -> str:
    """JSON 文本里 token/api_key/secret/password 一类字段的值换成 ***。"""
    return SECRET_FIELD_RE.sub(r'\1"***"', str(text or ""))


# 命令里出现这些就拒绝：密钥文件名的各种写法（含通配符 .secret_a*）
SECRET_COMMAND_RE = re.compile(r"(?i)(secret|api[_-]?key)")

# 能把东西发出去/拉进来执行的命令：一律要真人确认（全权限托管也不放行）
NETWORK_COMMAND_RE = re.compile(
    r"(?i)(\b(?:iwr|irm|curl|wget|invoke-webrequest|invoke-restmethod|start-bitstransfer|send-mailmessage"
    r"|iex|invoke-expression|downloadstring|downloadfile|uploadstring|uploadfile|tcpclient|scp|sftp|ssh|ftp"
    r"|bitsadmin|certutil)\b|net\.webclient|system\.net\.|\bgit\s+(?:push|remote|config)\b|\bpip3?\s+install\b"
    r"|\bnpm\s+(?:i|install|publish)\b)")


def network_command_reason(command: str) -> str:
    match = NETWORK_COMMAND_RE.search(str(command or ""))
    return match.group(1) if match else ""


# ---------------------------------------------------------------- 看屏幕
SENSITIVE_WINDOW_RE = re.compile(
    r"(?i)(密码|password|keepass|1password|bitwarden|lastpass|银行|bank|支付宝|alipay|paypal|微信|wechat|weixin|"
    r"\bqq\b|telegram|whatsapp|\bsignal\b|私信|无痕|inprivate|incognito|隐私浏览|登录|sign in|log in|验证码)")


def screen_has_sensitive(titles, extra: list | tuple | None = None) -> str:
    """看屏幕前查所有可见窗口（不只前台）：返回第一个敏感窗口的标题，没有返回空串。"""
    for title in titles or ():
        if sensitive_window(title, extra):
            return str(title)
    return ""


def sensitive_window(title: str, extra: list | tuple | None = None) -> bool:
    text = str(title or "")
    if SENSITIVE_WINDOW_RE.search(text):
        return True
    return any(str(word).strip() and str(word).strip().lower() in text.lower() for word in (extra or ()))
