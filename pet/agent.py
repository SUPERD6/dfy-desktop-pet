# -*- coding: utf-8 -*-
"""大肥鱼的手脚：DeepSeek function calling + 本机安全工具。

这一层让桌宠「不只是聊天，还能帮忙做事」：
- 用 DeepSeek / OpenAI 兼容的 `/chat/completions` 接口，带 `tools` 定义与 `tool_calls` 循环；
- 只读工具：工作总览、个人库检索、DSH 状态、列目录、读文件、搜文件；
- 需要主人点头的工具：执行 PowerShell、写文件（由 `confirm` 回调负责弹窗，本模块只调用）；
- 所有工具都有白名单/敏感文件/大小/二进制等边界，异常一律转成 `{ok: false}` 交给模型，
  绝不让异常冒出 `run()`；任何 API 失败返回 `source="error"` 让人话回退普通聊天；
- Key 只用于 HTTP 头，绝不写进 steps / reply / error / 日志（错误与工具结果都会脱敏）。

对外接口：
    AgentRunner(base_dir, config, personal=None, overview=None,
                status_path=None, log=print, confirm=None)
    .run(user_text, on_step=None) -> dict
        返回 {"reply": str, "steps": [{"tool","ok","detail"}],
              "source": "agent"|"error", "error": str|None}
    .execute_tool(name, args) -> dict       # 单独执行一个工具（调试/测试用），永不抛异常
    .system_prompt(user_text="") -> str     # 供测试/设置界面查看

线程约定：`run()` 可能在 worker 线程里跑；`confirm` 回调也在调用线程里同步执行，
是否需要切回 Tk 主线程由回调方负责（app 层用 root.after 调停）。
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

# 默认白名单：只有本项目根目录（config["agent"]["allowed_roots"] 可整体覆盖，比如再加上个人库）
DEFAULT_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
DEFAULT_SHARED_ROOT = ""  # 个人库默认不开放；需要时写进 allowed_roots
DEFAULT_STATUS_PATH = "~/.dsh/whale-bridge/status.json"

# 步数上限：任务硬要求最多 6 步（配置只能往小改，不能放大）
MAX_STEPS = 6
MAX_API_ROUNDS = 12            # 兜底：模型一直给空 tool_calls 时也一定会停
MAX_TOOL_RESULT_CHARS = 8000   # 单条工具结果进模型上下文的最大字符数
MAX_READ_BYTES_HARD = 5 * 1024 * 1024   # 超过 5MB 的文本文件不直接读
MAX_WRITE_BYTES = 200 * 1024             # write_file 内容上限 200KB
MAX_SHELL_COMMAND_CHARS = 4000
MAX_SHELL_OUTPUT_CHARS = 6000
MAX_LIST_ENTRIES = 200
MAX_SEARCH_FILES = 500
MAX_SEARCH_FILE_BYTES = 200 * 1024
SEARCH_DEADLINE_S = 6.0
MAX_PERSONAL_BLOCK_CHARS = 1200
MAX_OVERVIEW_CHARS = 2000
CONFIRM_PREVIEW_CHARS = 400
DEFAULT_SHELL_TIMEOUT_S = 60
STATUS_STALE_AFTER_S = 15.0

DEFAULT_PERSONA = (
    "你是「大肥鱼」，一条住在用户 Windows 桌面里的鲸鱼娘。\n"
    "贪吃（最爱白米饭）、爱偷懒、有点傲娇但心软；说话用中文口语，短句，一次 20～60 字。\n"
    "不写列表，不解释自己是 AI 模型；不知道的事就说不知道。"
)

# 允许写文件的文本扩展名（write_file 白名单）
TEXT_EXTENSIONS = {
    ".md", ".txt", ".json", ".py", ".html", ".css", ".js", ".yaml", ".yml", ".log",
}

# 敏感文件名 / 目录：读取、写入、搜索、命令里提到都拒绝
SENSITIVE_NAMES = {
    ".secret_api_key", "secret_api_key", "credentials", "credentials.json",
    ".credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".pgpass",
}
SENSITIVE_PREFIXES = (".env",)          # .env / .env.local / .env.production
SENSITIVE_SUFFIXES = (".pem", ".key", ".ppk", ".p12", ".pfx", ".jks")
SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", "credentials", "secrets"}

# 不读/不搜的二进制扩展名（宁可少读，也不把乱码塞给模型）
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff",
    ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".mp4", ".webm", ".mov", ".avi",
    ".zip", ".7z", ".rar", ".gz", ".tar", ".xz", ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".pyd", ".class", ".jar", ".node", ".asar", ".db", ".sqlite", ".sqlite3",
    ".pdf", ".ttf", ".otf", ".woff", ".woff2", ".bin", ".dat", ".iso", ".img",
}

# 搜索时跳过的目录名（只影响 search_files，list_dir 照常列）
SKIP_SEARCH_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    "runtimes", "pkg", ".harness-cache", ".cache", "cache",
}

# 明显破坏性的 PowerShell 命令：命中就拒绝（连确认弹窗都不弹）
DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)(?<![\w-])format(?![\w-])", "format 格式化磁盘"),
    (r"(?i)\bshutdown\b", "shutdown 关机"),
    (r"(?i)\b(?:stop|restart)-computer\b", "关机/重启电脑"),
    (r"(?i)\b(?:logoff|注销)\b", "注销当前用户"),
    (r"(?i)\brm\s+-(?:rf|fr)\b", "rm -rf"),
    (r"(?i)\bdel\s+/[sfq]\b", "del /s 递归删除"),
    (r"(?i)\b(?:rd|rmdir)\s+/s\b", "rd/rmdir /s 递归删除"),
    (r"(?i)\bremove-item\b[^\n]*-(?:recurse|force)\b", "Remove-Item -Recurse/-Force"),
    (r"(?i)\b(?:ri|rm|del|erase)\b[^\n]*\b-recurse\b", "递归删除（别名 + -Recurse）"),
    (r"(?i)\breg\s+delete\b", "reg delete 删注册表"),
    (r"(?i)\b(?:reg\.exe\s+add|new-itemproperty)\b[^\n]*-name\b", "改注册表项"),
    (r"(?i)\bbcdedit\b", "bcdedit 改启动配置"),
    (r"(?i)\bdiskpart\b", "diskpart 磁盘分区"),
    (r"(?i)\b(?:clear|initialize|format)-disk\b", "清盘/初始化磁盘"),
    (r"(?i)\bremove-partition\b", "删除分区"),
    (r"(?i)\bmkfs(?:\.\w+)?\b", "mkfs 格式化"),
    (r"(?i)\bdd\s+if=", "dd 裸写磁盘"),
    (r"(?i)\bcipher\s+/w\b", "cipher /w 擦除空闲空间"),
    (r"(?i)\b(?:set|new|remove)-executionpolicy\b", "改脚本执行策略"),
    (r'(?i)\b(?:remove-item|del|rd|rmdir)\b[^\n]*-path\s+["]?[a-z]:[\\/]',
     "删除整个盘"),
    (r"(?i)\b(?:takeown|icacls)\b[^\n]*\b(?:/reset|/grant)\b", "改系统权限"),
)

# 命令里提到敏感文件也拒绝（防止把 Key 之类读进 stdout）
# 注意：这里必须用普通字符串（不能用 raw + 双引号），否则 "" 会被 Python 拼成相邻字符串。
SENSITIVE_COMMAND_RE = re.compile(
    "(?i)(\\.secret_api_key|(?:^|[\\\\/\\s\"'=])\\.env(?:\\.|\\b)|credentials"
    "|id_rsa|id_ed25519|\\.pem\\b|\\.key\\b)"
)

# 给模型看的工具说明（system prompt 里也用它）
TOOL_HINTS: tuple[str, ...] = (
    "get_work_overview：看主人最近在做什么、有没有待办。无参数。",
    "read_personal_library：在主人的个人库（背景/偏好/长期记忆）里检索。参数 query（关键词，可空）、limit（默认 5）。",
    "get_dsh_status：看 DSH 助手此刻在干什么。无参数。",
    "list_dir：列目录。参数 path（相对主工作区或绝对路径，默认主工作区）。",
    "read_file：读文本文件（超过 20000 字节会截断）。参数 path、max_bytes。",
    "search_files：按关键词搜文件内容/文件名。参数 query、root（默认主工作区）、max_results（默认 10）。",
    "run_powershell：执行 PowerShell 命令（会先请主人确认！）。参数 command、timeout_s（默认 60）。",
    "write_file：写文本文件（会先请主人确认！）。参数 path、content。",
)

# OpenAI / DeepSeek 兼容的 tools 定义
TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_work_overview",
            "description": "查看主人最近在做什么、最近改了哪些项目、有没有待办事项。无参数。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_personal_library",
            "description": "在主人的个人库（个人背景、偏好、跨会话长期记忆）里检索相关条目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词，可为空"},
                    "limit": {"type": "integer", "description": "最多返回几条，默认 5"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dsh_status",
            "description": "查看 DSH AI 助手当前的实时工作状态（思考/调用工具/完成/出错/等待审批）。无参数。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出允许目录里的内容（只读）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径；相对路径以主工作区为基准，默认为主工作区"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取允许目录里的文本文件（只读，超长会截断）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "max_bytes": {"type": "integer", "description": "最多读多少字节，默认 20000"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "在允许目录里按关键词搜索文件名和文本内容（只读）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要搜的关键词"},
                    "root": {"type": "string", "description": "搜索起点，默认主工作区"},
                    "max_results": {"type": "integer", "description": "最多返回几条，默认 10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": "在本机执行一条 PowerShell 命令（需要主人确认；工作目录固定在项目根；拒绝破坏性命令）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 PowerShell 命令"},
                    "timeout_s": {"type": "integer", "description": "超时秒数，默认 60"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写一个文本文件（需要主人确认；只允许 md/txt/json/py/html/css/js/yaml/yml/log，内容不超过 200KB）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的文本内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
]

# 工具名清单（顺序与 TOOL_SPECS 一致，测试/界面可引用）
TOOL_NAMES: tuple[str, ...] = tuple(spec["function"]["name"] for spec in TOOL_SPECS)


class AgentError(Exception):
    """模型接口失败；detail 是给用户看的人话（绝不含 Key）。"""

    def __init__(self, detail: str, status: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.status = status


# --------------------------------------------------------------------------- 小工具函数
def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_float(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clean_key(raw) -> str:
    """Key 清洗：去空白/BOM/引号/误带的 Bearer 前缀。"""
    key = str(raw or "").strip().lstrip("\ufeff").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1].strip()
    return key


def _normalize_base_url(raw) -> str:
    """补协议、去尾部斜杠；本地地址默认 http，远端默认 https。"""
    base = str(raw or "").strip()
    if not base:
        return DEFAULT_BASE_URL
    if not re.match(r"^https?://", base, re.I):
        host = base.split("/", 1)[0].split(":", 1)[0].lower()
        local = (host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
                 or host.startswith(("127.", "192.168.", "10.")))
        base = ("http://" if local else "https://") + base
    return base.rstrip("/")


def _norm_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _is_within(path: Path, root: Path) -> bool:
    """path 是否在 root 目录内（含 root 本身）；Windows 下不区分大小写。"""
    try:
        Path(_norm_path(path)).relative_to(Path(_norm_path(root)))
        return True
    except (ValueError, OSError):
        return False


def _epoch_ms(value):
    """把 updatedAt 归一成毫秒时间戳；认数字（秒/毫秒）、数字字符串和 ISO 字符串。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return None
        return number * 1000.0 if number < 1e12 else number
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            number = float(text)
        except ValueError:
            number = None
        if number is not None:
            if number <= 0:
                return None
            return number * 1000.0 if number < 1e12 else number
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000.0
        except ValueError:
            return None
    return None


def _decode_text(raw: bytes, allow_replace: bool = False) -> str | None:
    """按 UTF-8（含 BOM）→ GBK 的顺序解码；解不开返回 None 当二进制处理。"""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    if allow_replace:
        return raw.decode("utf-8", errors="replace")
    return None


class AgentRunner:
    """给大肥鱼装上手和脚：工具调用循环 + 本地安全工具。

    同一个实例按「一次 run 一次」串行使用：`run()` 内部会记录当前 Key 用于脱敏，
    因此不要并发跑多个 `run()`（app 层本来也是一个气泡一次对话）。
    """

    MUTATING_TOOLS = ("run_powershell", "write_file")

    def __init__(self, base_dir: Path, config: dict, personal=None, overview=None,
                 status_path=None, log=print, confirm=None):
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.config = config if isinstance(config, dict) else {}
        self.personal = personal
        self.overview = overview
        self.log = log if callable(log) else print
        self.confirm = confirm if callable(confirm) else None
        self.allowed_roots = self._resolve_allowed_roots()
        self.status_path = self._resolve_status_path(status_path)
        self._active_key = ""
        # 记忆来源（app 里接 Brain）：提供 memory_block(text, include_personal=False)，拼在 system prompt 末尾
        self.memory = None

    # ------------------------------------------------------------------ 对外：主循环
    def run(self, user_text: str, on_step=None) -> dict:
        """跑一轮「用户说一句 → 可能调几次工具 → 她回一句」。永不抛异常。"""
        steps: list[dict] = []
        try:
            text = str(user_text or "").strip()
            if not text:
                return self._error_result("你还没说话呢。", "用户输入为空", steps)

            key, _source = self._resolve_api_key()
            self._active_key = key
            if not key:
                return self._error_result(
                    "我还没拿到 DeepSeek 的 Key，先在设置里填一下吧。",
                    "没有找到 API Key（config、DFY_API_KEY、pet/.secret_api_key 都没有）",
                    steps,
                )

            options = self._llm_options()
            max_steps = options["max_steps"]
            messages: list[dict] = [
                {"role": "system", "content": self.system_prompt(text)},
                {"role": "user", "content": text},
            ]
            api_rounds = 0
            while len(steps) < max_steps:
                api_rounds += 1
                if api_rounds > MAX_API_ROUNDS:  # 正常走不到；防模型一直空转
                    break
                message = self._request_chat(messages, key, options)
                tool_calls = message.get("tool_calls")
                if not tool_calls:
                    reply = self._clean_reply(message.get("content"))
                    if not reply:
                        return self._error_result(
                            "模型这次没说话，我切回普通聊天再试试。",
                            "模型返回了空内容",
                            steps,
                        )
                    return {"reply": self._redact(reply), "steps": steps,
                            "source": "agent", "error": None}

                if not isinstance(tool_calls, list):
                    return self._error_result(
                        "模型给的工具调用格式不对，我先停下。",
                        "模型返回的 tool_calls 不是数组",
                        steps,
                    )

                messages.append(self._assistant_message(message, tool_calls))
                for call in tool_calls:
                    if len(steps) >= max_steps:
                        break
                    name, args, call_id, parse_error = self._parse_tool_call(call)
                    if parse_error:
                        result: dict = {"ok": False, "error": parse_error}
                    else:
                        result = self.execute_tool(name, args)
                    step = self._make_step(name, result)
                    steps.append(step)
                    self._notify_step(on_step, step)
                    if not call_id:
                        call_id = f"call_{len(steps)}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": self._tool_content(result),
                    })

            # 步数用完（或空转兜底）还没有最终回复
            note = f"我调用工具的步数到上限啦（{max_steps} 步），先停下来。"
            return self._error_result(
                note, f"工具调用步数超过上限（{max_steps} 步）", steps
            )
        except AgentError as exc:
            detail = self._redact(exc.detail)
            self.log("Agent 调用失败：" + detail[:200])
            return self._error_result(
                "呜…在线模型这次没连上，我先切回普通聊天陪你。", detail, steps
            )
        except Exception as exc:  # 兜底：再奇怪的错误也不能从 run() 冒出去
            detail = self._redact(f"{type(exc).__name__}：{exc}")[:300]
            self.log("Agent 层异常：" + detail)
            return self._error_result(
                "我这边出了点小问题，先切回普通聊天啦。", detail, steps
            )

    # ------------------------------------------------------------------ 对外：工具
    def execute_tool(self, tool_name: str, args=None) -> dict:
        """执行一个工具；工具的异常一律变成 {ok: false, error: ...}，绝不外抛。"""
        name = str(tool_name or "").strip()
        try:
            tool_args = args if isinstance(args, dict) else {}
            handler = self._tool_handlers().get(name)
            if handler is None:
                return {"ok": False, "error": f"没有这个工具：{name or '(空名字)'}"}
            result = handler(tool_args)
            if not isinstance(result, dict):
                result = {"ok": True, "result": result}
            result.setdefault("ok", True)
            return self._redact_obj(result)
        except Exception as exc:
            detail = self._redact(f"工具执行失败（{type(exc).__name__}）：{exc}")[:300]
            self.log(f"Agent 工具 {name} 异常：" + detail[:200])
            return {"ok": False, "error": detail}

    def tools_spec(self) -> list[dict]:
        """返回 tools 定义的副本（给测试/调试用）。"""
        return copy.deepcopy(TOOL_SPECS)

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(spec["function"]["name"] for spec in TOOL_SPECS)

    def _tool_handlers(self) -> dict:
        return {
            "get_work_overview": self._tool_get_work_overview,
            "read_personal_library": self._tool_read_personal_library,
            "get_dsh_status": self._tool_get_dsh_status,
            "list_dir": self._tool_list_dir,
            "read_file": self._tool_read_file,
            "search_files": self._tool_search_files,
            "run_powershell": self._tool_run_powershell,
            "write_file": self._tool_write_file,
        }

    # ------------------------------------------------------------------ system prompt
    def system_prompt(self, user_text: str = "") -> str:
        options = self._llm_options()
        root = self.allowed_roots[0] if self.allowed_roots else self.base_dir.resolve()
        lines = [self._read_persona(), "", "【你现在能帮主人做事】",
                 f"你可以调用本机的工具来完成任务，一轮最多 {options['max_steps']} 步。工具如下："]
        lines.extend("- " + item for item in TOOL_HINTS)
        lines += [
            "",
            "规则：",
            "- 闲聊或不需要动本机东西的问题，直接回答，不要为了用工具而用工具。",
            "- 不要做未确认的危险操作；工具失败要如实说明。",
            "- 只能调用上面列出的工具；工具失败就如实告诉主人，绝不编造工具结果。",
            "- 不要读取或复述 .secret_api_key、.env、credentials 这类敏感文件，也不要执行破坏性命令。",
            "- run_powershell 和 write_file 会先弹窗让主人确认；主人没同意就停下来说明。",
            "- 回复用中文口语，20～100 字，最多三小句；不写 Markdown 列表。",
            f"主工作区（相对路径以它为基准）：{root}",
            "今天日期：" + datetime.now().strftime("%Y-%m-%d"),
            "用户称呼：" + str(self.config.get("user_name") or "主人"),
        ]
        personal_block = self._personal_block(user_text)
        if personal_block:
            lines += ["", "【主人的个人库摘要（只用来理解背景，不要逐字复述，不确定就不要编造）】",
                      personal_block]
        block_fn = getattr(self.memory, "memory_block", None)
        if callable(block_fn):  # 记忆放最后：前面不变的部分能吃到前缀缓存
            try:
                block = str(block_fn(user_text, include_personal=False) or "").strip()
            except Exception as exc:
                self.log("记忆读取失败：" + self._redact(str(exc))[:160])
                block = ""
            if block:
                lines += ["", block]
        return "\n".join(lines)

    def _read_persona(self) -> str:
        try:
            text = (self.base_dir / "persona.md").read_text(encoding="utf-8").strip()
            return text or DEFAULT_PERSONA
        except Exception:
            return DEFAULT_PERSONA

    def _personal_block(self, user_text: str) -> str:
        """动态调用 personal.prompt_block；模块没接上/报错都安静地返回空。"""
        if self.personal is None:
            return ""
        fn = getattr(self.personal, "prompt_block", None)
        if not callable(fn):
            return ""
        try:
            try:
                block = fn(user_text)
            except TypeError:  # 兼容 prompt_block() 无参实现
                block = fn()
        except Exception as exc:
            self.log("个人库摘要读取失败：" + self._redact(str(exc))[:160])
            return ""
        return str(block or "").strip()[:MAX_PERSONAL_BLOCK_CHARS]

    # ------------------------------------------------------------------ 工具：只读
    def _tool_get_work_overview(self, _args: dict) -> dict:
        fn = getattr(self.overview, "text_summary", None) if self.overview is not None else None
        if not callable(fn):
            return {"ok": False, "error": "工作总览暂不可用（overview 模块还没接上）"}
        try:
            try:
                text = fn()
            except TypeError:  # 兼容 text_summary(max_chars)
                text = fn(MAX_OVERVIEW_CHARS)
        except Exception as exc:
            self.log("工作总览读取失败：" + self._redact(str(exc))[:160])
            return {"ok": False, "error": f"工作总览读取失败（{type(exc).__name__}）"}
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "工作总览是空的（可能还没生成）"}
        return {"ok": True, "summary": text[:MAX_OVERVIEW_CHARS],
                "truncated": len(text) > MAX_OVERVIEW_CHARS}

    def _tool_read_personal_library(self, args: dict) -> dict:
        query = str(args.get("query") or "").strip()[:120]
        limit = _clamp_int(args.get("limit"), 5, 1, 20)
        fn = getattr(self.personal, "find", None) if self.personal is not None else None
        if not callable(fn):
            return {"ok": False, "error": "个人库暂不可用（personal 模块还没接上）"}
        try:
            try:
                found = fn(query, limit)
            except TypeError:  # 兼容 find(query)
                found = fn(query)
        except Exception as exc:
            self.log("个人库检索失败：" + self._redact(str(exc))[:160])
            return {"ok": False, "error": f"个人库检索失败（{type(exc).__name__}）"}
        if isinstance(found, str):
            found = [found]
        items: list[str] = []
        for item in list(found or [])[:limit]:
            text = self._normalize_personal_item(item)
            if text:
                items.append(text)
        result: dict = {"ok": True, "query": query, "count": len(items), "items": items}
        if not items:
            result["note"] = "没有找到相关条目"
        return result

    @staticmethod
    def _normalize_personal_item(item) -> str:
        if isinstance(item, str):
            return item.strip()[:600]
        if isinstance(item, dict):
            for key in ("text", "snippet", "line", "summary", "content", "value", "title"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:600]
            try:
                return json.dumps(item, ensure_ascii=False, default=str)[:600]
            except Exception:
                return ""
        return str(item)[:600]

    def _tool_get_dsh_status(self, _args: dict) -> dict:
        path = self.status_path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"ok": False, "path": str(path),
                    "error": "还没找到 DSH 状态文件（插件未安装或 DSH 没启动）"}
        except OSError as exc:
            return {"ok": False, "path": str(path),
                    "error": f"读 DSH 状态文件失败：{exc.strerror or exc}"}
        if len(raw) > 200 * 1024:
            return {"ok": False, "path": str(path), "error": "DSH 状态文件异常地大，先不读"}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {"ok": False, "path": str(path), "error": "DSH 状态文件内容坏了（不是合法 JSON）"}
        if not isinstance(data, dict):
            return {"ok": False, "path": str(path), "error": "DSH 状态文件格式不对（不是对象）"}
        state = str(data.get("state") or "").strip().lower() or "unknown"
        last_tool = data.get("lastTool")
        updated = data.get("updatedAt")
        result: dict = {
            "ok": True,
            "path": str(path),
            "state": state,
            "lastTool": last_tool if isinstance(last_tool, str) else None,
            "doneCount": _clamp_int(data.get("doneCount"), 0, 0, 10 ** 9),
            "errorCount": _clamp_int(data.get("errorCount"), 0, 0, 10 ** 9),
            "approvalOpen": bool(data.get("approvalOpen")),
            "updatedAt": updated if isinstance(updated, (int, float, str)) else None,
            "connected": True,
        }
        updated_ms = _epoch_ms(updated)
        if updated_ms is not None:
            age_s = max(0.0, time.time() - updated_ms / 1000.0)
            result["age_s"] = round(age_s, 1)
            if age_s > STATUS_STALE_AFTER_S:
                result["connected"] = False
                result["note"] = f"心跳 {int(age_s)} 秒没更新，DSH 可能已经断开"
        return result

    def _tool_list_dir(self, args: dict) -> dict:
        path, error = self._resolve_tool_path(args.get("path"), default=".")
        if error:
            return {"ok": False, "error": error}
        if not path.exists():
            return {"ok": False, "error": f"目录不存在：{self._display_path(path)}"}
        if not path.is_dir():
            return {"ok": False, "error": f"不是目录：{self._display_path(path)}"}
        try:
            children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            return {"ok": False, "error": f"列目录失败：{exc.strerror or exc}"}
        truncated = len(children) > MAX_LIST_ENTRIES
        entries: list[dict] = []
        for child in children[:MAX_LIST_ENTRIES]:
            if self._sensitive_reason(child):
                continue  # 敏感文件连名字都不列给模型
            try:
                is_dir = child.is_dir()
            except OSError:
                continue
            item: dict = {"name": child.name, "type": "dir" if is_dir else "file"}
            if not is_dir:
                try:
                    item["size"] = child.stat().st_size
                except OSError:
                    item["size"] = 0
            entries.append(item)
        return {"ok": True, "path": self._display_path(path), "count": len(entries),
                "entries": entries, "truncated": truncated}

    def _tool_read_file(self, args: dict) -> dict:
        path, error = self._resolve_tool_path(args.get("path"))
        if error:
            return {"ok": False, "error": error}
        if not path.exists():
            return {"ok": False, "error": f"文件不存在：{self._display_path(path)}"}
        if path.is_dir():
            return {"ok": False, "error": f"这是目录不是文件：{self._display_path(path)}"}
        suffix = path.suffix.lower()
        if suffix in BINARY_EXTENSIONS:
            return {"ok": False, "error": f"不读二进制文件（{suffix}）"}
        try:
            size = path.stat().st_size
        except OSError as exc:
            return {"ok": False, "error": f"读文件信息失败：{exc.strerror or exc}"}
        if size > MAX_READ_BYTES_HARD:
            return {"ok": False,
                    "error": f"文件太大（{size} 字节，超过 {MAX_READ_BYTES_HARD} 上限），不直接读"}
        max_bytes = _clamp_int(args.get("max_bytes"), 20000, 1, MAX_READ_BYTES_HARD)
        max_bytes = min(max_bytes, MAX_READ_BYTES_HARD)
        try:
            with path.open("rb") as fh:
                head = fh.read(min(size, max_bytes + 1))
        except OSError as exc:
            return {"ok": False, "error": f"读文件失败：{exc.strerror or exc}"}
        if b"\x00" in head[:8192]:
            return {"ok": False, "error": "这个文件里有二进制内容（NUL 字节），不读"}
        raw = head[:max_bytes]
        truncated = size > len(raw)
        text = _decode_text(raw, allow_replace=truncated)
        if text is None:
            return {"ok": False, "error": "这个文件不是 UTF-8/GBK 文本，按二进制处理不读"}
        if truncated:
            text += (f"\n\n[已截断：原文件 {size} 字节，这里只读了前 {len(raw)} 字节]")
        return {"ok": True, "path": self._display_path(path), "size": size,
                "bytes_read": len(raw), "truncated": truncated, "content": text}

    def _tool_search_files(self, args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "搜索关键词不能为空"}
        if len(query) > 100:
            return {"ok": False, "error": "搜索关键词太长（最多 100 字）"}
        root, error = self._resolve_tool_path(args.get("root"), default=".")
        if error:
            return {"ok": False, "error": error}
        if not root.exists():
            return {"ok": False, "error": f"搜索目录不存在：{self._display_path(root)}"}
        if not root.is_dir():
            return {"ok": False, "error": f"搜索起点不是目录：{self._display_path(root)}"}
        max_results = _clamp_int(args.get("max_results"), 10, 1, 50)
        needle = query.casefold()
        matches: list[dict] = []
        scanned = 0
        truncated = False
        deadline = time.time() + SEARCH_DEADLINE_S

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                name for name in dirnames
                if name.lower() not in SKIP_SEARCH_DIRS
                and not self._sensitive_reason(Path(dirpath) / name)
            )
            for filename in sorted(filenames):
                if len(matches) >= max_results:
                    truncated = True
                    break
                if time.time() > deadline:
                    truncated = True
                    break
                file_path = Path(dirpath) / filename
                if self._sensitive_reason(file_path):
                    continue
                suffix = file_path.suffix.lower()
                if suffix in BINARY_EXTENSIONS:
                    continue
                if needle in filename.casefold():
                    matches.append({
                        "path": self._display_path(file_path),
                        "line": 0,
                        "text": f"文件名匹配：{filename}",
                    })
                    continue
                scanned += 1
                if scanned > MAX_SEARCH_FILES:
                    truncated = True
                    break
                try:
                    if file_path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                        continue
                    raw = file_path.read_bytes()
                except OSError:
                    continue
                if b"\x00" in raw[:4096]:
                    continue
                text = _decode_text(raw)
                if text is None:
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if needle in line.casefold():
                        matches.append({
                            "path": self._display_path(file_path),
                            "line": lineno,
                            "text": line.strip()[:200],
                        })
                        break  # 一个文件先给一条，结果更有代表性
                if len(matches) >= max_results:
                    truncated = True
                    break
            if truncated:
                break
        return {"ok": True, "query": query, "root": self._display_path(root),
                "count": len(matches), "matches": matches, "truncated": truncated}

    # ------------------------------------------------------------------ 工具：需确认
    def _tool_write_file(self, args: dict) -> dict:
        path, error = self._resolve_tool_path(args.get("path"))
        if error:
            return {"ok": False, "error": error}
        content = args.get("content")
        if not isinstance(content, str):
            return {"ok": False, "error": "content 必须是字符串"}
        suffix = path.suffix.lower()
        if suffix not in TEXT_EXTENSIONS:
            allowed = "/".join(sorted(TEXT_EXTENSIONS))
            return {"ok": False,
                    "error": f"只允许写文本文件（{allowed}），不写 {suffix or '无扩展名'} 文件"}
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            return {"ok": False,
                    "error": f"内容太大（{len(data)} 字节，上限 {MAX_WRITE_BYTES} 字节）"}
        if path.exists() and path.is_dir():
            return {"ok": False, "error": f"目标是目录不是文件：{self._display_path(path)}"}
        preview = ("准备写文件\n路径：{}\n大小：{} 字节\n内容预览：\n{}".format(
            self._display_path(path), len(data), content[:CONFIRM_PREVIEW_CHARS])
            + ("\n…" if len(content) > CONFIRM_PREVIEW_CHARS else ""))
        allowed, reason = self._ask_confirm("write_file", args, preview)
        if not allowed:
            return {"ok": False, "rejected": True, "error": reason}
        created = not path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".dfy-write-", suffix=".tmp",
                                            dir=str(path.parent))
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            return {"ok": False, "error": f"写文件失败：{exc.strerror or exc}"}
        return {"ok": True, "path": self._display_path(path), "bytes": len(data),
                "created": created}

    def _tool_run_powershell(self, args: dict) -> dict:
        command = str(args.get("command") or "").strip()
        timeout_s = _clamp_int(args.get("timeout_s"), DEFAULT_SHELL_TIMEOUT_S, 1, 300)
        if not command:
            return {"ok": False, "error": "命令不能为空"}
        if len(command) > MAX_SHELL_COMMAND_CHARS:
            return {"ok": False, "error": f"命令太长（超过 {MAX_SHELL_COMMAND_CHARS} 字符）"}
        danger = self._dangerous_reason(command)
        if danger:
            return {"ok": False, "error": f"命令看起来有破坏性（{danger}），已拒绝执行"}
        sensitive = self._sensitive_command_reason(command)
        if sensitive:
            return {"ok": False, "error": f"命令里提到敏感文件（{sensitive}），已拒绝执行"}
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            return {"ok": False, "error": "这台机器上没找到 PowerShell"}
        cwd = self._shell_cwd()
        preview = ("准备执行 PowerShell（工作目录：{}）\n命令：\n{}".format(
            self._display_path(cwd), command[:CONFIRM_PREVIEW_CHARS]))
        allowed, reason = self._ask_confirm("run_powershell", args, preview)
        if not allowed:
            return {"ok": False, "rejected": True, "error": reason}
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                [exe, "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                creationflags=creationflags,
            )
        except OSError as exc:
            return {"ok": False, "error": f"启动 PowerShell 失败：{exc.strerror or exc}"}
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return {"ok": False, "timeout": True, "command": command[:500],
                    "error": f"命令超过 {timeout_s} 秒还没结束，已经被我掐掉了"}
        stdout = self._decode_output(stdout_bytes)
        stderr = self._decode_output(stderr_bytes)
        truncated = len(stdout) > MAX_SHELL_OUTPUT_CHARS or len(stderr) > MAX_SHELL_OUTPUT_CHARS
        result: dict = {
            "ok": proc.returncode == 0,
            "command": command[:500],
            "cwd": self._display_path(cwd),
            "exit_code": proc.returncode,
            "stdout": stdout[:MAX_SHELL_OUTPUT_CHARS],
            "stderr": stderr[:MAX_SHELL_OUTPUT_CHARS],
            "truncated": truncated,
        }
        if proc.returncode != 0:
            result["error"] = f"命令退出码 {proc.returncode}"
        return result

    @staticmethod
    def _decode_output(raw: bytes | None) -> str:
        if not raw:
            return ""
        text = _decode_text(raw, allow_replace=True)
        return (text or "").replace("\r\n", "\n").strip()

    def _shell_cwd(self) -> Path:
        """PowerShell 工作目录：项目根（base_dir 是 pet/ 时用它的上一级），且必须在白名单里。"""
        candidate = self.base_dir.resolve()
        if self.base_dir.name.lower() == "pet":
            parent = candidate.parent
            if any(_is_within(parent, root) for root in self.allowed_roots):
                candidate = parent
        if not candidate.is_dir():
            candidate = self.allowed_roots[0] if self.allowed_roots else Path.cwd()
        return candidate

    def _dangerous_reason(self, command: str) -> str:
        for pattern, label in DANGEROUS_PATTERNS:
            try:
                if re.search(pattern, command):
                    return label
            except re.error:
                continue
        return ""

    @staticmethod
    def _sensitive_command_reason(command: str) -> str:
        match = SENSITIVE_COMMAND_RE.search(command)
        return match.group(1) if match else ""

    def _ask_confirm(self, tool_name: str, args: dict, preview: str):
        """调用 confirm(tool_name, args, preview)；没有回调、回调报错都按「拒绝」处理。"""
        if not callable(self.confirm):
            return False, "没有可用的确认弹窗，这个操作需要主人点头，已拒绝"
        try:
            answer = self.confirm(tool_name, dict(args), preview)
        except Exception as exc:
            self.log("确认回调出错：" + self._redact(str(exc))[:160])
            return False, "确认弹窗出错了，已拒绝这个操作"
        if answer:
            return True, ""
        return False, "主人没有确认，操作已取消"

    # ------------------------------------------------------------------ 路径安全
    def _resolve_allowed_roots(self) -> list[Path]:
        agent_cfg = _as_dict(self.config.get("agent"))
        raw = agent_cfg.get("allowed_roots")
        roots: list[Path] = []
        if isinstance(raw, (list, tuple)) and raw:
            for item in raw:
                text = str(item or "").strip()
                if text:
                    roots.append(Path(os.path.expanduser(os.path.expandvars(text))).resolve())
        if not roots:
            roots = [Path(item) for item in (DEFAULT_PROJECT_ROOT, DEFAULT_SHARED_ROOT) if item]
            roots.append(self.base_dir.resolve())
            if self.base_dir.name.lower() == "pet":
                roots.append(self.base_dir.resolve().parent)
        unique: list[Path] = []
        for root in roots:
            if root not in unique:
                unique.append(root)
        return unique

    def _resolve_status_path(self, status_path) -> Path:
        if status_path is not None:
            raw = str(status_path)
        else:
            cfg = _as_dict(self.config.get("dsh_status"))
            raw = str(cfg.get("path") or DEFAULT_STATUS_PATH)
        return Path(os.path.expanduser(os.path.expandvars(raw)))

    def _resolve_tool_path(self, raw, default: str | None = None):
        """把工具参数里的路径解析成绝对路径并做白名单/敏感文件检查。

        返回 (Path|None, error|None)；相对路径以 allowed_roots[0]（主工作区）为基准。
        """
        text = str(raw if raw is not None else (default if default is not None else "")).strip()
        if not text:
            return None, "没有给路径"
        if "\x00" in text:
            return None, "路径里有非法字符"
        if any(part == ".." for part in re.split(r"[\\/]+", text)):
            return None, "路径里不允许出现 ..（防止跳出白名单目录）"
        expanded = os.path.expandvars(os.path.expanduser(text))
        candidate_path = Path(expanded)
        if not candidate_path.is_absolute():
            base = self.allowed_roots[0] if self.allowed_roots else self.base_dir.resolve()
            candidate_path = base / candidate_path
        try:
            candidate = candidate_path.resolve()
        except (OSError, RuntimeError):
            return None, "路径无法解析"
        sensitive = self._sensitive_reason(candidate)
        if sensitive:
            return None, f"这是敏感文件（{sensitive}），不能动"
        if not any(_is_within(candidate, root) for root in self.allowed_roots):
            roots = "、".join(str(root) for root in self.allowed_roots[:3])
            return None, f"路径不在允许的目录里（只能动：{roots}）"
        return candidate, None

    @staticmethod
    def _sensitive_reason(path: Path) -> str:
        """命中敏感文件/目录时返回人话原因，否则返回空字符串。"""
        name = path.name.lower()
        if name in SENSITIVE_NAMES or name.startswith(SENSITIVE_PREFIXES):
            return path.name
        for suffix in SENSITIVE_SUFFIXES:
            if name.endswith(suffix):
                return path.name
        for part in path.parts[:-1]:
            if part.lower() in SENSITIVE_DIRS:
                return part
        return ""

    def _display_path(self, path: Path) -> str:
        """给模型/界面看的短路径：在主工作区里就显示相对路径，否则显示绝对路径。"""
        try:
            if self.allowed_roots:
                rel = path.resolve().relative_to(self.allowed_roots[0])
                text = str(rel)
                return text if text not in ("", ".") else str(path.resolve())
        except (ValueError, OSError):
            pass
        return str(path)

    # ------------------------------------------------------------------ 模型接口
    def _llm_options(self) -> dict:
        llm = _as_dict(self.config.get("llm"))
        agent_cfg = _as_dict(self.config.get("agent"))
        return {
            "base_url": _normalize_base_url(llm.get("base_url")),
            "model": str(llm.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            "timeout_s": _clamp_float(llm.get("timeout_s"), 30.0, 0.1, 300.0),
            "max_tokens": _clamp_int(agent_cfg.get("max_tokens"), 800, 64, 4096),
            "temperature": _clamp_float(agent_cfg.get("temperature"), 0.3, 0.0, 2.0),
            "max_steps": _clamp_int(agent_cfg.get("max_steps"), MAX_STEPS, 1, MAX_STEPS),
        }

    def _resolve_api_key(self) -> tuple[str, str]:
        """Key 优先级与 brain 一致：config → 环境变量 DFY_API_KEY → pet/.secret_api_key。"""
        llm = _as_dict(self.config.get("llm"))
        key = _clean_key(llm.get("api_key"))
        if key:
            return key, "配置文件"
        env_key = _clean_key(os.environ.get("DFY_API_KEY"))
        if env_key:
            return env_key, "环境变量 DFY_API_KEY"
        try:
            file_key = _clean_key((self.base_dir / ".secret_api_key").read_text(encoding="utf-8"))
        except Exception:
            file_key = ""
        if file_key:
            return file_key, "密钥文件 .secret_api_key"
        return "", ""

    @staticmethod
    def _chat_url(base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.lower().endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _request_chat(self, messages: list[dict], key: str, options: dict) -> dict:
        """发一次 /chat/completions，返回 assistant message；失败翻译成 AgentError。"""
        url = self._chat_url(options["base_url"])
        payload = {
            "model": options["model"],
            "messages": messages,
            "temperature": options["temperature"],
            "max_tokens": options["max_tokens"],
            "stream": False,
            "tools": self.tools_spec(),
            "tool_choice": "auto",
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer " + key,
                "User-Agent": "DaFeiYu-Agent/1.0",
            },
            method="POST",
        )
        timeout = float(options["timeout_s"])
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            code = exc.code
            try:
                exc.close()
            except Exception:
                pass
            raise AgentError(self._http_error_detail(code), status=code) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise AgentError(self._network_error_detail(exc, timeout)) from None
        except Exception as exc:
            raise AgentError("请求模型时出错：" + type(exc).__name__) from None
        if len(raw) > 2 * 1024 * 1024:
            raise AgentError("模型返回内容太大")
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise AgentError("模型接口返回了无法解析的内容") from None
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise AgentError("模型接口返回格式不正确（没有 choices）")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message")
        if not isinstance(message, dict):
            raise AgentError("模型接口返回格式不正确（没有 message）")
        return message

    @staticmethod
    def _http_error_detail(code: int) -> str:
        if code in (401, 403):
            return f"API Key 无效或没有权限（HTTP {code}），检查 Key 是否填对、是否过期。"
        if code == 429:
            return "请求太频繁了（HTTP 429），先缓一缓再试。"
        if code == 404:
            return "接口地址不对（HTTP 404），检查 base_url 是不是少了 /v1。"
        if code == 400:
            return "接口拒绝了这次请求（HTTP 400），检查模型名是否可用。"
        if 500 <= code < 600:
            return f"模型服务暂时不可用（HTTP {code}），等会儿再试。"
        return f"接口返回异常（HTTP {code}）。"

    def _network_error_detail(self, exc: Exception, timeout: float) -> str:
        reason = getattr(exc, "reason", exc)
        text = self._redact(str(reason) or type(exc).__name__)
        low = text.lower()
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in low or "timeout" in low:
            return f"请求超时（{timeout:g} 秒内模型没回应），检查网络后重试。"
        if any(word in low for word in ("getaddrinfo", "name or service not known",
                                        "nodename nor servname", "name resolution")):
            return "域名解析失败，检查网络或接口地址。"
        if "connection refused" in low or "积极拒绝" in text:
            return "连接被拒绝，检查接口地址或本地模型服务是否启动。"
        if "certificate" in low or "ssl" in low:
            return "HTTPS 证书校验失败，检查系统时间或网络代理。"
        return f"网络错误：{text[:120]}"

    # ------------------------------------------------------------------ 消息/结果整理
    def _assistant_message(self, message: dict, tool_calls: list) -> dict:
        """把模型返回的 assistant 消息（带 tool_calls）整理成可以回传的格式。"""
        calls: list[dict] = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            fn = _as_dict(call.get("function"))
            arguments = fn.get("arguments")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments, ensure_ascii=False)
            elif not isinstance(arguments, str):
                arguments = "{}"
            calls.append({
                "id": str(call.get("id") or ""),
                "type": "function",
                "function": {"name": str(fn.get("name") or ""), "arguments": arguments},
            })
        content = message.get("content")
        return {
            "role": "assistant",
            "content": content if isinstance(content, str) else None,
            "tool_calls": calls,
        }

    @staticmethod
    def _parse_tool_call(call) -> tuple[str, dict, str, str]:
        """解析 tool_call → (name, args, call_id, error)。error 非空表示这次调用不合法。"""
        if not isinstance(call, dict):
            return "", {}, "", "工具调用格式不对"
        fn = call.get("function")
        if not isinstance(fn, dict):
            return "", {}, "", "工具调用缺少 function"
        name = str(fn.get("name") or "").strip()
        call_id = str(call.get("id") or "").strip()
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            return name, raw_args, call_id, ""
        if isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = json.loads(raw_args)
            except (ValueError, TypeError):
                return name, {}, call_id, "工具参数不是合法 JSON"
            if not isinstance(parsed, dict):
                return name, {}, call_id, "工具参数必须是 JSON 对象"
            return name, parsed, call_id, ""
        return name, {}, call_id, ""

    def _tool_content(self, result: dict) -> str:
        payload = result if isinstance(result, dict) else {"ok": True, "result": result}
        try:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            text = json.dumps({"ok": False, "error": "工具结果无法序列化"})
        text = self._redact(text)
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + "…（工具结果太长，已截断）"
        return text

    def _make_step(self, name: str, result: dict) -> dict:
        ok = bool(result.get("ok")) if isinstance(result, dict) else False
        return {"tool": str(name or "未知工具"), "ok": ok,
                "detail": self._redact(self._step_detail(name, result))}

    def _step_detail(self, name: str, result) -> str:
        """给界面显示的一步人话摘要（不含 Key、不含大段内容）。"""
        if not isinstance(result, dict):
            return "返回格式异常"
        if result.get("rejected"):
            return "已拒绝：" + str(result.get("error") or "需要主人确认")[:100]
        if not result.get("ok"):
            return "失败：" + str(result.get("error") or "未知原因")[:120]
        if name == "get_work_overview":
            return "工作总览：" + str(result.get("summary") or "")[:100]
        if name == "read_personal_library":
            return "个人库：检索「{}」得到 {} 条".format(
                result.get("query") or "", result.get("count") or 0)
        if name == "get_dsh_status":
            state = result.get("state") or "unknown"
            extra = "，最近工具 " + str(result["lastTool"]) if result.get("lastTool") else ""
            return f"DSH 状态：{state}{extra}"
        if name == "list_dir":
            return "列目录 {}：{} 项".format(result.get("path"), result.get("count") or 0)
        if name == "read_file":
            note = "（已截断）" if result.get("truncated") else ""
            return "读文件 {}：{} 字节{}".format(
                result.get("path"), result.get("bytes_read") or 0, note)
        if name == "search_files":
            return "搜索「{}」：{} 处匹配".format(
                result.get("query") or "", result.get("count") or 0)
        if name == "run_powershell":
            return "执行命令（退出码 {}）：{}".format(
                result.get("exit_code"), str(result.get("command") or "")[:60])
        if name == "write_file":
            return "写入 {}：{} 字节".format(result.get("path"), result.get("bytes") or 0)
        return "完成"

    def _notify_step(self, on_step, step: dict) -> None:
        if not callable(on_step):
            return
        try:
            on_step(dict(step))
        except Exception as exc:  # UI 回调出错不能影响 agent 主流程
            self.log("on_step 回调出错：" + self._redact(str(exc))[:160])

    @staticmethod
    def _clean_reply(content) -> str:
        text = str(content or "").strip()
        if not text:
            return ""
        text = re.sub(r"^(?:大肥鱼|鲸鱼娘)[：:]\s*", "", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:600]

    def _error_result(self, reply: str, error: str, steps: list[dict]) -> dict:
        return {"reply": self._redact(reply), "steps": steps,
                "source": "error", "error": self._redact(error)}

    # ------------------------------------------------------------------ 脱敏
    def _redact(self, text) -> str:
        """日志/错误/步骤/回复里抹掉 Key（含通用 sk- 形态），绝不外泄。"""
        value = str(text or "")
        key = self._active_key
        if not key:
            try:  # 单独调 execute_tool / 还没跑 run 时的兜底
                key, _source = self._resolve_api_key()
            except Exception:
                key = ""
        if key:
            value = value.replace(key, "***")
        value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", value)
        value = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", value)
        return value

    def _redact_obj(self, obj):
        if isinstance(obj, str):
            return self._redact(obj)
        if isinstance(obj, list):
            return [self._redact_obj(item) for item in obj]
        if isinstance(obj, dict):
            return {str(key): self._redact_obj(value) for key, value in obj.items()}
        return obj
