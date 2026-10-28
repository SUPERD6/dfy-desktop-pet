# -*- coding: utf-8 -*-
"""大肥鱼 Agent 层的单元测试（标准库 unittest，全程不联网）。

做法：用本地 ``http.server`` 起一个假的 OpenAI / DeepSeek 兼容接口
（``127.0.0.1`` 随机端口），按脚本返回带 ``tool_calls`` 的响应或错误，
把 AgentRunner 的 ``base_url`` 指过去；所有文件操作都在
``tempfile.TemporaryDirectory`` 里，不碰真实个人库/工作区。

运行（仓库根目录）：
    python -m unittest pet.tests.test_agent -v
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 超时用例里客户端会先断开，服务端留下的 socket 回收时报 ResourceWarning，与结论无关
warnings.filterwarnings("ignore", category=ResourceWarning)

PET_DIR = Path(__file__).resolve().parents[1]
if str(PET_DIR) not in sys.path:
    sys.path.insert(0, str(PET_DIR))

import agent  # noqa: E402  （要在改 sys.path 之后导入）

FAKE_KEY = "test-key-123456"
FINAL_REPLY = "主人好呀，我查完啦。"
SECRET_KEY = "sk-super-secret-abcdef123456"


# --------------------------------------------------------------------------- 假接口
def say(text: str = FINAL_REPLY) -> dict:
    """脚本项：普通最终回复。"""
    return {"message": {"role": "assistant", "content": text}}


def call(name: str, args: dict | None = None, call_id: str = "call_1") -> dict:
    """脚本项：一次工具调用。"""
    return {"message": _assistant_with_tool_calls([(name, args or {}, call_id)])}


def calls(*specs) -> dict:
    """脚本项：一轮里多个工具调用，specs 是 (name, args[, call_id])。"""
    normalized = []
    for index, spec in enumerate(specs, 1):
        name, args = spec[0], spec[1] if len(spec) > 1 else {}
        call_id = spec[2] if len(spec) > 2 else f"call_{index}"
        normalized.append((name, args, call_id))
    return {"message": _assistant_with_tool_calls(normalized)}


def _assistant_with_tool_calls(items) -> dict:
    tool_calls = []
    for name, args, call_id in items:
        tool_calls.append({
            "id": call_id,
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args, ensure_ascii=False)},
        })
    return {"role": "assistant", "content": None, "tool_calls": tool_calls}


def api_error(code: int = 401) -> dict:
    """脚本项：接口返回错误状态码。"""
    return {"__status__": code, "body": {"error": {"message": "fake error"}}}


def slow(seconds: float) -> dict:
    """脚本项：服务端先睡一会儿（配合客户端超时）。"""
    return {"__sleep__": seconds}


def raw_body(data: bytes) -> dict:
    """脚本项：返回不是 JSON 的响应体。"""
    return {"__raw__": data}


class _FakeHandler(BaseHTTPRequestHandler):
    """一个只会说假话的 /chat/completions 服务端。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 安静，不刷屏
        pass

    def do_POST(self) -> None:  # noqa: N802（http.server 约定）
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        state = self.server.state  # type: ignore[attr-defined]
        with state["lock"]:
            state["requests"].append({
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
            })
            item = state["script"].pop(0) if state["script"] else state["default"]
        if not isinstance(item, dict):
            item = {"message": {"role": "assistant", "content": FINAL_REPLY}}

        if "__sleep__" in item:
            time.sleep(float(item["__sleep__"]))
        if "__raw__" in item:
            self._send_raw(200, item["__raw__"])
            return
        if "__status__" in item:
            self._send_json(int(item["__status__"]),
                            item.get("body") or {"error": {"message": "fake error"}})
            return

        message = item.get("message") or {"role": "assistant", "content": FINAL_REPLY}
        self._send_json(200, {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": state["model"],
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
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


class FakeDeepSeek:
    """本地假 DeepSeek 接口，只监听 127.0.0.1，测试全程不出网。"""

    def __init__(self) -> None:
        self.server = _QuietServer(("127.0.0.1", 0), _FakeHandler)
        self.server.state = {  # type: ignore[attr-defined]
            "model": "deepseek-chat",
            "script": [],
            "default": {"message": {"role": "assistant", "content": FINAL_REPLY}},
            "requests": [],
            "lock": threading.Lock(),
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "FakeDeepSeek":
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

    def request_json(self) -> str:
        """把收到的请求整体序列化，方便断言「任何地方都不许出现 Key」。"""
        return json.dumps(self.requests, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- 假依赖对象
class FakeOverview:
    def __init__(self, summary: str = "最近在忙大肥鱼桌宠：加 Agent 层和素材。",
                 exc: Exception | None = None) -> None:
        self.summary = summary
        self.exc = exc
        self.calls = 0

    def text_summary(self) -> str:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.summary


class FakeOverviewMaxChars:
    """另一种签名：text_summary(max_chars)。"""

    def text_summary(self, max_chars: int = 500) -> str:
        return f"总览（截到 {max_chars} 字）：在写测试。"


class FakePersonal:
    def __init__(self,
                 block: str = "用户是 小明，计算机硕士在读，方向可靠 LLM Agent。",
                 items=None,
                 exc: Exception | None = None) -> None:
        self.block = block
        self.items = items if items is not None else [
            {"text": "喜欢用简单中文回答"},
            "空闲时会做桌宠项目",
            {"topic": "preference", "text": "偏好小步提交"},
        ]
        self.exc = exc
        self.queries: list[str] = []
        self.find_calls = 0

    def prompt_block(self, query: str = "") -> str:
        self.queries.append(query)
        return self.block

    def find(self, query: str = "", limit: int = 5):
        self.find_calls += 1
        if self.exc is not None:
            raise self.exc
        return list(self.items)[:limit]


class FakePersonalNoArgBlock:
    """兼容 prompt_block() 无参实现。"""

    def prompt_block(self) -> str:
        return "无参签名也能用。"


class LegacyPersonalFind:
    """兼容 find(query) 单参实现。"""

    def find(self, query: str = ""):  # noqa: D401（故意少一个参数）
        return ["单参 find 的条目"]


# --------------------------------------------------------------------------- 测试基类
class AgentTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fake = FakeDeepSeek().start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fake.stop()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dfy_agent_test_")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.logs: list[str] = []
        # 保证机器上真实的 DFY_API_KEY 不会干扰 Key 优先级测试
        self._saved_env_key = os.environ.pop("DFY_API_KEY", None)
        self.addCleanup(self._restore_env)
        self.fake.configure(script=[], default=say())
        self.fake.clear_requests()
        self.confirm_calls: list[tuple] = []
        self.steps_seen: list[dict] = []

    def _restore_env(self) -> None:
        os.environ.pop("DFY_API_KEY", None)
        if self._saved_env_key is not None:
            os.environ["DFY_API_KEY"] = self._saved_env_key

    # ---- 构造 helper ----
    def make_runner(self, *, confirm=None, personal=None, overview=None, status_path=None,
                    allowed_roots=None, agent_cfg: dict | None = None,
                    llm: dict | None = None) -> "agent.AgentRunner":
        roots = [str(self.dir)] if allowed_roots is None else [str(p) for p in allowed_roots]
        config = {
            "user_name": "主人",
            "llm": {
                "enabled": True,
                "base_url": self.fake.base_url,
                "api_key": FAKE_KEY,
                "model": "deepseek-chat",
                "timeout_s": 5,
            },
            "agent": {"allowed_roots": roots},
        }
        if agent_cfg:
            config["agent"].update(agent_cfg)
        if llm:
            config["llm"].update(llm)
        return agent.AgentRunner(
            self.dir, config, personal=personal, overview=overview,
            status_path=status_path, log=self.logs.append, confirm=confirm,
        )

    def saying_yes(self, **_kwargs):
        def confirm(tool_name, args, preview):
            self.confirm_calls.append((tool_name, dict(args), preview))
            return True
        return confirm

    def saying_no(self, **_kwargs):
        def confirm(tool_name, args, preview):
            self.confirm_calls.append((tool_name, dict(args), preview))
            return False
        return confirm

    def script(self, *items: dict) -> None:
        self.fake.configure(script=list(items))

    def record_step(self, step: dict) -> None:
        self.steps_seen.append(dict(step))

    def role_sequence(self, request_index: int) -> list[str]:
        return [m.get("role") for m in self.fake.requests[request_index]["body"]["messages"]]

    def tool_messages(self, request_index: int) -> list[dict]:
        return [m for m in self.fake.requests[request_index]["body"]["messages"]
                if m.get("role") == "tool"]

    def write(self, relative: str, text: str) -> Path:
        path = self.dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


# --------------------------------------------------------------------------- 主循环
class RunLoopTests(AgentTestCase):
    def test_two_round_tool_call_then_reply(self) -> None:
        """第一轮 tool_calls、第二轮最终回复：顺序、steps、reply 都对。"""
        overview = FakeOverview()
        personal = FakePersonal()
        self.script(call("get_work_overview"), say())
        runner = self.make_runner(overview=overview, personal=personal)

        result = runner.run("帮我看看最近在忙什么", on_step=self.record_step)

        self.assertEqual(result["source"], "agent")
        self.assertIsNone(result["error"])
        self.assertEqual(result["reply"], FINAL_REPLY)
        self.assertEqual(len(result["steps"]), 1)
        step = result["steps"][0]
        self.assertEqual(set(step), {"tool", "ok", "detail"})
        self.assertEqual(step["tool"], "get_work_overview")
        self.assertTrue(step["ok"])
        self.assertIn("工作总览", step["detail"])
        self.assertIn("最近在忙大肥鱼桌宠", step["detail"])
        self.assertEqual(self.steps_seen, result["steps"], msg="on_step 应收到每一步")
        self.assertEqual(overview.calls, 1)
        self.assertEqual(len(self.fake.requests), 2)

        # 第一次请求：带 tools 定义 + 人设 + 个人库摘要
        first = self.fake.requests[0]
        body = first["body"]
        self.assertEqual(first["path"], "/v1/chat/completions")
        self.assertEqual(first["auth"], "Bearer " + FAKE_KEY)
        self.assertFalse(body["stream"])
        self.assertEqual(body["tool_choice"], "auto")
        tool_names = [item["function"]["name"] for item in body["tools"]]
        self.assertEqual(tuple(tool_names), agent.TOOL_NAMES)
        system_prompt = body["messages"][0]["content"]
        self.assertIn("鲸鱼娘", system_prompt)          # 来自 persona.md
        self.assertIn("get_work_overview", system_prompt)
        self.assertIn("不要为了用工具而用工具", system_prompt)
        self.assertIn("不要做未确认的危险操作；工具失败要如实说明", system_prompt)
        self.assertIn("主人没同意就停下来说明", system_prompt)
        self.assertIn("小明", system_prompt)      # 个人库摘要注入
        self.assertEqual(body["messages"][-1], {"role": "user", "content": "帮我看看最近在忙什么"})
        self.assertEqual(personal.queries, ["帮我看看最近在忙什么"],
                         msg="prompt_block 应带着当前问题调用")

        # 第二次请求：带 assistant(tool_calls) + tool 结果
        self.assertEqual(self.role_sequence(1), ["system", "user", "assistant", "tool"])
        messages = self.fake.requests[1]["body"]["messages"]
        assistant_msg = messages[2]
        self.assertEqual(assistant_msg["tool_calls"][0]["function"]["name"], "get_work_overview")
        tool_message = messages[3]
        self.assertEqual(tool_message["tool_call_id"], "call_1")
        self.assertIn("最近在忙大肥鱼桌宠", tool_message["content"])

    def test_answer_without_tool(self) -> None:
        """模型直接回答：不调工具，source=agent，steps 为空。"""
        self.script(say("在的在的，今天想做什么呀？"))
        runner = self.make_runner()

        result = runner.run("你好")

        self.assertEqual(result["source"], "agent")
        self.assertEqual(result["reply"], "在的在的，今天想做什么呀？")
        self.assertEqual(result["steps"], [])
        self.assertEqual(len(self.fake.requests), 1)

    def test_multiple_tool_calls_in_one_round(self) -> None:
        """一轮里多个 tool_calls：都要执行，且按返回顺序进 steps。"""
        self.write("docs/note.md", "这里有一根 needle")
        self.script(
            calls(("list_dir", {}), ("read_file", {"path": "docs/note.md"})),
            say(),
        )
        runner = self.make_runner()

        result = runner.run("看看 docs 里有什么", on_step=self.record_step)

        self.assertEqual([step["tool"] for step in result["steps"]],
                         ["list_dir", "read_file"])
        self.assertTrue(all(step["ok"] for step in result["steps"]))
        self.assertEqual(len(self.steps_seen), 2)
        tool_msgs = self.tool_messages(1)
        self.assertEqual([m["tool_call_id"] for m in tool_msgs], ["call_1", "call_2"])
        self.assertIn("needle", tool_msgs[1]["content"])

    def test_max_steps_stops_at_six(self) -> None:
        """模型一直要工具：第 6 步停下，给可读失败说明。"""
        self.fake.configure(default=call("get_work_overview"))
        runner = self.make_runner(overview=FakeOverview())

        result = runner.run("一直查")

        self.assertEqual(len(result["steps"]), agent.MAX_STEPS)
        self.assertEqual(result["source"], "error")
        self.assertIsNotNone(result["error"])
        self.assertIn(str(agent.MAX_STEPS), result["error"])
        self.assertTrue(result["reply"].strip(), msg="失败也要有给人看的一句话")
        self.assertEqual(len(self.fake.requests), agent.MAX_STEPS)

    def test_empty_user_text_no_request(self) -> None:
        """空输入：直接返回 error，不发请求。"""
        runner = self.make_runner()
        result = runner.run("   ")
        self.assertEqual(result["source"], "error")
        self.assertEqual(result["steps"], [])
        self.assertEqual(len(self.fake.requests), 0)

    def test_on_step_callback_error_does_not_break_run(self) -> None:
        """UI 的 on_step 回调抛异常也不能打断 agent。"""
        self.script(call("get_work_overview"), say())
        runner = self.make_runner(overview=FakeOverview())

        def boom(_step):
            raise RuntimeError("UI 炸了")

        result = runner.run("查一下", on_step=boom)

        self.assertEqual(result["source"], "agent")
        self.assertEqual(len(result["steps"]), 1)
        self.assertTrue(any("on_step" in line for line in self.logs), msg=str(self.logs))


# --------------------------------------------------------------------------- 确认流程
class ConfirmTests(AgentTestCase):
    def test_write_file_after_confirm_true(self) -> None:
        """confirm 点头才写文件；args/preview 都给 confirm 看得到。"""
        runner = self.make_runner(confirm=self.saying_yes())

        result = runner.execute_tool(
            "write_file", {"path": "记录/note.md", "content": "# 记录\n你好"}
        )

        self.assertTrue(result["ok"], msg=str(result))
        target = self.dir / "记录" / "note.md"
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "# 记录\n你好")
        self.assertEqual(len(self.confirm_calls), 1)
        tool_name, args, preview = self.confirm_calls[0]
        self.assertEqual(tool_name, "write_file")
        self.assertEqual(args["path"], "记录/note.md")
        self.assertIn("准备新建文件", preview)
        self.assertIn("你好", preview)  # 完整内容，不截断（agent-8）
        self.assertIn("note.md", preview)

    def test_write_file_confirm_none_rejected_end_to_end(self) -> None:
        """没有确认弹窗时拒绝写文件，模型收到拒绝结果并继续回答。"""
        self.script(call("write_file", {"path": "x.md", "content": "hi"}), say())
        runner = self.make_runner(confirm=None)

        result = runner.run("帮我写个 x.md")

        self.assertEqual(result["source"], "agent")
        self.assertFalse(result["steps"][0]["ok"])
        self.assertIn("拒绝", result["steps"][0]["detail"])
        self.assertFalse((self.dir / "x.md").exists())
        tool_msgs = self.tool_messages(1)
        self.assertEqual(len(tool_msgs), 1)
        payload = json.loads(tool_msgs[0]["content"])
        self.assertFalse(payload["ok"])
        self.assertTrue(payload.get("rejected"))

    def test_run_powershell_confirm_false_does_not_execute(self) -> None:
        """confirm 拒绝时命令绝不执行，模型收到拒绝结果。"""
        marker = self.dir / "marker.txt"
        command = f"Set-Content -LiteralPath '{marker}' -Value done"
        self.script(call("run_powershell", {"command": command}), say())
        runner = self.make_runner(confirm=self.saying_no())

        result = runner.run("帮我生成一个标记文件")

        self.assertEqual(result["source"], "agent")
        self.assertFalse(result["steps"][0]["ok"])
        self.assertFalse(marker.exists(), msg="confirm 拒绝后命令不能被执行")
        self.assertEqual(self.confirm_calls[0][0], "run_powershell")
        tool_msgs = self.tool_messages(1)
        self.assertIn("rejected", tool_msgs[0]["content"])

    def test_dangerous_command_rejected_without_confirm(self) -> None:
        """明显破坏性命令直接拒，不打扰主人确认。"""
        runner = self.make_runner(confirm=self.saying_yes())
        for command in (
            "Remove-Item -Recurse -Force .",
            "format c: /q",
            "shutdown /s /t 0",
            "del /s /q C:\\Windows",
            "reg delete HKLM\\Software\\Foo /f",
            "bcdedit /set {default} safeboot minimal",
        ):
            result = runner.execute_tool("run_powershell", {"command": command})
            self.assertFalse(result["ok"], msg=command)
            self.assertIn("破坏性", result["error"], msg=command)
        self.assertEqual(self.confirm_calls, [], msg="破坏性命令不该弹确认")

    def test_sensitive_command_rejected_without_confirm(self) -> None:
        """命令里提到敏感文件（Key/.env/id_rsa/*.pem）直接拒，不弹确认。"""
        runner = self.make_runner(confirm=self.saying_yes())
        for command in (
            "Get-Content pet/.secret_api_key",
            "Get-Content .env",
            'Get-Content ".env.local"',
            "cat id_rsa",
            "Get-Content server.pem",
            "Get-Content api.key",
            "Get-Content credentials.json",
        ):
            result = runner.execute_tool("run_powershell", {"command": command})
            self.assertFalse(result["ok"], msg=command)
            self.assertIn("敏感", result["error"], msg=command)
        self.assertEqual(self.confirm_calls, [], msg="敏感命令不该弹确认")

        # 正常命令不能误伤：这里 confirm 返回 False，应走到「主人没确认」而不是「敏感」
        safe = self.make_runner(confirm=self.saying_no())
        rejected = safe.execute_tool("run_powershell", {"command": 'Write-Output "hello"'})
        self.assertTrue(rejected.get("rejected"), msg=str(rejected))
        self.assertIn("确认", rejected["error"])

    def test_confirm_exception_treated_as_reject(self) -> None:
        def broken(_tool_name, _args, _preview):
            raise RuntimeError("弹窗线程挂了")

        runner = self.make_runner(confirm=broken)
        result = runner.execute_tool("write_file", {"path": "a.md", "content": "hi"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["rejected"])
        self.assertFalse((self.dir / "a.md").exists())
        self.assertTrue(any("确认回调出错" in line for line in self.logs), msg=str(self.logs))

    @unittest.skipUnless(shutil.which("powershell") or shutil.which("pwsh"),
                         "本机没有 PowerShell")
    def test_shell_timeout_kills_process(self) -> None:
        """命令超时：杀掉进程、返回 timeout，不卡死 run()。"""
        runner = self.make_runner(confirm=self.saying_yes())
        started = time.time()
        result = runner.execute_tool(
            "run_powershell", {"command": "Start-Sleep -Seconds 20", "timeout_s": 1}
        )
        elapsed = time.time() - started

        self.assertFalse(result["ok"])
        self.assertTrue(result.get("timeout"))
        self.assertIn("秒", result["error"])
        self.assertLess(elapsed, 15)

    @unittest.skipUnless(shutil.which("powershell") or shutil.which("pwsh"),
                         "本机没有 PowerShell")
    def test_shell_success_output(self) -> None:
        """确认后能真的执行无害命令，工作目录在项目根（这里临时目录）。"""
        runner = self.make_runner(confirm=self.saying_yes())
        result = runner.execute_tool("run_powershell", {"command": "Write-Output dfy-ok"})

        self.assertTrue(result["ok"], msg=str(result))
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("dfy-ok", result["stdout"])
        self.assertEqual(Path(result["cwd"]), self.dir)


# --------------------------------------------------------------------------- 路径安全
class PathSafetyTests(AgentTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("docs/note.md", "文档内容 needle")
        self.write("pet/.secret_api_key", "sk-file-secret-abcdef")
        self.write("pet/config.json", '{"llm": {"api_key": "sk-in-config"}}')
        (self.dir / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        self.write(".env", "DFY_API_KEY=sk-env-secret")
        self.write("private.key", "-----BEGIN PRIVATE KEY-----")

    def test_read_file_basic_and_truncated(self) -> None:
        big = self.write("big.txt", "x" * 1000)
        runner = self.make_runner()

        ok_result = runner.execute_tool("read_file", {"path": "docs/note.md"})
        self.assertTrue(ok_result["ok"], msg=str(ok_result))
        self.assertIn("needle", ok_result["content"])
        self.assertFalse(ok_result["truncated"])

        truncated = runner.execute_tool("read_file", {"path": "big.txt", "max_bytes": 100})
        self.assertTrue(truncated["ok"], msg=str(truncated))
        self.assertTrue(truncated["truncated"])
        self.assertIn("已截断", truncated["content"])
        self.assertEqual(truncated["bytes_read"], 100)
        self.assertEqual(truncated["size"], big.stat().st_size)

    def test_path_traversal_rejected(self) -> None:
        runner = self.make_runner()
        for bad in ("../outside.txt", "docs/../../etc/passwd", "..\\..\\windows\\win.ini"):
            result = runner.execute_tool("read_file", {"path": bad})
            self.assertFalse(result["ok"], msg=bad)
            self.assertIn("..", result["error"], msg=bad)

    def test_secret_files_rejected(self) -> None:
        runner = self.make_runner(confirm=self.saying_yes())
        for path in ("pet/.secret_api_key", ".env", "private.key", "pet/credentials"):
            result = runner.execute_tool("read_file", {"path": path})
            self.assertFalse(result["ok"], msg=path)
            self.assertIn("敏感", result["error"], msg=path)
        # 写入/列目录同样不能碰敏感文件
        write_result = runner.execute_tool(
            "write_file", {"path": "pet/.secret_api_key", "content": "x"}
        )
        self.assertFalse(write_result["ok"])
        self.assertIn("敏感", write_result["error"])
        self.assertEqual(self.confirm_calls, [], msg="敏感路径在确认之前就该被拒")
        listing = runner.execute_tool("list_dir", {"path": "pet"})
        names = [item["name"] for item in listing["entries"]]
        self.assertNotIn(".secret_api_key", names)
        self.assertIn("config.json", names)

    def test_outside_allowed_root_rejected(self) -> None:
        other = tempfile.TemporaryDirectory(prefix="dfy_agent_outside_")
        self.addCleanup(other.cleanup)
        outside = Path(other.name)
        (outside / "secret.txt").write_text("外面", encoding="utf-8")
        runner = self.make_runner()
        for tool_name, args in (
            ("read_file", {"path": str(outside / "secret.txt")}),
            ("list_dir", {"path": str(outside)}),
            ("search_files", {"query": "外面", "root": str(outside)}),
            ("write_file", {"path": str(outside / "new.md"), "content": "x"}),
        ):
            result = runner.execute_tool(tool_name, args)
            self.assertFalse(result["ok"], msg=tool_name)
            self.assertIn("允许的目录", result["error"], msg=tool_name)

    def test_binary_files_rejected(self) -> None:
        runner = self.make_runner()
        png = runner.execute_tool("read_file", {"path": "pic.png"})
        self.assertFalse(png["ok"])
        self.assertIn("二进制", png["error"])

        self.write("blob.txt", "abc")
        (self.dir / "blob.txt").write_bytes(b"abc\x00def\x00")
        nul = runner.execute_tool("read_file", {"path": "blob.txt"})
        self.assertFalse(nul["ok"])
        self.assertIn("二进制", nul["error"])

    def test_too_large_read_rejected(self) -> None:
        huge = self.dir / "huge.txt"
        huge.write_bytes(b"a" * (agent.MAX_READ_BYTES_HARD + 1))
        runner = self.make_runner()
        result = runner.execute_tool("read_file", {"path": "huge.txt"})
        self.assertFalse(result["ok"])
        self.assertIn("太大", result["error"])

    def test_write_file_limits(self) -> None:
        runner = self.make_runner(confirm=self.saying_yes())
        bad_ext = runner.execute_tool("write_file", {"path": "run.exe", "content": "x"})
        self.assertFalse(bad_ext["ok"])
        self.assertIn("只允许写文本文件", bad_ext["error"])

        too_big = runner.execute_tool(
            "write_file", {"path": "big.md", "content": "x" * (agent.MAX_WRITE_BYTES + 1)}
        )
        self.assertFalse(too_big["ok"])
        self.assertIn("内容太大", too_big["error"])

        not_str = runner.execute_tool("write_file", {"path": "a.md", "content": 123})
        self.assertFalse(not_str["ok"])
        self.assertEqual(self.confirm_calls, [], msg="校验不过不该弹确认窗")
        self.assertFalse((self.dir / "run.exe").exists())
        self.assertFalse((self.dir / "big.md").exists())

    def test_list_dir_and_search_files(self) -> None:
        self.write("search/needle_name.md", "没有关键词")
        self.write("search/content.md", "这一行有 needle 关键词")
        runner = self.make_runner()

        listing = runner.execute_tool("list_dir", {})
        self.assertTrue(listing["ok"], msg=str(listing))
        names = [item["name"] for item in listing["entries"]]
        self.assertIn("docs", names)
        self.assertNotIn(".secret_api_key", names)

        found = runner.execute_tool("search_files", {"query": "needle", "root": "search"})
        self.assertTrue(found["ok"], msg=str(found))
        self.assertEqual(found["count"], 2, msg=str(found))
        self.assertTrue(any(item["line"] == 0 for item in found["matches"]),
                        msg="文件名匹配应有 line=0")
        self.assertTrue(any(item["line"] > 0 for item in found["matches"]),
                        msg="内容匹配应有行号")

        limited = runner.execute_tool(
            "search_files", {"query": "needle", "root": "search", "max_results": 1}
        )
        self.assertEqual(limited["count"], 1)
        self.assertTrue(limited["truncated"])

        empty = runner.execute_tool("search_files", {"query": "  "})
        self.assertFalse(empty["ok"])
        secret_hit = runner.execute_tool("search_files", {"query": "sk-file-secret"})
        self.assertEqual(secret_hit["count"], 0, msg="搜索不能碰到敏感文件")

    def test_default_allowed_roots_is_project_only(self) -> None:
        """不覆盖 allowed_roots 时，默认白名单只有项目根；个人库要在配置里显式开放。"""
        config = {
            "llm": {"base_url": self.fake.base_url, "api_key": FAKE_KEY},
            "agent": {},
        }
        runner = agent.AgentRunner(self.dir, config, log=self.logs.append)
        roots = [str(root).replace("\\", "/").lower() for root in runner.allowed_roots]
        self.assertTrue(any("大肥鱼" in root or "deepseek" in root for root in roots),
                        msg=str(roots))
        self.assertLessEqual(len(roots), 2, msg=str(roots))  # 项目根 + Agent 自己的工作目录
        self.assertFalse(any("personal_config" in root for root in roots), msg=str(roots))


# --------------------------------------------------------------------------- 只读工具适配
class ReadonlyToolTests(AgentTestCase):
    def test_work_overview_adapter(self) -> None:
        runner = self.make_runner(overview=None)
        missing = runner.execute_tool("get_work_overview", {})
        self.assertFalse(missing["ok"])
        self.assertIn("暂不可用", missing["error"])

        overview = FakeOverview()
        result = self.make_runner(overview=overview).execute_tool("get_work_overview", {})
        self.assertTrue(result["ok"], msg=str(result))
        self.assertIn("最近在忙大肥鱼桌宠", result["summary"])
        self.assertEqual(overview.calls, 1)

        alt = self.make_runner(overview=FakeOverviewMaxChars()).execute_tool(
            "get_work_overview", {})
        self.assertTrue(alt["ok"], msg=str(alt))

        boom = self.make_runner(overview=FakeOverview(exc=RuntimeError("总览崩了"))).execute_tool(
            "get_work_overview", {})
        self.assertFalse(boom["ok"])
        self.assertIn("工作总览读取失败", boom["error"])

    def test_personal_adapter(self) -> None:
        missing = self.make_runner(personal=None).execute_tool("read_personal_library", {})
        self.assertFalse(missing["ok"])
        self.assertIn("暂不可用", missing["error"])

        personal = FakePersonal()
        result = self.make_runner(personal=personal).execute_tool(
            "read_personal_library", {"query": "偏好", "limit": 2})
        self.assertTrue(result["ok"], msg=str(result))
        self.assertEqual(result["count"], 2)
        self.assertEqual(personal.find_calls, 1)

        legacy = self.make_runner(personal=LegacyPersonalFind()).execute_tool(
            "read_personal_library", {"query": "x"})
        self.assertTrue(legacy["ok"], msg=str(legacy))
        self.assertIn("单参 find", legacy["items"][0])

        boom = self.make_runner(personal=FakePersonal(exc=RuntimeError("库崩了"))).execute_tool(
            "read_personal_library", {})
        self.assertFalse(boom["ok"])
        self.assertIn("个人库检索失败", boom["error"])

    def test_prompt_block_legacy_no_arg_signature(self) -> None:
        runner = self.make_runner(personal=FakePersonalNoArgBlock())
        prompt = runner.system_prompt("你好")
        self.assertIn("无参签名也能用", prompt)

    def test_dsh_status_adapter(self) -> None:
        status_path = self.dir / "status.json"
        status_path.write_text(json.dumps({
            "ok": True, "state": "running", "lastTool": "pwsh",
            "doneCount": 2, "errorCount": 1, "approvalOpen": True,
            "updatedAt": int(time.time() * 1000), "version": 1,
        }), encoding="utf-8")
        result = self.make_runner(status_path=status_path).execute_tool("get_dsh_status", {})
        self.assertTrue(result["ok"], msg=str(result))
        self.assertEqual(result["state"], "running")
        self.assertEqual(result["lastTool"], "pwsh")
        self.assertEqual(result["doneCount"], 2)
        self.assertTrue(result["approvalOpen"])
        self.assertTrue(result["connected"])

        # 心跳过期 → connected False 并有说明
        status_path.write_text(json.dumps({
            "state": "idle", "updatedAt": int((time.time() - 60) * 1000),
        }), encoding="utf-8")
        stale = self.make_runner(status_path=status_path).execute_tool("get_dsh_status", {})
        self.assertTrue(stale["ok"], msg=str(stale))
        self.assertFalse(stale["connected"])
        self.assertIn("断开", stale["note"])

        bad = self.make_runner(status_path=self.dir / "nope.json").execute_tool(
            "get_dsh_status", {})
        self.assertFalse(bad["ok"])
        self.assertIn("还没找到 DSH 状态文件", bad["error"])

        broken_path = self.dir / "broken.json"
        broken_path.write_text("{not json", encoding="utf-8")
        broken = self.make_runner(status_path=broken_path).execute_tool("get_dsh_status", {})
        self.assertFalse(broken["ok"])
        self.assertIn("内容坏了", broken["error"])

    def test_run_works_when_personal_and_overview_missing(self) -> None:
        """两个模块都没接上时，run() 仍能正常走完一轮并回话。"""
        self.script(
            calls(("get_work_overview", {}), ("read_personal_library", {})),
            say("这两个模块还没接上，我先记着。"),
        )
        runner = self.make_runner(personal=None, overview=None)
        result = runner.run("最近在忙啥")
        self.assertEqual(result["source"], "agent")
        self.assertEqual(result["reply"], "这两个模块还没接上，我先记着。")
        self.assertEqual([step["ok"] for step in result["steps"]], [False, False])


# --------------------------------------------------------------------------- 接口失败
class ApiErrorTests(AgentTestCase):
    def test_401_returns_friendly_error_without_key(self) -> None:
        self.fake.configure(default=api_error(401))
        runner = self.make_runner(llm={"api_key": SECRET_KEY})

        result = runner.run("你好")

        self.assertEqual(result["source"], "error")
        self.assertIn("401", result["error"])
        self.assertTrue(result["reply"].strip())
        self.assertEqual(len(self.fake.requests), 1)
        self.assertEqual(self.fake.requests[0]["auth"], "Bearer " + SECRET_KEY)
        blob = json.dumps(result, ensure_ascii=False) + "\n".join(self.logs)
        self.assertNotIn(SECRET_KEY, blob)
        self.assertNotIn("abcdef123456", blob)
        self.assertTrue(any("Agent 调用失败" in line for line in self.logs), msg=str(self.logs))

    def test_timeout_returns_error(self) -> None:
        self.script(slow(1.2), say())
        runner = self.make_runner(llm={"timeout_s": 0.2})
        started = time.time()
        result = runner.run("你好")
        elapsed = time.time() - started

        self.assertEqual(result["source"], "error")
        self.assertIn("超时", result["error"])
        self.assertLess(elapsed, 3.0)

    def test_bad_json_response(self) -> None:
        self.script(raw_body(b"this is not json"))
        result = self.make_runner().run("你好")
        self.assertEqual(result["source"], "error")
        self.assertIn("无法解析", result["error"])

    def test_missing_choices_response(self) -> None:
        # 合法 JSON 但没有 choices：要当接口格式错误处理
        self.script(raw_body(b'{"id": "x", "object": "chat.completion"}'))
        result = self.make_runner().run("你好")
        self.assertEqual(result["source"], "error")
        self.assertIn("格式不正确", result["error"])

    def test_no_key_returns_error(self) -> None:
        config = {"llm": {"base_url": self.fake.base_url, "api_key": ""}, "agent": {}}
        runner = agent.AgentRunner(self.dir, config, log=self.logs.append)
        result = runner.run("你好")

        self.assertEqual(result["source"], "error")
        self.assertIn("API Key", result["error"])
        self.assertEqual(len(self.fake.requests), 0)
        self.assertNotIn(SECRET_KEY, json.dumps(result, ensure_ascii=False))

    def test_key_priority_config_env_file(self) -> None:
        # 1) config 里的 Key 优先于环境变量
        os.environ["DFY_API_KEY"] = "sk-env-应该被忽略"
        runner = self.make_runner(llm={"api_key": FAKE_KEY})
        self.assertEqual(runner.run("你好")["source"], "agent")
        self.assertEqual(self.fake.requests[-1]["auth"], "Bearer " + FAKE_KEY)

        # 2) config 空 → 环境变量
        env_key = "sk-env-key-abcdef123456"
        os.environ["DFY_API_KEY"] = env_key
        runner = self.make_runner(llm={"api_key": ""})
        self.assertEqual(runner.run("你好")["source"], "agent")
        self.assertEqual(self.fake.requests[-1]["auth"], "Bearer " + env_key)

        # 3) 环境变量也没有 → pet/.secret_api_key 文件
        os.environ.pop("DFY_API_KEY", None)
        file_key = "sk-file-key-abcdef123456"
        (self.dir / ".secret_api_key").write_text(file_key + "\n", encoding="utf-8")
        runner = self.make_runner(llm={"api_key": ""})
        self.assertEqual(runner.run("你好")["source"], "agent")
        self.assertEqual(self.fake.requests[-1]["auth"], "Bearer " + file_key)
        self.assertNotIn(file_key, json.dumps(self.logs, ensure_ascii=False))


# --------------------------------------------------------------------------- 脱敏
class RedactionTests(AgentTestCase):
    def test_tool_output_redacts_key(self) -> None:
        """工具读到的内容里就算有 Key，也不能原样进模型上下文/步骤。"""
        self.write("data/token.txt", f"token = {SECRET_KEY}")
        self.script(call("read_file", {"path": "data/token.txt"}), say())
        runner = self.make_runner(llm={"api_key": SECRET_KEY})

        result = runner.run("读一下 token 文件")

        self.assertEqual(result["source"], "agent")
        self.assertEqual(result["steps"][0]["tool"], "read_file")
        self.assertTrue(result["steps"][0]["ok"])
        tool_msgs = self.tool_messages(1)
        self.assertEqual(len(tool_msgs), 1)
        self.assertNotIn(SECRET_KEY, tool_msgs[0]["content"])
        self.assertIn("***", tool_msgs[0]["content"])
        self.assertNotIn(SECRET_KEY, json.dumps(result, ensure_ascii=False))
        # 只看请求体：Authorization 头本来就该有 Key，但内容里不许出现
        bodies = json.dumps([item["body"] for item in self.fake.requests], ensure_ascii=False)
        self.assertNotIn(SECRET_KEY, bodies)

    def test_key_not_in_system_prompt(self) -> None:
        self.script(say())
        runner = self.make_runner(llm={"api_key": SECRET_KEY})
        runner.run("你好")
        system_prompt = self.fake.requests[0]["body"]["messages"][0]["content"]
        self.assertNotIn(SECRET_KEY, system_prompt)
        self.assertNotIn(SECRET_KEY, json.dumps(runner.system_prompt("你好"), ensure_ascii=False))

    def test_key_not_echoed_in_reply(self) -> None:
        """模型万一复述了 Key，最终回复里也要抹掉。"""
        self.script(say(f"主人的 Key 是 {SECRET_KEY} 哦"))
        runner = self.make_runner(llm={"api_key": SECRET_KEY})
        result = runner.run("我的 Key 是多少")
        self.assertEqual(result["source"], "agent")
        self.assertNotIn(SECRET_KEY, result["reply"])
        self.assertIn("***", result["reply"])


# --------------------------------------------------------------------------- 工具定义
class ToolSpecTests(unittest.TestCase):
    def test_tool_specs_are_openai_compatible(self) -> None:
        names = []
        for spec in agent.TOOL_SPECS:
            self.assertEqual(spec["type"], "function")
            function = spec["function"]
            names.append(function["name"])
            self.assertTrue(function["description"], msg=function["name"])
            self.assertEqual(function["parameters"]["type"], "object")
            self.assertIn("properties", function["parameters"])
        self.assertEqual(names, list(agent.TOOL_NAMES))
        self.assertIn("run_powershell", names)
        self.assertIn("write_file", names)
        self.assertEqual(len(set(names)), len(names), msg="工具名不能重复")

    def test_mutating_tools_are_marked(self) -> None:
        self.assertEqual(set(agent.AgentRunner.MUTATING_TOOLS),
                         {"run_powershell", "write_file", "replace_in_file"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
