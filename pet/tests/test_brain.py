# -*- coding: utf-8 -*-
"""大肥鱼大脑的单元测试（标准库 unittest，全程不联网）。

做法：用本地 http.server 起一个假的 OpenAI 兼容接口（127.0.0.1 随机端口），
把 Brain 的 base_url 指过去，覆盖成功、401、429、超时、返回格式坏等路径。

运行（仓库根目录）：
    python -m unittest discover -s pet\\tests -v
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import threading
import time
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

# 超时用例里客户端会先断开，服务端留下的 socket 回收时报 ResourceWarning，与测试结论无关
warnings.filterwarnings("ignore", category=ResourceWarning)

PET_DIR = Path(__file__).resolve().parents[1]
if str(PET_DIR) not in sys.path:
    sys.path.insert(0, str(PET_DIR))

import brain  # noqa: E402  （要在改 sys.path 之后导入）
import memory  # noqa: E402

FAKE_KEY = "test-key-123456"
FAKE_REPLY = "主人好呀，今天也要一起加油哦。"


# --------------------------------------------------------------------------- 假接口
class _FakeHandler(BaseHTTPRequestHandler):
    """一个只会说假话的 /chat/completions 服务端。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 安静，不刷屏
        pass

    def do_POST(self) -> None:  # noqa: N802（http.server 约定）
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            body = {}
        state = self.server.state  # type: ignore[attr-defined]
        with state["lock"]:
            state["requests"].append({
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
            })
            mode = state["script"].pop(0) if state["script"] else state["default"]

        if mode == "sleep":
            time.sleep(state["delay"])
        if mode == "401":
            self._send_json(401, {"error": {"message": "Authentication Fails"}})
            return
        if mode == "429":
            self._send_json(429, {"error": {"message": "Rate limit reached"}})
            return
        if mode == "500":
            self._send_json(500, {"error": {"message": "Internal Server Error"}})
            return
        if mode == "bad_json":
            self._send_raw(200, b"this is not json")
            return
        if mode == "empty_choices":
            self._send_json(200, {"choices": []})
            return

        self._send_json(200, {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": state["model"],
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": state["reply"]},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def _send_json(self, code: int, payload: dict) -> None:
        self._send_raw(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _send_raw(self, code: int, data: bytes) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端超时先跑了，正常现象
        finally:
            self.close_connection = True


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        pass  # 超时测试里客户端先断开，不打印堆栈


class FakeOpenAI:
    """本地假 OpenAI 接口，只监听 127.0.0.1，测试全程不出网。"""

    def __init__(self) -> None:
        self.server = _QuietServer(("127.0.0.1", 0), _FakeHandler)
        self.server.state = {  # type: ignore[attr-defined]
            "reply": FAKE_REPLY,
            "model": "deepseek-chat",
            "default": "ok",
            "script": [],
            "delay": 1.2,
            "requests": [],
            "lock": threading.Lock(),
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "FakeOpenAI":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def state(self) -> dict:
        return self.server.state  # type: ignore[attr-defined]

    @property
    def requests(self) -> list[dict]:
        return self.state["requests"]

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def configure(self, **kwargs) -> None:
        with self.state["lock"]:
            self.state.update(kwargs)

    def clear_requests(self) -> None:
        with self.state["lock"]:
            self.state["requests"].clear()


# --------------------------------------------------------------------------- 测试基类
class BrainTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fake = FakeOpenAI().start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fake.stop()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dfy_brain_test_")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        random.seed(20261025)
        # 保证机器上真的 DFY_API_KEY 不会干扰 Key 优先级测试
        self._saved_env_key = os.environ.pop("DFY_API_KEY", None)
        self.addCleanup(self._restore_env)
        self.fake.configure(reply=FAKE_REPLY, model="deepseek-chat",
                            default="ok", script=[], delay=1.2)
        self.fake.clear_requests()
        self.logs: list[str] = []

    def _restore_env(self) -> None:
        if self._saved_env_key is not None:
            os.environ["DFY_API_KEY"] = self._saved_env_key

    # ---- 构造 helper ----
    def make_config(self, **llm_overrides) -> dict:
        llm = {
            "enabled": True,
            "base_url": self.fake.base_url,
            "api_key": FAKE_KEY,
            "model": "deepseek-chat",
        }
        llm.update(llm_overrides)
        return {"user_name": "", "llm": llm}

    def make_brain(self, **llm_overrides) -> "brain.Brain":
        return brain.Brain(self.dir, self.make_config(**llm_overrides), log=self.logs.append)

    def read_memory_file(self) -> dict:
        return json.loads((self.dir / "memory.json").read_text(encoding="utf-8"))

    @staticmethod
    def all_offline_lines() -> set[str]:
        lines = set(brain.FALLBACKS)
        for _keywords, replies in brain.RULES:
            lines.update(replies)
        return lines


# --------------------------------------------------------------------------- LLM 客户端
class LLMClientTests(BrainTestCase):
    def test_success_path_llm_reply(self) -> None:
        """正常回复：走 /chat/completions，返回 llm，并写入记忆。"""
        b = self.make_brain()
        reply, source = b.reply("你好呀")

        self.assertEqual(source, "llm")
        self.assertEqual(reply, FAKE_REPLY)
        self.assertEqual(len(self.fake.requests), 1)
        request = self.fake.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["auth"], "Bearer " + FAKE_KEY)
        self.assertEqual(request["body"]["model"], "deepseek-chat")
        self.assertFalse(request["body"]["stream"])
        messages = request["body"]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("鲸鱼娘", messages[0]["content"])
        self.assertEqual(messages[-1], {"role": "user", "content": "你好呀"})

        # 记忆落盘：历史和 v2 结构都在
        data = self.read_memory_file()
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["history"][-2]["role"], "user")
        self.assertEqual(data["history"][-2]["content"], "你好呀")
        self.assertEqual(data["history"][-1]["role"], "assistant")

    def test_401_falls_back_to_offline_error_without_leaking_key(self) -> None:
        """401：Key 无效 → offline-error，日志里不能出现 Key。"""
        secret = "sk-super-secret-abcdef123456"
        self.fake.configure(default="401")
        b = self.make_brain(api_key=secret)
        reply, source = b.reply("你好")

        self.assertEqual(source, "offline-error")
        self.assertIn(reply, self.all_offline_lines())
        self.assertTrue(any("401" in line or "Key" in line for line in self.logs),
                        msg=f"日志里应有友好错误说明：{self.logs}")
        for line in self.logs:
            self.assertNotIn(secret, line)
            self.assertNotIn("abcdef123456", line)

    def test_timeout_falls_back_to_offline_error(self) -> None:
        """超时：0.2 秒超时、服务端睡 1.2 秒 → offline-error，且不拖太久。"""
        self.fake.configure(default="sleep", delay=1.2)
        b = self.make_brain(timeout_s=0.2, max_retries=0)
        started = time.time()
        reply, source = b.reply("你好")
        elapsed = time.time() - started

        self.assertEqual(source, "offline-error")
        self.assertIn(reply, self.all_offline_lines())
        self.assertLess(elapsed, 2.5)
        self.assertTrue(any("超时" in line for line in self.logs), msg=str(self.logs))

    def test_429_retry_then_success(self) -> None:
        """429 限流：允许重试一次，第二次成功。"""
        self.fake.configure(script=["429"], default="ok")
        b = self.make_brain(max_retries=1)
        reply, source = b.reply("你好")

        self.assertEqual(source, "llm")
        self.assertEqual(reply, FAKE_REPLY)
        self.assertEqual(len(self.fake.requests), 2)

    def test_429_exhausted_falls_back(self) -> None:
        """429 用尽重试 → offline-error（不抛异常给界面）。"""
        self.fake.configure(default="429")
        b = self.make_brain(max_retries=1)
        _reply, source = b.reply("你好")
        self.assertEqual(source, "offline-error")
        self.assertEqual(len(self.fake.requests), 2)

    def test_bad_response_falls_back(self) -> None:
        """接口返回坏 JSON / 空 choices → offline-error。"""
        self.fake.configure(default="bad_json")
        self.assertEqual(self.make_brain().reply("你好")[1], "offline-error")

        self.fake.configure(default="empty_choices")
        self.assertEqual(self.make_brain().reply("你好")[1], "offline-error")

    def test_key_priority_config_env_file(self) -> None:
        """Key 优先级：config → 环境变量 DFY_API_KEY → pet/.secret_api_key。"""
        secret_file = self.dir / ".secret_api_key"

        # 1) 三处都有 → 用 config
        secret_file.write_text("file-key\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"DFY_API_KEY": "env-key"}):
            b = self.make_brain(api_key="config-key")
            self.assertEqual(b.reply("你好")[1], "llm")
            self.assertEqual(self.fake.requests[-1]["auth"], "Bearer config-key")

            # 2) config 为空 → 用环境变量
            self.fake.clear_requests()
            b = brain.Brain(self.dir, self.make_config(api_key=""), log=self.logs.append)
            self.assertEqual(b.reply("你好")[1], "llm")
            self.assertEqual(self.fake.requests[-1]["auth"], "Bearer env-key")

        # 3) 环境和 config 都没有 → 用文件，且首尾空白要 strip
        self.fake.clear_requests()
        b = brain.Brain(self.dir, self.make_config(api_key=""), log=self.logs.append)
        self.assertEqual(b.reply("你好")[1], "llm")
        self.assertEqual(self.fake.requests[-1]["auth"], "Bearer file-key")

        # 4) 三处都没有 → 离线，不发请求
        secret_file.unlink()
        self.fake.clear_requests()
        b = brain.Brain(self.dir, self.make_config(api_key=""), log=self.logs.append)
        self.assertEqual(b.reply("你好")[1], "offline")
        self.assertEqual(self.fake.requests, [])

    def test_llm_options_defaults_and_string_values(self) -> None:
        """config 里缺字段用默认值；界面存的字符串数字也能解析；非法值回默认。"""
        default_opts = self.make_brain()._llm_options()
        self.assertEqual(default_opts["timeout_s"], 30.0)
        self.assertEqual(default_opts["max_tokens"], 220)
        self.assertEqual(default_opts["temperature"], 0.9)
        self.assertEqual(default_opts["max_retries"], 1)

        b = self.make_brain(timeout_s="5", max_tokens="300",
                            temperature="0.5", max_retries="2")
        opts = b._llm_options()
        self.assertEqual(opts["timeout_s"], 5.0)
        self.assertEqual(opts["max_tokens"], 300)
        self.assertEqual(opts["temperature"], 0.5)
        self.assertEqual(opts["max_retries"], 2)

        bad = self.make_brain(timeout_s="abc", max_tokens=None,
                              temperature="高", max_retries="x")._llm_options()
        self.assertEqual(bad["timeout_s"], 30.0)
        self.assertEqual(bad["max_tokens"], 220)
        self.assertEqual(bad["temperature"], 0.9)
        self.assertEqual(bad["max_retries"], 1)

    def test_test_connection_ok_and_failures(self) -> None:
        """设置窗「测试连接」：成功/401/无 Key 三种人话，都不含 Key。"""
        b = self.make_brain()
        ok, detail = b.test_connection()
        self.assertTrue(ok)
        self.assertIn("连接成功", detail)
        self.assertIn("deepseek-chat", detail)
        self.assertNotIn(FAKE_KEY, detail)

        self.fake.configure(default="401")
        ok, detail = b.test_connection()
        self.assertFalse(ok)
        self.assertIn("Key 无效", detail)
        self.assertNotIn(FAKE_KEY, detail)

        no_key = self.make_brain(api_key="")
        ok, detail = no_key.test_connection()
        self.assertFalse(ok)
        self.assertIn("没找到 API Key", detail)


# --------------------------------------------------------------------------- 记忆
class MemoryTests(BrainTestCase):
    def test_memory_write_reload_and_retrieval(self) -> None:
        """事实/称呼写入后能重载，并能按新输入检索到。"""
        b = self.make_brain(enabled=False)
        b.reply("我叫小明，记住我喜欢蓝色")
        self.assertEqual(b.user_name(), "小明")
        facts = [fact["text"] for fact in b.memory_store.facts]
        self.assertIn("喜欢蓝色", facts)
        self.assertEqual(len([f for f in facts if "蓝色" in f]), 1, "同一事实不能重复存两条")

        # 重新构造 Brain（模拟重启）→ 记忆还在
        b2 = brain.Brain(self.dir, self.make_config(enabled=False), log=self.logs.append)
        self.assertEqual(b2.user_name(), "小明")
        relevant = b2.memory_store.relevant_lines("我最喜欢什么颜色？")
        self.assertTrue(any("蓝色" in line for line in relevant), msg=str(relevant))
        self.assertIn("喜欢蓝色", b2.system_prompt("蓝色"))

        # 疑问句不能被当成新名字/新喜好
        b2.reply("我叫什么名字？")
        self.assertEqual(b2.user_name(), "小明")

    def test_history_limit_60(self) -> None:
        """对话历史只保留最近 60 条。"""
        b = self.make_brain(enabled=False)
        for index in range(35):
            b.reply(f"随便聊第 {index} 句")
        self.assertEqual(len(b.memory_store.history), 60)
        self.assertEqual(len(self.read_memory_file()["history"]), 60)
        self.assertEqual(b.memory_store.history[-2]["content"], "随便聊第 34 句")

    def test_old_memory_json_self_heals(self) -> None:
        """旧格式 memory.json（facts 是字符串、无 version）能读、能升级、不丢数据。"""
        old = {
            "user_name": "老王",
            "facts": ["喜欢白饭", "讨厌香菜"],
            "history": [{"role": "user", "content": "之前聊过火锅", "time": 0}],
        }
        (self.dir / "memory.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

        b = brain.Brain(self.dir, self.make_config(enabled=False), log=self.logs.append)
        self.assertEqual(b.user_name(), "老王")
        self.assertEqual([fact["text"] for fact in b.memory_store.facts], ["喜欢白饭", "讨厌香菜"])
        self.assertIsInstance(b.memory_store.facts[0], dict)
        self.assertIn("2 条事实", b.memory_summary())
        self.assertIn("1 条对话", b.memory_summary())

        upgraded = self.read_memory_file()
        self.assertEqual(upgraded["version"], 2)
        self.assertEqual([fact["text"] for fact in upgraded["facts"]], ["喜欢白饭", "讨厌香菜"])

        # 再读一次也不炸
        b2 = brain.Brain(self.dir, self.make_config(enabled=False), log=self.logs.append)
        self.assertEqual(b2.memory_store.facts[0]["text"], "喜欢白饭")

    def test_corrupt_memory_json_self_heals(self) -> None:
        """memory.json 半截/乱码时不崩，坏文件留档，记忆按空处理。"""
        (self.dir / "memory.json").write_text("{这不是 json", encoding="utf-8")
        b = brain.Brain(self.dir, self.make_config(enabled=False), log=self.logs.append)
        self.assertEqual(b.memory_store.facts, [])
        self.assertTrue((self.dir / "memory.json.corrupt").exists())
        # 还能正常聊天并重新生成合法文件
        self.assertEqual(b.reply("你好")[1], "offline")
        self.assertEqual(self.read_memory_file()["version"], 2)

    def test_app_style_direct_memory_access(self) -> None:
        """兼容 app.py 设置窗直接写 brain.memory["user_name"] 的旧习惯。"""
        b = self.make_brain(enabled=False)
        self.assertIsInstance(b.memory, dict)
        b.memory["user_name"] = "新名字"
        self.assertEqual(b.user_name(), "新名字")
        b.reply("你好")
        b2 = brain.Brain(self.dir, self.make_config(enabled=False), log=self.logs.append)
        self.assertEqual(b2.user_name(), "新名字")

    def test_memory_summary_initial(self) -> None:
        b = self.make_brain(enabled=False)
        summary = b.memory_summary()
        self.assertIn("记忆：", summary)
        self.assertIn("还没有聊天记忆", summary)
        self.assertIn("称呼：未设置", summary)


# --------------------------------------------------------------------------- 离线规则 / prompt
class OfflineAndPromptTests(BrainTestCase):
    def test_offline_rules(self) -> None:
        """没启用模型：命中关键词用对应台词，没命中用兜底台词，不发网络请求。"""
        b = self.make_brain(enabled=False)
        self.assertEqual(b.reply("")[0], "你还没说话呢。")
        self.assertEqual(b.reply("   ")[1], "offline")

        reply, source = b.reply("摸摸头")
        self.assertEqual(source, "offline")
        self.assertIn(reply, brain.RULES[3][1])
        self.assertEqual(self.fake.requests, [])

        fallback = b.reply("zxcvbnm")
        self.assertEqual(fallback[1], "offline")
        self.assertIn(fallback[0], brain.FALLBACKS)

    def test_disabled_model_ignores_stale_key(self) -> None:
        b = self.make_brain(enabled=False)
        reply, source = b.reply("你好")
        self.assertEqual(source, "offline")
        self.assertIn("离线", b.status_text())
        self.assertEqual(self.fake.requests, [])

    def test_system_prompt_contains_memory(self) -> None:
        """system prompt 里要有最近对话 + 相关记忆，且不泄漏 Key。"""
        b = self.make_brain(enabled=False)
        b.reply("我叫小明，记住我喜欢蓝色")
        prompt = b.system_prompt("蓝色")
        self.assertIn("小明", prompt)
        self.assertIn("喜欢蓝色", prompt)
        self.assertIn("最近的对话", prompt)
        self.assertIn("20～60", prompt)
        self.assertNotIn(FAKE_KEY, prompt)

        no_query = b.system_prompt()
        self.assertIn("你记得的事", no_query)

    def test_status_text_variants(self) -> None:
        offline = self.make_brain(enabled=False)
        self.assertIn("离线", offline.status_text())

        online = self.make_brain()
        self.assertIn("在线模型", online.status_text())
        self.assertIn("deepseek-chat", online.status_text())
        self.assertIn("配置文件", online.status_text())
        self.assertNotIn(FAKE_KEY, online.status_text())

        no_key = self.make_brain(api_key="")
        self.assertIn("没找到 API Key", no_key.status_text())


# --------------------------------------------------------------------------- 主动搭话 / DSH
class ProactiveAndDSHTests(BrainTestCase):
    def test_proactive_offline_dedupes_within_20(self) -> None:
        """离线主动搭话：按时段模板，最近 20 条内不重复轰炸。"""
        b = self.make_brain(enabled=False)
        context = {"time_of_day": "morning", "dsh_state": "idle", "idle_minutes": 0}
        lines = [b.proactive_line(context) for _ in range(6)]

        self.assertEqual(len(set(lines)), 6, msg=f"6 次应拿到 6 句不重复：{lines}")
        for line in lines:
            self.assertIn(line, brain.PROACTIVE_TEMPLATES["morning"])
        self.assertEqual(b.memory_store.proactive_recent, lines)

        # 模板用完后仍能返回一句，不崩
        again = b.proactive_line(context)
        self.assertIn(again, brain.PROACTIVE_TEMPLATES["morning"])

    def test_proactive_contexts(self) -> None:
        """久未互动 / 工作状态 / 有上文时都能生成一句人话。"""
        b = self.make_brain(enabled=False)
        idle_line = b.proactive_line({"time_of_day": "night", "idle_minutes": 45})
        self.assertTrue(idle_line)
        self.assertGreaterEqual(len(idle_line), 6)

        running_line = b.proactive_line({"time_of_day": "afternoon", "dsh_state": "running"})
        self.assertTrue(running_line)

        followup = b.proactive_line({"time_of_day": "evening", "last_user_text": "我在改论文"})
        self.assertTrue(followup)

        # 上下文缺字段 / 类型离谱也不崩
        self.assertTrue(b.proactive_line(None))
        self.assertTrue(b.proactive_line({"time_of_day": "深夜", "idle_minutes": "abc"}))

    def test_proactive_with_llm_and_repeat_fallback(self) -> None:
        """有模型时用模型生成；模型又返回重复句时退回离线模板。"""
        model_line = "主人要不要先喝口水，我在这儿陪着你哦。"
        self.fake.configure(reply=model_line)
        b = self.make_brain()
        context = {"time_of_day": "afternoon", "idle_minutes": 30}

        first = b.proactive_line(context)
        self.assertEqual(first, model_line)
        self.assertEqual(len(self.fake.requests), 1)
        request_body = self.fake.requests[0]["body"]
        self.assertIn("15～40", request_body["messages"][-1]["content"])
        self.assertIn("下午", request_body["messages"][-1]["content"])

        second = b.proactive_line(context)
        self.assertNotEqual(second, first, "同一句主动搭话不能在 20 条内重复")
        self.assertTrue(second)

    def test_proactive_llm_failure_uses_offline(self) -> None:
        self.fake.configure(default="401")
        b = self.make_brain()
        line = b.proactive_line({"time_of_day": "morning", "idle_minutes": 30})
        self.assertTrue(line)
        self.assertTrue(any("主动搭话" in item for item in self.logs), msg=str(self.logs))

    def test_line_for_dsh_state(self) -> None:
        """running/success/failure/approval 给候选台词，idle/未知返回 None。"""
        b = self.make_brain(enabled=False)
        for state in ("running", "success", "failure", "approval"):
            hits = {b.line_for_dsh_state(state) for _ in range(40)}
            self.assertTrue(hits, f"{state} 不应为空")
            self.assertTrue(hits.issubset(set(brain.DSH_STATE_LINES[state])),
                            msg=f"{state}: {hits}")

        self.assertIsNone(b.line_for_dsh_state("idle"))
        self.assertIsNone(b.line_for_dsh_state("bogus"))
        self.assertIsNone(b.line_for_dsh_state(""))
        self.assertIn(b.line_for_dsh_state("tool"), brain.DSH_STATE_LINES["running"])


# --------------------------------------------------------------------------- 接口签名
class InterfaceTests(BrainTestCase):
    def test_required_interface(self) -> None:
        """Lead 的 app.py 依赖的接口签名都在。"""
        b = brain.Brain(self.dir, self.make_config(enabled=False), log=self.logs.append)
        self.assertTrue(callable(b.reply))
        self.assertIsInstance(b.status_text(), str)
        self.assertIsInstance(b.user_name(), str)
        self.assertIsInstance(b.system_prompt(), str)

        ok, detail = b.test_connection()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(detail, str)

        self.assertIsInstance(b.proactive_line(), str)
        self.assertIsInstance(b.memory_summary(), str)
        self.assertIsNone(b.line_for_dsh_state("idle"))
        self.assertIsInstance(b.line_for_dsh_state("running"), str)

        reply, source = b.reply("你好")
        self.assertIsInstance(reply, str)
        self.assertIn(source, ("llm", "offline", "offline-error"))

        # 记忆模块独立可用（旧代码/以后设置窗可能直接用它）
        self.assertTrue(hasattr(memory, "MemoryStore"))
        self.assertIsInstance(b.memory_store, memory.MemoryStore)


if __name__ == "__main__":
    unittest.main(verbosity=2)
