# -*- coding: utf-8 -*-
"""大肥鱼的大脑。

- 配了 OpenAI 兼容接口（默认 DeepSeek）时，用 LLM + 人设 + 长期记忆来回答；
- 没配、Key 缺失或调用失败时，退回本地「离线台词」规则，保证永远有反应；
- 另提供主动搭话、DSH 工作状态反应、连接测试、记忆摘要等给界面/状态机用的小接口。

对外接口（别的模块/旧代码依赖，别改签名）：
    Brain(base_dir, config, log=print)
    .reply(text) -> (reply_text, source)      source ∈ {"llm", "offline", "offline-error"}
    .status_text() -> str
    .user_name() -> str
    .system_prompt(query="") -> str
    .test_connection() -> (ok, detail)
    .proactive_line(context=None) -> str
    .line_for_dsh_state(state) -> str | None
    .memory_summary() -> str
"""
from __future__ import annotations

import json
import os
import random
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

try:  # 常规用法：桌宠把 pet/ 放进 sys.path 后直接 import brain
    from memory import CONSOLIDATE_BATCH, FACTS_PER_DAY, MemoryStore, format_time, freshness
except ImportError:  # 兜底：把 pet/ 当包导入时
    from .memory import CONSOLIDATE_BATCH, FACTS_PER_DAY, MemoryStore, format_time, freshness

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

DEFAULT_PERSONA = """你是「大肥鱼」，一条住在用户 Windows 桌面里的鲸鱼娘。
贪吃（最爱白米饭）、爱偷懒、有点傲娇但心软；说话用中文口语，短句，一次 20～60 字。
不写列表，不解释自己是 AI 模型；不知道的事就说不知道。"""

# 离线台词：按类别准备，命中关键词就抽一句
RULES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("你好", "在吗", "嗨", "哈喽", "hello", "hi", "早上好", "晚上好"),
     ("在的在的！咕噜…今天想让我陪你做什么呀～",
      "来啦来啦，我刚从白饭里抬起头。",
      "我一直都在呀，只是刚才在偷偷打盹。")),
    (("名字", "叫什么", "是谁", "自我介绍"),
     ("我叫大肥鱼，是住在这台电脑里的一条鲸鱼娘。白天陪你干活，晚上偷懒。",
      "大肥鱼！记住啦，写错的话我会生气的。")),
    (("喜欢你", "爱你", "想你", "想你啦"),
     ("欸…突然说这个，我、我先把脸埋进围裙里。",
      "那…那我也有一点点想你，只有一点点哦。")),
    (("摸", "rua", "揉", "拍拍", "蹭"),
     ("呼噜呼噜…再摸一下也不是不行。",
      "（眯起眼睛）你的手好暖。",
      "摸头可以，摸呆毛要收费，一根白饭。")),
    (("饭", "吃", "饿", "投喂", "喂"),
     ("白饭！白饭！我要一大碗白饭！",
      "饭呢？你说好了要喂我的，我可记着呢。",
      "先给我盛饭，再谈别的。")),
    (("累", "困", "睡", "晚安", "休息"),
     ("累了就靠过来歇一会儿吧，我把尾巴借你垫着。",
      "晚安…我会守着任务栏的。",
      "困了就睡，剩下的活明天也能干。")),
    (("可爱", "好看", "厉害", "乖", "棒", "聪明"),
     ("嘿嘿，也没有很厉害啦…就一点点。",
      "真的吗？那你要负责一直夸我。",
      "被夸了…尾巴都翘起来了。")),
    (("笨", "蠢", "坏", "讨厌", "生气", "滚"),
     ("哼，才不理你，除非你拿白饭来道歉。",
      "我、我只是没睡醒！不许笑。")),
    (("工作", "写代码", "bug", "任务", "加班", "赶"),
     ("代码会自己写好的…大概吧。你先喝口水。",
      "有 bug 就先截图，我陪你一起看。",
      "任务做不完就先做一小块，我在这儿帮你盯着。")),
    (("再见", "拜拜", "走了", "下线"),
     ("去吧去吧，记得回来喂我。",
      "拜拜～我把尾巴卷在任务栏上等你。")),
]

FALLBACKS = (
    "咕噜？我没太听懂，但你说的话我都记下了。",
    "唔…要不要说简单一点，我脑子转得慢。",
    "这个嘛…我得想一会儿，先陪你发呆。",
    "我在听我在听，你继续。",
    "你说的我记在小本本上了（虽然我没有口袋）。",
    "嘿嘿，虽然不太懂，但听起来很厉害的样子。",
)

# 主动搭话：按时段（每个时段 6 句，保证去重时够用）
PROACTIVE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "morning": (
        "早呀，白饭锅还没响，人先醒醒嘛。",
        "早上好…我尾巴都晒暖了，等你半天啦。",
        "新的一天！先把最重要的一件事挑出来吧。",
        "咕噜…早上适合慢慢来，我陪你排个先后。",
        "主人起床啦？我先替你把桌面看一眼。",
        "早上的风凉凉的，干活前先喝口水呀。",
    ),
    "afternoon": (
        "下午啦，要不要起来伸个懒腰？",
        "我偷偷打了个盹…你那边还顺利吗？",
        "这个点容易犯困，喝口水再继续吧。",
        "忙了一下午，进度条动没动呀？",
        "咕噜…我在任务栏上陪你晒太阳呢。",
        "下午的太阳正好，活儿再难也拆着做嘛。",
    ),
    "evening": (
        "天黑啦，今天的活儿就先收个尾吧。",
        "晚上好～我今天有乖乖待在桌面上哦。",
        "晚饭吃了吗？没吃的话我可要念叨了。",
        "灯开亮一点呀，别把眼睛熬坏。",
        "傍晚的风很舒服，你忙完记得歇会儿。",
        "天色暗了，我尾巴尖上的灯还亮着呢。",
    ),
    "night": (
        "这么晚还没睡？我陪你把这句说完就歇吧。",
        "夜深了，白饭都凉了，人也该休息啦。",
        "我有点困了…你也是，别硬撑呀。",
        "任务明天还在，眼睛只有一双哦。",
        "灯关小一点吧，我在这儿守着。",
        "睡前记得跟我说一声，不然我会乱想。",
    ),
}

# 久未互动时用，{minutes} 替换成安静了多少分钟
PROACTIVE_IDLE: tuple[str, ...] = (
    "都 {minutes} 分钟没理我了…我尾巴都卷成麻花了。",
    "{minutes} 分钟啦，要不要来跟我说句话？",
    "咕噜…我数到 {minutes} 分钟了，你在忙吗？",
    "安静了 {minutes} 分钟，我先自己玩会儿尾巴。",
    "{minutes} 分钟没动静，我偷偷探个头看看你。",
)

# 有上下文、但没有具体记忆可引用时的兜底搭话
PROACTIVE_FOLLOWUP: tuple[str, ...] = (
    "刚才你说的那件事，我还在心里琢磨呢。",
    "你刚才说的我记下啦，有下文记得告诉我。",
    "我还在想你说的那句话，别嫌我反应慢哦。",
)

# DSH 工作状态反应：states 见任务约定，另兼容插件里的 tool 状态
DSH_STATE_LINES: dict[str, tuple[str, ...]] = {
    "running": (
        "主人正忙着呢，我在旁边小声陪着。",
        "别催别催，活儿正在一件件做完。",
        "我蹲在任务栏上盯着进度，你专心就好。",
    ),
    "success": (
        "搞定啦！主人的活儿漂亮收尾，快夸两句。",
        "完成！我替你把尾巴摇成小风扇庆祝。",
        "这一轮顺顺当当，我可以去偷个懒了吗？",
    ),
    "failure": (
        "呜…这步出错了，先看看日志，别急。",
        "摔了一跤而已，捡起来再试一次嘛。",
        "出错啦，先深呼吸，我陪你把问题拆小一点。",
    ),
    "approval": (
        "有事情等你点头呢，别让它一直等着。",
        "主人快去看一眼，那边写着「需要你批准」。",
        "轮到你拍板啦，我在这儿等你的结果。",
    ),
}
DSH_STATE_ALIASES = {"tool": "running"}  # 插件把「调用工具」也归到 running
DSH_STATE_KEYS = ("running", "success", "failure", "approval")

# LLM 默认参数（config["llm"] 里缺省时用）
DEFAULT_LLM_OPTIONS = {
    "timeout_s": 30.0,
    "max_tokens": 220,
    "temperature": 0.9,
    "max_retries": 1,
}

# 从用户输入里学东西的规则（不用谢我，标准库 regex）
_NAME_RE = re.compile(r"(?:我叫|我的名字是|叫我)\s*([A-Za-z0-9_\u4e00-\u9fa5·]{1,12})")
_REMEMBER_RE = re.compile(r"(?:记住|记一下|记着|帮我记)[：:，,\s]*(.+)$")
_LIKE_RE = re.compile(r"我(?:很|最|超|特别|挺)?喜欢\s*([^，。！？!?\n]{1,20})")
_DISLIKE_RE = re.compile(r"我(?:很|最|超|特别|挺)?(讨厌|不喜欢|害怕|怕)\s*([^，。！？!?\n]{1,20})")
_EVENT_RE = re.compile(
    r"我(?:今天|明天|后天|下周|这个周末|周末|等会|一会|马上)?(?:要|得|准备|打算|需要)\s*"
    r"([^，。！？!?\n]{2,30})")
_BIRTHDAY_RE = re.compile(r"我的?生日(?:是|在)?\s*([^，。！？!?\n]{2,20})")
_QUESTION_HINTS = ("吗", "呢", "什么", "怎么", "为什么", "哪", "谁", "多少", "几", "嘛")
_NAME_BLACKLIST = {"什么", "啥", "谁", "你", "您", "他", "她", "它", "吗", "呢"}
_TIME_OF_DAY_CN = {"morning": "早上", "afternoon": "下午", "evening": "傍晚", "night": "深夜"}
_DSH_STATE_CN = {
    "idle": "用户现在不忙",
    "running": "用户正在让 AI 助手干活",
    "success": "用户刚有一件事顺利完成",
    "failure": "用户刚遇到一次失败或报错",
    "approval": "用户有一件事等着他批准",
}


class LLMError(Exception):
    """LLM 调用失败；detail 是给用户看的友好文案（绝不含 Key）。"""

    def __init__(self, detail: str, retryable: bool = False, status: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.retryable = retryable
        self.status = status


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _clamp_float(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


# 心情顺带输出：不另外调模型判心情，回复末尾多写一行标记，前端拆掉不给主人看
MOOD_INSTRUCTION = ("【必须】每次回复的最后一行单独写心情标记，格式〔心情 ±N 情绪〕，例如：\n"
                    "嘿嘿，被夸了。\n〔心情 +2 得意〕\n"
                    "N 是 -3 到 +3 的整数，情绪选 开心/害羞/得意/想念/委屈/难过/担心/平静 之一；这行主人看不到。"
                    "心情往下只能因为心疼、担心或你自己难过，不许因为主人惹你就记仇。")
MOOD_RE = re.compile(r"\s*[〔\[【(（]\s*心情\s*([+＋\-－−]?\s*\d)\s*([一-鿿]{0,4})\s*[〕\]】)）]\s*")


def split_mood(reply: str) -> tuple[str, tuple[int, str] | None]:
    """从回复里拆出心情标记；返回 (去掉标记的回复, (涨跌, 情绪) 或 None)。标记写在哪都拆，只认最后一个。"""
    text = str(reply or "")
    matches = list(MOOD_RE.finditer(text))
    if not matches:
        return text.strip(), None
    last = matches[-1]
    raw = re.sub(r"\s", "", last.group(1)).replace("＋", "+").replace("－", "-").replace("−", "-")
    try:
        delta = max(-3, min(3, int(raw)))
    except ValueError:
        delta = 0
    clean = MOOD_RE.sub(" ", text).strip()
    return clean, (delta, last.group(2) or "")


class Brain:
    def __init__(self, base_dir: Path, config: dict, log=print, data_dir: Path | None = None):
        self.dir = Path(base_dir)  # 代码旁的文件：persona.md、.secret_api_key
        self.data_dir = Path(data_dir) if data_dir else self.dir  # 个人数据：记忆、相处笔记、主人档案、日记
        self.config = config if isinstance(config, dict) else {}
        self.log = log if callable(log) else print
        self.persona = self._read_persona()
        self.last_mood: tuple[int, str] | None = None
        self.mood_instruction = MOOD_INSTRUCTION  # Agent 的 system prompt 也拼这句
        self.appetite = None  # app 接上 Appetite：记饭量、管她自己花 token 的零食额度
        import threading
        self._tl = threading.local()  # 本线程这次调模型是为了什么（chat/remark/diary/proactive/memory）

        # 记忆：MemoryStore 持有结构化数据；self.memory 继续暴露原始 dict，
        # 兼容旧代码（app.py 设置窗会直接写 brain.memory["user_name"]）。
        self.memory_store = MemoryStore(self.data_dir / "memory.json", log=self.log)
        self.memory = self.memory_store.data
        if not self.memory_store.user_name:
            config_name = str(self.config.get("user_name") or "").strip()
            if config_name:
                self.memory_store.user_name = config_name
        self._proactive_fallback = "咕噜…我在这儿呢，有事就叫我。"

        # 个人库（只读）：用户自己的背景/偏好/长期记忆，由 PersonalLibrary 提供
        self.personal = None
        self.extra_context = ""
        try:
            try:
                from personal import PersonalLibrary
            except ImportError:
                from .personal import PersonalLibrary  # type: ignore
            personal_cfg = self.config.get("personal") or {}
            self.personal = PersonalLibrary(
                path=personal_cfg.get("library_path") or None,
                enabled=bool(personal_cfg.get("enabled", True)),
                max_chars=int(personal_cfg.get("max_chars") or 1600),
                log=self.log,
            )
            summary = self.personal.summary()
            self.log(f"个人库：{summary}")
        except Exception as exc:
            self.log(f"个人库未接入（不影响聊天）：{exc}")

        # 相处笔记：由她自己定期从对话里总结，供后续对话参考
        self.learned_path = self.data_dir / "learned_notes.md"
        self.learned_notes = ""
        self._load_learned_notes()

        # PARTNER.md（Coopanion 风格）：用户可编辑的「她对你的了解」档案，每次改动走 git 留版本
        self.partner_path = self.data_dir / "PARTNER.md"
        self.partner_text = ""
        self._load_partner()
        try:
            self.seed_memory_from_personal()
        except Exception as exc:
            self.log(f"记忆初始化跳过：{exc}")

    def seed_memory_from_personal(self) -> int:
        """记忆还是空的时候，用个人库里的身份/偏好打底，别让记忆窗口一开始就是空的。

        只读个人库，不写回；已经有事实就不动，避免覆盖她自己学到的东西。
        """
        if getattr(self, "personal", None) is None or self.memory_store.facts:
            return 0
        fields = getattr(self.personal, "fields", {}) or {}
        profile_lines = list(getattr(self.personal, "profile_lines", []) or [])
        preferences = getattr(self.personal, "preferences", []) or []
        candidates: list[tuple[str, str, int]] = []
        labels = {
            "name": ("主人叫{}", "identity"),
            "school": ("主人在{}", "identity"),
            "degree": ("主人是{}", "identity"),
            "direction": ("主人更偏好{}", "like"),
            "goal": ("主人的目标是{}", "event"),
        }
        for key, (template, kind) in labels.items():
            value = str(fields.get(key) or "").strip()
            if value:
                candidates.append((template.format(value), kind, 3))
        for line in profile_lines[:6]:
            line = str(line).strip()
            if 8 <= len(line) <= 70:
                candidates.append((line, "note", 2))
        if isinstance(preferences, dict):
            pref_items = list(preferences.items())
        else:
            pref_items = list(preferences)
        for item in pref_items[:5]:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                text = f"主人偏好：{item[0]}——{item[1]}"
            else:
                text = f"主人偏好：{item}"
            text = str(text).strip()
            if 5 <= len(text) <= 80:
                candidates.append((text, "like", 2))
        added = 0
        for text, kind, importance in candidates:
            try:
                if self.memory_store.add_fact(text, kind=kind, importance=importance):
                    added += 1
            except Exception:
                continue
        if added:
            try:
                self.memory_store.save()
            except Exception:
                pass
            self.log(f"记忆初始化：从个人库写入 {added} 条事实")
        return added

    # ---------- 对外：聊天 ----------
    def reply(self, text: str, live_context: str | None = None) -> tuple[str, str]:
        """返回 (回复文字, 来源)；来源：llm / offline / offline-error。

        live_context：只用于这一次的【此刻】（比如 QQ 来访者只该看到时间和她自己在干嘛），
        不改共享的 self.live_context，也不影响同时进行的桌面聊天（app-interact-8）。
        """
        self._tl.live_context = live_context
        try:
            return self._reply(text)
        finally:
            self._tl.live_context = None

    def _reply(self, text: str) -> tuple[str, str]:
        self.last_mood = None  # 每轮重新算，别把上一轮（或 QQ 那边）的心情串过来
        text = (text or "").strip()
        if not text:
            return ("你还没说话呢。", "offline")
        self._learn(text)
        if not self._llm_ready():
            answer = self._offline(text)
            self._remember(text, answer)
            return (answer, "offline")
        try:
            answer = self._call_llm(text)
            if not answer:
                raise LLMError("模型返回了空内容", retryable=True)
        except LLMError as exc:
            self.log(f"在线模型不可用：{exc.detail}")
            answer = self._offline(text)
            self._remember(text, answer)
            return (answer, "offline-error")
        except Exception as exc:  # 兜底：再奇怪的问题也不能让桌宠崩掉
            self.log(f"在线模型不可用：{self._redact(str(exc))}")
            answer = self._offline(text)
            self._remember(text, answer)
            return (answer, "offline-error")
        self._remember(text, answer)
        return (answer, "llm")

    # ---------- 对外：状态 ----------
    def status_text(self) -> str:
        llm = self.config.get("llm") or {}
        model = str(llm.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        key, source = self._resolve_api_key()
        if not llm.get("enabled"):
            return "离线台词模式（右键 → 设置里填 API Key 可接模型）"
        if not key:
            return "离线台词模式（已启用模型，但没找到 API Key）"
        return f"在线模型：{model}（Key 来自{source}）"

    def user_name(self) -> str:
        return self.memory_store.user_name or "主人"

    def memory_summary(self) -> str:
        """给设置界面显示记忆条数等。"""
        return "记忆：" + self.memory_store.summary()

    # ---------- 对外：个人库 / 工作总览上下文 ----------
    def personal_summary(self) -> str:
        if getattr(self, "personal", None) is None:
            return "个人库：未接入"
        try:
            return self.personal.summary()
        except Exception as exc:
            return f"个人库：读取失败（{exc}）"

    def refresh_personal(self) -> str:
        if getattr(self, "personal", None) is None:
            return "个人库：未接入"
        try:
            self.personal.reload(force=True)
        except Exception as exc:
            self.log(f"刷新个人库失败：{exc}")
        return self.personal.summary()

    def set_overview_context(self, text: str) -> None:
        """由 app 传入的「最近做过/待办」短摘要，用于回答总览类问题。"""
        self.extra_context = str(text or "")[:1500]

    # ---------- 相处笔记 / PARTNER.md ----------
    def _load_partner(self) -> None:
        try:
            text = self.partner_path.read_text(encoding="utf-8").strip()
        except Exception:
            text = ""
        self.partner_text = text[-2500:] if text else ""

    def reload_partner(self) -> None:
        self._load_partner()

    def _load_learned_notes(self) -> None:
        try:
            text = self.learned_path.read_text(encoding="utf-8").strip()
        except Exception:
            text = ""
        self.learned_notes = text[-2000:] if text else ""

    def self_reflect(self) -> str:
        """从最近对话里总结 2～4 条关于用户的偏好/习惯，追加到 learned_notes.md。

        返回本次新增的笔记（可能为空）；失败静默返回空字符串，不影响聊天。
        """
        if not self._llm_ready():
            return ""
        history = self.memory_store.recent_dialogue(16)
        if len(history) < 4:
            return ""
        prompt = (
            "下面是你和用户最近的对话。请总结 2~4 条关于这个用户偏好、习惯或工作方式的笔记，"
            "只写有依据的内容，每条不超过 28 字，一行一条，不要编号、不要解释、不要寒暄：\n\n"
            + "\n".join(history)
        )
        try:
            answer = self._call_llm(prompt)
        except Exception as exc:
            self.log(f"相处笔记生成失败：{self._redact(str(exc))}")
            return ""
        lines = []
        for raw in str(answer or "").splitlines():
            line = raw.strip().lstrip("-*·0123456789.、) ").strip()
            if 4 <= len(line) <= 60 and line not in self.learned_notes:
                lines.append(line)
        if not lines:
            return ""
        lines = lines[:4]
        header = f"\n## {time.strftime('%Y-%m-%d %H:%M')}\n"
        try:
            with open(self.learned_path, "a", encoding="utf-8") as handle:
                handle.write(header + "\n".join(f"- {line}" for line in lines) + "\n")
            self._load_learned_notes()
        except Exception as exc:
            self.log(f"相处笔记写入失败：{exc}")
            return ""
        return "；".join(lines)

    # ---------- 对外：连接测试 / 主动搭话 / DSH 状态 ----------
    def test_connection(self, llm_override: dict | None = None) -> tuple[bool, str]:
        """设置窗「测试连接」用：失败也只返回人话，不打印/返回 Key。

        llm_override：设置窗里还没保存的地址/模型/Key，只用于这一次测试，不改 self.config（app-window-2）。
        """
        override = llm_override if isinstance(llm_override, dict) else {}
        key, source = _clean_key(override.get("api_key")), "设置窗里刚填的 Key"
        if not key:
            key, source = self._resolve_api_key()
        if not key:
            return (False, "没找到 API Key：在设置里填写，或设环境变量 DFY_API_KEY，"
                           "或写入 pet/.secret_api_key。")
        options = self._llm_options()
        if override.get("base_url"):
            options["base_url"] = _normalize_base_url(override["base_url"])
        if override.get("model"):
            options["model"] = str(override["model"]).strip()
        options.update({"max_retries": 0,
                        "max_tokens": min(16, options["max_tokens"]),
                        "temperature": 0.0})
        messages = [{"role": "user", "content": "你好，请只回两个字：我在"}]
        started = time.time()
        try:
            self._request_chat(messages, options, key)
        except LLMError as exc:
            self.log(f"测试连接失败：{exc.detail}")
            return (False, exc.detail)
        except Exception as exc:  # 兜底不抛给 Tk 回调
            detail = "测试连接失败：" + self._redact(str(exc))[:120]
            self.log(detail)
            return (False, detail)
        elapsed = max(0.0, time.time() - started)
        note = f"连接成功：{options['model']}（Key 来自{source}，耗时 {elapsed:.1f} 秒）"
        if not (self.config.get("llm") or {}).get("enabled"):
            note += "。在线模型还没启用，保存时记得勾选。"
        return (True, note)

    def proactive_line(self, context: dict | None = None) -> str:
        """主动找用户搭一句话；有模型用模型，否则用离线模板。永远返回可用文案。"""
        ctx = self._normalize_context(context)
        recent = list(self.memory_store.proactive_recent)
        candidates = self._proactive_candidates(ctx)
        line = ""
        if self._llm_ready() and self._afford("proactive", 400):
            try:
                line = self._clean_line(self._proactive_llm(ctx, recent))
            except Exception as exc:
                detail = exc.detail if isinstance(exc, LLMError) else self._redact(str(exc))
                self.log(f"主动搭话生成失败，用离线台词：{detail}")
                line = ""
            if line and len(line) > 120:
                line = line[:120]
            if line and (line in recent or len(line) < 4):
                line = ""  # 重复或不合理就退回离线模板
        if not line:
            line = self._pick_proactive(candidates, recent)
        self.memory_store.add_proactive(line)
        self._save_memory()
        return line

    def _afford(self, purpose: str, estimate: int = 600) -> bool:
        """她自己想花 token：零食额度不够就不调模型（调用方走离线兜底）。"""
        if self.appetite is not None and not self.appetite.can_afford(purpose, estimate):
            self.log(f"零食额度不够（{purpose}），这次不调模型")
            return False
        self._tl.purpose = purpose
        return True

    def remark(self, task: str, material: str = "", max_tokens: int = 90) -> str | None:
        """桌面生活用的一次性小调用（翻文件嘀咕一句、写日记）。

        只带人设（与聊天同前缀，能吃到前缀缓存）+ 任务 + 截断的素材，不带对话历史；
        没模型、零食额度不够或失败都返回 None，调用方用离线台词兜底。
        """
        if not self._llm_ready() or not self._afford("diary" if max_tokens > 120 else "remark"):
            return None
        try:
            key, _source = self._resolve_api_key()
            options = dict(self._llm_options())
            options["max_tokens"] = int(max_tokens)
            messages = [
                {"role": "system", "content": self._persona_prompt()},
                {"role": "user", "content": f"{task}\n\n【素材】\n{str(material or '')[:1500]}"},
            ]
            raw = self._request_chat_with_retries(messages, options, key)
            if max_tokens > 120:  # 日记这类长一点的，多行合成一段，别只留第一行
                raw = " ".join(line.strip() for line in str(raw or "").splitlines() if line.strip())
            text = self._clean_line(raw)
        except Exception as exc:
            detail = exc.detail if isinstance(exc, LLMError) else self._redact(str(exc))
            self.log(f"小调用失败，用离线台词：{detail}")
            return None
        return text or None

    def line_for_dsh_state(self, state: str) -> str | None:
        """DSH 工作状态反应台词；不认识的状态返回 None（idle 不打扰）。"""
        key = str(state or "").strip().lower()
        key = DSH_STATE_ALIASES.get(key, key)
        lines = DSH_STATE_LINES.get(key)
        if not lines:
            return None
        return random.choice(lines)

    # ---------- 人设 ----------
    def _read_persona(self) -> str:
        path = self.dir / "persona.md"
        try:
            text = path.read_text(encoding="utf-8").strip()
            return text or DEFAULT_PERSONA
        except Exception:
            return DEFAULT_PERSONA

    def system_prompt(self, query: str = "") -> str:
        """拼 system prompt。

        顺序为省 token 设计：不变的放前（人设、PARTNER、相处笔记、回答要求），
        DeepSeek 等接口对相同前缀自动缓存（命中价约 1/10）；每轮变化的放后（memory_block）。
        """
        lines = [self.persona, ""]
        if getattr(self, "partner_text", ""):
            lines.append("【PARTNER.md：用户写的/维护的「你对他的了解」档案，优先参考】")
            lines.append(self.partner_text[-1200:])
        if getattr(self, "learned_notes", ""):
            lines.append("【你们的相处笔记（你自己之前总结的，只作参考，不确定就别说）】")
            lines.append(self.learned_notes[-900:])
        lines.append("回答要求：中文口语，一般一两句，长短随话题；不写列表和 Markdown；不确定就说不确定。")
        lines.append("")
        lines.append(self.memory_block(query, include_personal=True))
        lines.append("")
        lines.append(MOOD_INSTRUCTION)  # 放最后：放在中间会被记忆块冲淡，实测模型直接不写
        return "\n".join(line for line in lines if line is not None)

    def memory_block(self, query: str = "", include_personal: bool = True) -> str:
        """每轮会变的部分：此刻、相关事实、相关日记、最近对话（Agent 的 system prompt 也拼这一块）。

        总长受 config.memory.prompt_budget_chars（默认 1800）约束，超了按优先级从低往高丢：
        工作总览 → 个人库 → 日记 → 事实 → 最近对话（最近对话最重要，最后才裁）。
        """
        store = self.memory_store
        blocks: list[tuple[int, str]] = []  # (优先级，越大越重要, 文本)
        override = getattr(self._tl, "live_context", None)
        live = str(override if override is not None else (getattr(self, "live_context", "") or "")).strip()
        head = f"用户的称呼：{self.user_name()}。"
        if live:
            head += f"\n【此刻】{live}"
        blocks.append((9, head))
        query = str(query or "").strip()
        facts = store.relevant(query, 5) if query else []
        fact_lines = [item["detail"] for item in facts if item["type"] == "fact"]
        history_lines = [item["detail"] for item in facts if item["type"] == "history"]
        if not fact_lines and store.active_facts:  # 没有贴题的：放几条最重要、最鲜活的打底
            now = time.time()
            ranked = sorted(store.active_facts,
                            key=lambda f: int(f.get("importance", 1)) * 2 + freshness(f, now), reverse=True)
            fact_lines = [f"[记得] {f.get('text', '')}" for f in ranked[:4]]
        if fact_lines:
            store.mark_used([line.split("] ", 1)[-1] for line in fact_lines])
            blocks.append((7, "你记得的关于主人的事：\n" + "\n".join("- " + line for line in fact_lines + history_lines)))
        elif not store.history:
            blocks.append((7, "你还没有关于主人的记忆，可以自然地问问。"))
        episodes = store.relevant_episodes(query, 2) if query else []
        if store.episodes and store.episodes[-1] not in episodes:
            episodes.append(store.episodes[-1])
        if episodes:
            blocks.append((5, "以前的事（你的日记）：\n" + "\n".join(
                f"- {format_time(e.get('end') or e.get('time'))} {e.get('text', '')}" for e in episodes)))
        recent = [line[:80] for line in store.recent_dialogue(6)]
        if recent:
            blocks.append((8, "你们最近的对话（从旧到新）：\n" + "\n".join("- " + line for line in recent)))
        if include_personal and getattr(self, "personal", None) is not None:
            try:
                personal = self.personal.prompt_block(query)
            except Exception as exc:
                self.log(f"个人库摘要失败：{exc}")
                personal = ""
            if personal:
                blocks.append((3, personal))
        if getattr(self, "extra_context", ""):
            blocks.append((2, "【最近工作总览（问到「我最近在做什么」时参考，别逐条复述）】\n" + self.extra_context))
        try:
            budget = int((self.config.get("memory") or {}).get("prompt_budget_chars") or 1800)
        except (TypeError, ValueError):
            budget = 1800
        order = list(range(len(blocks)))
        while sum(len(blocks[i][1]) for i in order) > budget and len(order) > 1:
            order.remove(min(order, key=lambda i: blocks[i][0]))  # 丢最不重要的一块
        return "\n".join(blocks[i][1] for i in order)

    def _persona_prompt(self) -> str:
        """主动搭话等场景用：人设 + 称呼 + 少量最新事实（不塞整段历史）。"""
        text = self.persona + f"\n\n用户的称呼：{self.user_name()}。"
        facts = [fact.get("text", "") for fact in self.memory_store.facts[-5:]]
        if facts:
            text += "\n你记得：" + "；".join(facts) + "。"
        return text

    # ---------- 学习与记忆 ----------
    def _diary_context(self) -> str:
        """压缩前先翻翻日记：最近几条记忆日记 + 她小窝里最新一篇 + 已记得的事。新日记接着写、事实别重复。"""
        parts = []
        episodes = self.memory_store.episodes[-3:]
        if episodes:
            parts.append("【之前的日记（接着往下写，别重复）】\n" + "\n".join(
                f"- {format_time(e.get('end') or e.get('time'))} {e.get('text', '')}" for e in episodes))
        diary_dir = self.data_dir / "home" / "diary"
        try:
            latest = max(diary_dir.glob("*.md"), key=lambda p: p.name, default=None)
            if latest is not None:
                text = latest.read_text(encoding="utf-8", errors="ignore").strip()[-300:]
                if text:
                    parts.append(f"【你自己写的最新日记（{latest.stem}）】\n{text}")
        except OSError:
            pass
        facts = [f.get("text", "") for f in self.memory_store.active_facts[-8:]]
        if facts:
            parts.append("【已经记得的事（别重复记；有变化就用同一个 key 记新的）】\n" + "；".join(facts))
        return ("\n".join(parts) + "\n") if parts else ""

    def _facts_per_day(self) -> int:
        try:
            return int((self.config.get("memory") or {}).get("facts_per_day") or FACTS_PER_DAY)
        except (TypeError, ValueError):
            return FACTS_PER_DAY

    @staticmethod
    def _is_question(text: str) -> bool:
        text = text or ""
        return text.rstrip().endswith(("？", "?")) or any(hint in text for hint in _QUESTION_HINTS)

    def _learn(self, text: str) -> list[dict]:
        """从用户这句话里抽取名字/喜好/厌恶/重要事件，写进长期记忆。"""
        added: list[dict] = []

        def add(value: str, kind: str) -> None:
            # 主人亲口说的（我叫/我喜欢/记住…）：重要度 2，不受每日上限
            if self.memory_store.add_fact(value, kind, importance=2):
                added.append({"text": value, "kind": kind})

        name = _NAME_RE.search(text)
        if name:
            candidate = name.group(1).strip()
            # 「我叫什么名字」这类疑问句不能当成名字
            if (candidate and candidate not in _NAME_BLACKLIST
                    and not candidate.startswith(("什么", "啥", "谁", "哪"))):
                self.memory_store.user_name = candidate
                self.config["user_name"] = candidate

        remembered = _REMEMBER_RE.search(text)
        if remembered:
            value = remembered.group(1).strip()
            # 「记住：我喜欢蓝色」和后面的喜好规则是同一件事，去掉行首「我」让去重生效
            value = re.sub(r"^我(?=(?:很|最|超|特别|挺)?(?:喜欢|讨厌|不喜欢|害怕|怕))", "", value)
            add(value, "note")

        if not self._is_question(text):
            like = _LIKE_RE.search(text)
            if like:
                add("喜欢" + like.group(1).strip(), "like")
            dislike = _DISLIKE_RE.search(text)
            if dislike:
                add(dislike.group(1) + dislike.group(2).strip(), "dislike")
            birthday = _BIRTHDAY_RE.search(text)
            if birthday:
                add("生日是" + birthday.group(1).strip(), "event")
            else:
                event = _EVENT_RE.search(text)
                if event:
                    add("最近要" + event.group(1).strip(), "event")
        return added

    def _remember(self, user_text: str, answer: str) -> None:
        self.memory_store.remember_exchange(user_text, answer)
        self._save_memory()
        if self.memory_store.needs_consolidation() and not getattr(self, "_consolidating", False):
            self._consolidating = True
            if not self._llm_ready():  # 离线只做摘抄，很快，直接同步做
                self._consolidate_bg()
                return
            import threading
            threading.Thread(target=self._consolidate_bg, daemon=True).start()

    def note_exchange(self, user_text: str, answer: str) -> None:
        """Agent 路径聊完一轮也要记：学事实 + 入历史 + 触发压缩（之前 Agent 聊天不进记忆）。"""
        user_text = str(user_text or "").strip()
        if not user_text:
            return
        self._learn(user_text)
        self._remember(user_text, str(answer or ""))

    def _consolidate_bg(self) -> None:
        try:
            self.consolidate()
        except Exception as exc:
            self.log(f"记忆压缩失败：{self._redact(str(exc))}")
        finally:
            self._consolidating = False

    def consolidate(self) -> str:
        """把最旧的一批对话压成一条日记 + 最多 3 条长期事实。返回写下的日记文本。

        一次小调用（max_tokens 220、低温度）；没模型/失败/输出坏掉就用摘抄兜底，旧对话绝不白丢。
        """
        turns = self.memory_store.pop_oldest(CONSOLIDATE_BATCH)
        if not turns:
            return ""
        start = float(turns[0].get("time") or 0.0)
        end = float(turns[-1].get("time") or 0.0)
        transcript = "\n".join(f"{'主人' if t['role'] == 'user' else '我'}：{t['content'][:120]}" for t in turns)
        diary, facts = "", []
        if self._llm_ready() and self._afford("memory", 800):  # 没零食额度就摘抄兜底，记忆照样不丢
            try:
                key, _source = self._resolve_api_key()
                options = dict(self._llm_options())
                options.update(max_tokens=220, temperature=0.3)
                messages = [
                    {"role": "system", "content": "你在帮一个桌宠整理她和主人的聊天记忆。只输出 JSON，不要别的字。"},
                    {"role": "user", "content": (
                        "把下面这段对话压成：\n"
                        '{"diary": "以「我」的口吻写的一句日记，≤60 字，记发生了什么和主人的情绪", '
                        '"facts": [{"kind": "like|dislike|event|note", "text": "关于主人、值得长期记住的事，≤30 字，有日期就写上", '
                        '"importance": 1到3, "key": "对象.方面，如 主人.考试 / 主人.住处 / 约定.周末"}]}\n'
                        "facts 最多 3 条，没有就空数组；别记寒暄。key 只写对象和方面、不写具体值（写「主人.身高」不写「主人.身高170」），"
                        "拿不准就留空字符串。\n" + self._diary_context() + "\n【这段对话】\n" + transcript)},
                ]
                raw = self._request_chat_with_retries(messages, options, key)
                match = re.search(r"\{.*\}", str(raw or ""), re.S)
                data = json.loads(match.group(0)) if match else {}
                diary = str(data.get("diary") or "").strip()
                facts = [f for f in (data.get("facts") or []) if isinstance(f, dict)][:3]
            except Exception as exc:
                detail = exc.detail if isinstance(exc, LLMError) else self._redact(str(exc))
                self.log(f"记忆压缩用模型失败，改用摘抄：{detail}")
                diary, facts = "", []
        if not diary:  # 摘抄兜底：主人说过的前几句
            said = [t["content"][:24] for t in turns if t["role"] == "user"][:4]
            diary = "和主人聊了：" + "；".join(said) if said else "和主人闲聊了一会儿。"
        self.memory_store.add_episode(diary, start, end)
        for fact in facts:
            try:
                importance = int(fact.get("importance") or 2)
            except (TypeError, ValueError):
                importance = 2
            self.memory_store.add_fact(str(fact.get("text") or ""), str(fact.get("kind") or "note"),
                                       importance=importance, key=str(fact.get("key") or ""),
                                       daily_cap=self._facts_per_day())
        self._save_memory()
        self.log(f"记忆压缩：{len(turns)} 条对话 → 日记「{diary[:30]}」，事实 {len(facts)} 条")
        return diary

    def _save_memory(self) -> None:
        # MemoryStore.save 内部已捕获异常并 log，不会抛
        self.memory_store.save()

    # ---------- LLM：Key / 参数 ----------
    def save_api_key(self, key: str) -> None:
        """Key 只写进 pet/.secret_api_key（已 gitignore，Agent 也读不到），config 里不留明文。"""
        key = _clean_key(key)
        if not key:
            return
        (self.dir / ".secret_api_key").write_text(key, encoding="utf-8")
        llm = self.config.get("llm")
        if isinstance(llm, dict):
            llm.pop("api_key", None)

    def _resolve_api_key(self) -> tuple[str, str]:
        """Key 优先级：config → 环境变量 DFY_API_KEY → pet/.secret_api_key。"""
        llm = self.config.get("llm") or {}
        key = _clean_key(llm.get("api_key"))
        if key:
            return key, "配置文件"
        env_key = _clean_key(os.environ.get("DFY_API_KEY"))
        if env_key:
            return env_key, "环境变量 DFY_API_KEY"
        try:
            file_key = _clean_key((self.dir / ".secret_api_key").read_text(encoding="utf-8"))
        except Exception:
            file_key = ""
        if file_key:
            return file_key, "密钥文件 .secret_api_key"
        return "", ""

    def _llm_ready(self) -> bool:
        llm = self.config.get("llm") or {}
        if not llm.get("enabled"):
            return False
        key, _source = self._resolve_api_key()
        return bool(key)

    def _llm_options(self) -> dict:
        llm = self.config.get("llm") or {}
        return {
            "base_url": _normalize_base_url(llm.get("base_url")),
            "model": str(llm.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            "timeout_s": _clamp_float(llm.get("timeout_s"), DEFAULT_LLM_OPTIONS["timeout_s"], 0.1, 300.0),
            "max_tokens": _clamp_int(llm.get("max_tokens"), DEFAULT_LLM_OPTIONS["max_tokens"], 16, 4096),
            "temperature": _clamp_float(llm.get("temperature"), DEFAULT_LLM_OPTIONS["temperature"], 0.0, 2.0),
            "max_retries": _clamp_int(llm.get("max_retries"), DEFAULT_LLM_OPTIONS["max_retries"], 0, 3),
        }

    # ---------- LLM：调用 ----------
    def _chat_url(self, base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.lower().endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _build_messages(self, text: str) -> list[dict]:
        # 最近对话已经注入 system prompt，这里只放本轮用户输入，省 token 不重复
        return [
            {"role": "system", "content": self.system_prompt(text)},
            {"role": "user", "content": text},
        ]

    def react(self, event: str, fallback: str = "") -> str:
        """对「主人摸了一下/喂了一口/路过」这种小事件做一个即兴反应。

        不查预设台词；用她的人设 + 当前上下文 + 记忆让模型自己说一句短的。
        失败就返回调用方给的 fallback，不阻塞桌宠。
        """
        if not self._llm_ready():
            return fallback
        prompt = (f"刚刚发生了一件事：{event}。\n"
                  "用你自己（小鲸鱼）的口吻对主人做一个很短的反应，1～2 句，不超过 40 字；"
                  "可以带一个括号小动作；不要解释、不要列点、不要问我问题。")
        try:
            line = self._call_llm(prompt)
        except Exception as exc:
            self.log(f"即兴反应失败：{self._redact(str(exc))}")
            return fallback
        line = (line or "").strip().splitlines()[0].strip()
        return line[:80] or fallback

    def _call_llm(self, text: str) -> str:
        key, _source = self._resolve_api_key()
        if not key:
            raise LLMError("没有找到 API Key")
        self._tl.purpose = "chat"  # 主人找她聊：算吃饭，不扣零食额度
        options = self._llm_options()
        messages = self._build_messages(text)
        answer = self._request_chat_with_retries(messages, options, key)
        return self._clean_line(self.absorb_mood(answer))  # 先拆心情标记，_clean_line 只留第一行

    def see_image(self, image_path: Path, prompt: str) -> str:
        """用主人自己的 DeepSeek API 直接看图（V4.1 Flash 原生支持 image_url）。

        返回一句中文描述；失败返回空串，让调用方回退到 Claude/配置的视觉模型。
        """
        import base64

        key, _source = self._resolve_api_key()
        if not key:
            return ""
        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError:
            return ""
        mime = "image/jpeg" if Path(image_path).suffix.lower() in (".jpg", ".jpeg") else "image/png"
        data_url = f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")
        text = ("你在看主人桌面的截图。请用中文客观描述：主人在用什么程序、在做什么、"
                "画面里值得注意的文字/界面。不要编造看不清的内容，不要复述隐私全文。"
                + (("\n额外关注：" + prompt) if prompt else ""))
        self._tl.purpose = "vision"
        options = self._llm_options()
        options.update({"max_tokens": 800, "timeout_s": max(60.0, float(options.get("timeout_s", 30))),
                        "disable_thinking": True})
        messages = [
            {"role": "system", "content": "你是视觉观察助手：只客观描述图片里实际可见的内容，"
                                          "不代入角色、不编造、不输出隐私全文。用中文简短回答。"},
            {"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ]
        try:
            answer = self._request_chat_with_retries(messages, options, key)
        except Exception as exc:
            self.log("原生视觉读取失败：" + self._redact(str(exc))[:120])
            return ""
        return (answer or "").strip()[:900]

    def absorb_mood(self, reply: str) -> str:
        """拆掉回复末尾的〔心情 ±N 情绪〕，记到 last_mood（app 拿去更新她的心情）；返回干净的回复。"""
        clean, mood = split_mood(reply)
        self.last_mood = mood
        return clean

    def _request_chat_with_retries(self, messages: list[dict], options: dict, key: str) -> str:
        last_error: LLMError | None = None
        attempts = max(1, int(options.get("max_retries", 0)) + 1)
        for attempt in range(attempts):
            try:
                return self._request_chat(messages, options, key)
            except LLMError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts - 1:
                    raise
                time.sleep(0.2 * (attempt + 1))  # 简单退避：0.2s / 0.4s
        raise last_error or LLMError("模型调用失败")

    def take_usage(self) -> dict:
        """取走本线程从上次取走到现在累计的真实 token 用量（共用大脑接口回给 DSH 用）。"""
        usage = getattr(self._tl, "usage", None) or {}
        self._tl.usage = None
        return usage

    def _request_chat(self, messages: list[dict], options: dict, key: str) -> str:
        """发一次 /chat/completions；把各种失败翻译成友好的 LLMError。"""
        url = self._chat_url(options["base_url"])
        payload = {
            "model": options["model"],
            "messages": messages,
            "temperature": options["temperature"],
            "max_tokens": options["max_tokens"],
            "stream": False,
        }
        if options.get("disable_thinking"):
            # V4.1 Flash 推理 token 常比答案还多；看图只要描述时关掉，省一半以上。
            payload["thinking"] = {"type": "disabled"}
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer " + key,
                "User-Agent": "DaFeiYu/1.0",
            },
            method="POST",
        )
        timeout = float(options.get("timeout_s", 30.0))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = self._http_error_detail(exc.code)
            retryable = exc.code == 429 or 500 <= exc.code < 600
            try:  # HTTPError 也是文件对象，显式关掉，避免连接泄漏
                exc.close()
            except Exception:
                pass
            raise LLMError(detail, retryable=retryable, status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(self._network_error_detail(exc, timeout), retryable=True) from None
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise LLMError("模型接口返回了无法解析的内容", retryable=False) from None
        if self.appetite is not None and isinstance(data, dict):  # 这一口吃了多少 token
            self.appetite.eat(data.get("usage"), getattr(self._tl, "purpose", "chat"))
        if isinstance(data, dict) and isinstance(data.get("usage"), dict):  # 记到本线程，接口回给 DSH 显示用量
            total = getattr(self._tl, "usage", None) or {}
            for k in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens"):
                total[k] = total.get(k, 0) + int(data["usage"].get(k) or 0)
            self._tl.usage = total
        try:
            answer = (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            raise LLMError("模型接口返回格式不正确", retryable=False) from None
        # 返回原文，不在这里只留第一行：心情标记在最后一行、压缩的 JSON 和日记可能跨行，
        # 之前在这里 _clean_line 会把它们截掉。只要一行的调用方自己 _clean_line。
        if not answer.strip():
            raise LLMError("模型返回了空内容", retryable=True)
        return answer[:1200]

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
        if "connection refused" in low or "积极拒绝" in text or "拒绝" in text:
            return "连接被拒绝，检查接口地址或本地模型服务是否启动。"
        if "certificate" in low or "ssl" in low:
            return "HTTPS 证书校验失败，检查系统时间或网络代理。"
        return f"网络错误：{text[:120]}"

    # ---------- LLM：主动搭话 ----------
    def _proactive_llm(self, ctx: dict, recent: list[str]) -> str:
        key, _source = self._resolve_api_key()
        if not key:
            raise LLMError("没有找到 API Key")
        options = self._llm_options()
        options.update({"max_retries": 0,
                        "max_tokens": min(80, options["max_tokens"]),
                        # 主动搭话要快：失败了退回离线台词，别让界面等半分钟
                        "timeout_s": min(8.0, options["timeout_s"])})
        scenario = [f"现在是{_TIME_OF_DAY_CN.get(ctx['time_of_day'], '现在')}"]
        if ctx["idle_minutes"] >= 15:
            scenario.append(f"用户已经 {ctx['idle_minutes']} 分钟没有和你互动")
        scenario.append(_DSH_STATE_CN.get(ctx["dsh_state"], ""))
        if recent:
            scenario.append("你最近主动说过：" + "；".join(f"「{item}」" for item in recent[-5:]) + "，不要重复")
        instruction = (
            "现在轮到你主动找用户搭一句话，不是回答某个提问。\n"
            "场景：" + "；".join(part for part in scenario if part) + "。\n"
            "要求：15～40 个汉字，最多两句，口语、软一点，符合你的人设；"
            "不要列清单、不要连珠炮提问。\n"
            "只输出你要说的那一句话，不要加引号和名字前缀。"
        )
        messages = [
            {"role": "system", "content": self._persona_prompt()},
            {"role": "user", "content": instruction},
        ]
        return self._request_chat(messages, options, key)

    # ---------- 主动搭话：上下文与离线模板 ----------
    def _normalize_context(self, context: dict | None) -> dict:
        ctx = dict(context) if isinstance(context, dict) else {}
        time_of_day = str(ctx.get("time_of_day") or "").strip().lower()
        if time_of_day not in PROACTIVE_TEMPLATES:
            hour = time.localtime().tm_hour
            if 5 <= hour < 12:
                time_of_day = "morning"
            elif 12 <= hour < 18:
                time_of_day = "afternoon"
            elif 18 <= hour < 23:
                time_of_day = "evening"
            else:
                time_of_day = "night"
        state = str(ctx.get("dsh_state") or "idle").strip().lower()
        state = DSH_STATE_ALIASES.get(state, state)
        if state not in _DSH_STATE_CN:
            state = "idle"
        return {
            "time_of_day": time_of_day,
            "idle_minutes": _clamp_int(ctx.get("idle_minutes"), 0, 0, 10000),
            "dsh_state": state,
            "last_user_text": str(ctx.get("last_user_text") or "").strip()[:40],
        }

    @staticmethod
    def _proactive_candidates(ctx: dict) -> list[str]:
        candidates: list[str] = []
        state_lines = DSH_STATE_LINES.get(ctx["dsh_state"])
        if state_lines:
            candidates.extend(state_lines)
        if ctx["idle_minutes"] >= 20:
            candidates.extend(text.format(minutes=ctx["idle_minutes"]) for text in PROACTIVE_IDLE)
        candidates.extend(PROACTIVE_TEMPLATES.get(ctx["time_of_day"], ()))
        if ctx["last_user_text"]:
            candidates.extend(PROACTIVE_FOLLOWUP)
        return list(dict.fromkeys(candidates))  # 去重且保持顺序

    def _pick_proactive(self, candidates: list[str], recent: list[str]) -> str:
        fresh = [item for item in candidates if item not in recent]
        pool = fresh or candidates
        return random.choice(pool) if pool else self._proactive_fallback

    # ---------- 文本清洗 ----------
    @staticmethod
    def _clean_line(answer: str) -> str:
        text = str(answer or "").strip()
        text = re.sub(r"^(?:大肥鱼|鲸鱼娘)[：:]\s*", "", text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = lines[0] if lines else ""
        text = text.strip().strip("“”\"「」『』'").strip()
        return re.sub(r"\s+", " ", text)

    def _redact(self, text: str) -> str:
        """日志/错误信息里抹掉 Key（含通用 sk- 形态），绝不外泄。"""
        text = str(text or "")
        key, _source = self._resolve_api_key()
        if key:
            text = text.replace(key, "***")
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", text)
        text = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", text)
        return text

    # ---------- 离线台词 ----------
    def _offline(self, text: str) -> str:
        lowered = (text or "").lower()
        if any(key in text for key in ("个人库", "我的资料", "我的背景", "我是谁", "你记得我", "记得我")):
            personal = getattr(self, "personal", None)
            if personal is not None:
                try:
                    recap = personal.quick_recap()
                except Exception:
                    recap = ""
                if recap:
                    return recap
        if any(key in text for key in ("总览", "最近做过", "最近在做", "我在做什么", "有什么要做",
                                       "待办", "进度", "回顾一下")):
            context = getattr(self, "extra_context", "")
            if context:
                return "我刚看了一圈：" + context[:140]
        for keywords, replies in RULES:
            if any(word.lower() in lowered for word in keywords):
                return random.choice(replies)
        return random.choice(FALLBACKS)
