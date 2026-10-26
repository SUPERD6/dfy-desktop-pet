# -*- coding: utf-8 -*-
"""WorkOverview 单元测试：tempfile 假工作区 + 假 DSH 目录 + 本地假 LLM 接口。

全程不联网、不碰真实个人库、不写仓库里的 pet/overview.json。
覆盖：最近文件扫描与目录跳过、会话目录名解码与过滤、DSH 状态、TODO 线索、
HTML/JSON 写入与转义、刷新节流、is_stale、text_summary 长度、AI 建议成功/失败回退、
以及「个人库内容不外发、不泄漏 Key」。
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

PET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PET))

from personal import PersonalLibrary  # noqa: E402
from work_overview import WorkOverview, decode_session_dir  # noqa: E402

SESSIONS_WORKSPACE = "D:/Work/DeepSeek/大肥鱼"


# ----------------------------------------------------------------------
# 测试辅助
# ----------------------------------------------------------------------
class FakePersonal:
    """假的 PersonalLibrary：用来断言「个人库正文不会进 HTML/摘要/外发请求」。"""

    def __init__(self, available: bool = True):
        self.is_available = available
        self.path = Path("D:/fake/Shared")
        self.error = None if available else "假个人库不可用"
        self.recap = "PERSONAL_RECAP_MARKER：我记得你。"
        self.block = "PERSONAL_BLOCK_MARKER\n" + ("个人库背景正文，不应外发。" * 80)

    def quick_recap(self) -> str:
        return self.recap

    def prompt_block(self, query: str = "") -> str:
        return self.block

    def summary(self) -> str:
        return "个人库：已加载（测试假数据）"


class FakeLLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        self.server.requests.append({  # type: ignore[attr-defined]
            "path": self.path,
            "body": body,
            "authorization": self.headers.get("Authorization") or "",
        })
        if self.server.mode == "error":  # type: ignore[attr-defined]
            payload = b'{"error": "boom"}'
            self.send_response(500)
        else:
            content = "- 先处理待办里的第一条\n- 继续收尾最近改动的项目\n- 给 DSH 的错误写一条复盘"
            payload = json.dumps(
                {"choices": [{"message": {"content": content}}]}, ensure_ascii=False
            ).encode("utf-8")
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # noqa: D401
        pass


class FakeLLMServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLMHandler)
        self.httpd.requests = []  # type: ignore[attr-defined]
        self.httpd.mode = "ok"  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    @property
    def requests(self):
        return self.httpd.requests  # type: ignore[attr-defined]

    @property
    def mode(self) -> str:
        return self.httpd.mode  # type: ignore[attr-defined]

    @mode.setter
    def mode(self, value: str):
        self.httpd.mode = value  # type: ignore[attr-defined]

    def stop(self):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()


def write_file(path: Path, text: str = "x", mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class OverviewTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_root = Path(self.tmp.name)
        self.project_root = self.tmp_root / "project"
        self.pet_dir = self.project_root / "pet"
        self.pet_dir.mkdir(parents=True)
        self.ws = self.tmp_root / "workspace"
        self.sessions_dir = self.tmp_root / "dsh-sessions"
        self.sessions_dir.mkdir()
        self.status_path = self.tmp_root / "status.json"
        self.logs: list[str] = []
        self.now = time.time()

        # 避免环境里真实的 Key 影响「无 Key 用模板」的用例
        env = mock.patch.dict(os.environ, {"DFY_API_KEY": ""})
        env.start()
        self.addCleanup(env.stop)

    # 假工作区：WorkA/ProjA（近期改动 + 应跳过的目录）、WorkB/ProjOld（旧文件）
    def build_workspace(self) -> None:
        recent = self.now - 3600
        old = self.now - 10 * 86400
        write_file(self.ws / "WorkA" / "ProjA" / "recent1.md", "a", recent)
        write_file(self.ws / "WorkA" / "ProjA" / "recent2.py", "b", recent - 100)
        write_file(self.ws / "WorkA" / "ProjA" / "old.md", "c", old)
        write_file(self.ws / "WorkA" / "ProjA" / "node_modules" / "dep.js", "d", recent)
        write_file(self.ws / "WorkA" / "ProjA" / ".git" / "config", "e", recent)
        write_file(self.ws / "WorkA" / "ProjA" / "cache" / "x.tmp", "f", recent)
        write_file(self.ws / "WorkB" / "ProjOld" / "only_old.md", "g", old)

    def build_sessions(self) -> None:
        good = self.sessions_dir / "--D-Work-DeepSeek-~5927~80A5~9C7C--"
        session_child = good / "session-1111"
        write_file(session_child / "events.jsonl", "SECRET_SESSION_TEXT 不应被读到")
        recent = self.now - 120
        os.utime(good, (recent, recent))
        os.utime(session_child, (recent, recent))
        old_dir = self.sessions_dir / "--D-Work-DeepSeek--"
        old_dir.mkdir(parents=True)
        old = self.now - 5 * 86400
        os.utime(old_dir, (old, old))

    def write_status(self, state: str = "tool", **extra) -> None:
        payload = {
            "ok": True,
            "state": state,
            "lastTool": "grep",
            "doneCount": 5,
            "errorCount": 2,
            "approvalOpen": False,
            "updatedAt": int(self.now * 1000),
            "version": 1,
        }
        payload.update(extra)
        self.status_path.write_text(json.dumps(payload), encoding="utf-8")

    def make_config(self, **overview_extra) -> dict:
        overview = {
            "roots": [str(self.ws)],
            "sessions_dir": str(self.sessions_dir),
            "status_path": str(self.status_path),
            "min_interval_minutes": 10,
        }
        overview.update(overview_extra)
        return {"overview": overview, "llm": {"enabled": False, "api_key": ""}}

    def make_overview(self, personal=None, config: dict | None = None) -> WorkOverview:
        return WorkOverview(
            self.pet_dir,
            config if config is not None else self.make_config(),
            personal=personal,
            log=self.logs.append,
        )


# ----------------------------------------------------------------------
# 会话目录名解码
# ----------------------------------------------------------------------
class DecodeSessionDirTests(unittest.TestCase):
    def test_decode_unicode_and_drive(self):
        self.assertEqual(
            decode_session_dir("--D-Work-DeepSeek-~5927~80A5~9C7C--"),
            SESSIONS_WORKSPACE,
        )

    def test_decode_plain_ascii_path(self):
        self.assertEqual(
            decode_session_dir("--C-Users-alice-AppData-Local-Temp-tmp94al5msm--"),
            "C:/Users/alice/AppData/Local/Temp/tmp94al5msm",
        )

    def test_decode_degrades_on_weird_input(self):
        self.assertEqual(decode_session_dir(""), "")
        self.assertEqual(decode_session_dir("--D-Work-DeepSeek--"), "D:/Work/DeepSeek")
        # 奇形怪状也不能抛异常
        self.assertIsInstance(decode_session_dir("~~not-a-session~~"), str)


# ----------------------------------------------------------------------
# 工作总览主体
# ----------------------------------------------------------------------
class WorkOverviewTests(OverviewTestBase):
    def test_refresh_collects_recent_projects_and_skips_noise(self):
        self.build_workspace()
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        data = overview.data
        self.assertIsNotNone(data)
        self.assertEqual(data["version"], 1)
        projects = {item["path"]: item for item in data["recent_projects"]}
        self.assertIn("WorkA/ProjA", projects)
        self.assertEqual(projects["WorkA/ProjA"]["recent_files"], 2)  # node_modules/.git/cache 不计
        self.assertIn("WorkB/ProjOld", projects)
        self.assertEqual(projects["WorkB/ProjOld"]["recent_files"], 0)
        self.assertTrue(data["recent_projects"][0]["recent_files"] >= data["recent_projects"][-1]["recent_files"])

    def test_project_limit_respected(self):
        recent = self.now - 600
        for index in range(6):
            write_file(self.ws / "Work" / f"Proj{index}" / "a.txt", "x", recent)
        overview = self.make_overview(config=self.make_config(project_limit=3))
        self.assertTrue(overview.refresh(force=True))
        self.assertLessEqual(len(overview.data["recent_projects"]), 3)

    def test_own_project_scanned_even_with_many_workspaces(self):
        # 当前项目（pet 的上级目录）作为二级目录时，即使其它项目文件更多也应进结果
        own = self.ws / "WorkA" / "大肥鱼"
        (own / "pet").mkdir(parents=True)
        write_file(own / "recent.md", "x", self.now - 60)
        write_file(self.ws / "WorkA" / "ProjA" / "recent.md", "x", self.now - 60)
        overview = WorkOverview(own / "pet", self.make_config(), personal=None, log=self.logs.append)
        self.assertTrue(overview.refresh(force=True))
        paths = [item["path"] for item in overview.data["recent_projects"]]
        self.assertIn("WorkA/大肥鱼", paths)

    def test_session_activity_decoded_and_filtered(self):
        self.build_workspace()
        self.build_sessions()
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        sessions = overview.data["sessions"]
        self.assertEqual(len(sessions), 1)  # 5 天前的旧目录被过滤
        self.assertEqual(sessions[0]["path"], SESSIONS_WORKSPACE)
        self.assertEqual(sessions[0]["label"], "大肥鱼")
        # 会话正文绝不能出现在任何输出里
        self.assertNotIn("SECRET_SESSION_TEXT", json.dumps(overview.data, ensure_ascii=False))
        self.assertNotIn("SECRET_SESSION_TEXT", overview.html_path.read_text(encoding="utf-8"))

    def test_temp_workspace_sessions_filtered_by_default(self):
        # DSH 自测会留下 AppData/Local/Temp 下的 scratch 会话，默认不应挤进工作总览
        self.build_workspace()
        self.build_sessions()  # 大肥鱼 工作区会话
        temp_session = self.sessions_dir / "--C-Users-alice-AppData-Local-Temp-faketmp--"
        write_file(temp_session / "events.jsonl", "x")
        os.utime(temp_session, (self.now - 10, self.now - 10))  # 临时会话更新
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        self.assertEqual(
            [item["path"] for item in overview.data["sessions"]], [SESSIONS_WORKSPACE]
        )
        summary = overview.text_summary()
        self.assertIn("大肥鱼", summary)
        self.assertNotIn("faketmp", summary)

    def test_temp_sessions_included_when_configured(self):
        self.build_workspace()
        self.build_sessions()
        temp_session = self.sessions_dir / "--C-Users-alice-AppData-Local-Temp-faketmp--"
        write_file(temp_session / "events.jsonl", "x")
        os.utime(temp_session, (self.now - 10, self.now - 10))
        overview = self.make_overview(config=self.make_config(include_temp_sessions=True))
        self.assertTrue(overview.refresh(force=True))
        paths = [item["path"] for item in overview.data["sessions"]]
        self.assertEqual(len(paths), 2)
        self.assertTrue(any("faketmp" in path for path in paths))

    def test_dsh_status_parsed(self):
        self.write_status("running", lastTool="pwsh", doneCount=7, errorCount=1)
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        dsh = overview.data["dsh"]
        self.assertTrue(dsh["available"])
        self.assertTrue(dsh["connected"])
        self.assertEqual(dsh["state"], "running")
        self.assertEqual(dsh["lastTool"], "pwsh")
        self.assertEqual(dsh["doneCount"], 7)
        self.assertEqual(dsh["errorCount"], 1)
        self.assertIn("running", json.dumps(overview.data, ensure_ascii=False))

    def test_dsh_status_stale_heartbeat(self):
        self.write_status("tool", updatedAt=int((self.now - 3600) * 1000))
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        self.assertTrue(overview.data["dsh"]["available"])
        self.assertFalse(overview.data["dsh"]["connected"])
        self.assertIn("心跳", overview.data["dsh"]["reason"])

    def test_missing_sessions_status_and_roots_degrade(self):
        config = self.make_config(
            roots=[str(self.tmp_root / "does-not-exist")],
            sessions_dir=str(self.tmp_root / "no-sessions"),
            status_path=str(self.tmp_root / "no-status.json"),
        )
        overview = self.make_overview(config=config)
        self.assertTrue(overview.refresh(force=True))
        data = overview.data
        self.assertEqual(data["recent_projects"], [])
        self.assertEqual(data["sessions"], [])
        self.assertFalse(data["dsh"]["available"])
        self.assertTrue(overview.html_path.exists())
        summary = overview.text_summary()
        self.assertIn("DSH：未连接", summary)

    # ---------- 待办线索 ----------
    def test_todo_files_scanned_limited_and_truncated(self):
        self.build_workspace()
        long_todo = "\n".join(f"- 待办 {index}" for index in range(40))
        write_file(self.ws / "TODO.md", long_todo)
        write_file(self.ws / "WorkA" / "TODO.md", "- 工作区待办")
        write_file(self.ws / "WorkA" / "ProjA" / "待办.md", "- 项目待办")
        write_file(self.ws / "WorkB" / "TODO.md", "- 第四个不应被扫描")
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        todos = overview.data["todos"]
        self.assertEqual(todos["count"], 3)
        self.assertEqual(len(todos["files"]), 3)
        first = todos["files"][0]
        self.assertEqual(len(first["lines"]), 30)
        self.assertTrue(first["truncated"])

    def test_todo_missing_gives_hint(self):
        self.build_workspace()
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        self.assertEqual(overview.data["todos"]["count"], 0)
        self.assertIn("未发现显式 TODO 文件", overview.data["todos"]["hint"])

    # ---------- 输出文件 ----------
    def test_html_and_json_written(self):
        self.build_workspace()
        self.build_sessions()
        self.write_status()
        overview = self.make_overview(personal=FakePersonal())
        self.assertTrue(overview.refresh(force=True))
        self.assertTrue(overview.json_path.exists())
        self.assertTrue(overview.html_path.exists())
        self.assertEqual(overview.json_path.name, "overview.json")
        self.assertEqual(overview.html_path.name, "工作总览.html")

        payload = json.loads(overview.json_path.read_text(encoding="utf-8"))
        self.assertIn("generated_at", payload)
        self.assertIn("recent_projects", payload)
        self.assertTrue(payload["generated_at"])

        html = overview.html_path.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("charset=\"utf-8\"", html)
        self.assertIn("工作总览", html)
        self.assertIn("数据来源", html)
        self.assertIn(payload["generated_at"], html)
        self.assertIn("#ffffff", html)  # 白底
        self.assertNotIn("<script", html)  # 自包含、无脚本
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        # 摘要段必须用「这次」的数据，而不是上一次 refresh 的旧数据
        summary_section = html.split("一句话摘要", 1)[1].split("最近做过", 1)[0]
        self.assertIn("最近在动", summary_section)
        self.assertIn("WorkA/ProjA", summary_section)

    def test_html_escapes_todo_content(self):
        self.build_workspace()
        write_file(self.ws / "WorkA" / "TODO.md", "- <script>alert(1)</script>\n- 正常待办")
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        html = overview.html_path.read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    # ---------- 节流 / 过期 ----------
    def test_refresh_throttle_and_force(self):
        self.build_workspace()
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        first_bytes = overview.json_path.read_bytes()
        first_generated = overview.data["generated_at"]
        time.sleep(0.01)
        self.assertFalse(overview.refresh())  # 没过节流
        self.assertEqual(overview.json_path.read_bytes(), first_bytes)
        self.assertEqual(overview.data["generated_at"], first_generated)

        # 新实例从 overview.json 恢复，仍受节流
        again = self.make_overview()
        self.assertIsNotNone(again.data)
        self.assertFalse(again.is_stale())
        self.assertFalse(again.refresh())
        self.assertTrue(again.refresh(force=True))

    def test_is_stale_after_old_generated_at(self):
        self.build_workspace()
        overview = self.make_overview()
        self.assertTrue(overview.is_stale())  # 还没生成
        self.assertTrue(overview.refresh(force=True))
        self.assertFalse(overview.is_stale())
        payload = json.loads(overview.json_path.read_text(encoding="utf-8"))
        payload["generated_at"] = "2020-01-01 00:00:00"
        payload["generated_ts"] = time.time() - 3600
        overview.json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        fresh = self.make_overview()
        self.assertTrue(fresh.is_stale())
        self.assertTrue(fresh.refresh())

    # ---------- text_summary ----------
    def test_text_summary_length_and_content(self):
        self.build_workspace()
        self.build_sessions()
        self.write_status()
        write_file(self.ws / "WorkA" / "TODO.md", "- 把总览接进桌宠")
        overview = self.make_overview()
        self.assertTrue(overview.refresh(force=True))
        summary = overview.text_summary()
        self.assertLessEqual(len(summary), 500)
        for keyword in ("最近在动", "待办线索", "DSH", "下一步建议", "生成于"):
            self.assertIn(keyword, summary)
        self.assertIn("ProjA", summary)
        self.assertLessEqual(len(overview.text_summary(max_chars=120)), 120)

    def test_text_summary_before_refresh(self):
        overview = self.make_overview()
        summary = overview.text_summary()
        self.assertIn("还没有工作总览数据", summary)
        self.assertLessEqual(len(summary), 500)

    def test_text_summary_does_not_include_personal_block(self):
        self.build_workspace()
        personal = FakePersonal()
        overview = self.make_overview(personal=personal)
        self.assertTrue(overview.refresh(force=True))
        summary = overview.text_summary()
        self.assertNotIn("PERSONAL_BLOCK_MARKER", summary)
        self.assertNotIn("个人库背景正文", summary)

    # ---------- 个人库摘要 ----------
    def test_personal_summary_in_data_and_html(self):
        self.build_workspace()
        personal = FakePersonal()
        overview = self.make_overview(personal=personal)
        self.assertTrue(overview.refresh(force=True))
        block = overview.data["personal"]
        self.assertTrue(block["available"])
        self.assertIn("PERSONAL_RECAP_MARKER", block["recap"])
        html = overview.html_path.read_text(encoding="utf-8")
        self.assertIn("PERSONAL_RECAP_MARKER", html)
        # 只摘录短摘要，不嵌整段 block
        self.assertNotIn("个人库背景正文，不应外发。个人库背景正文", html)

    def test_real_personal_library_integration(self):
        self.build_workspace()
        shared = self.tmp_root / "Shared"
        (shared / "context").mkdir(parents=True)
        (shared / "context" / "profile.md").write_text(
            "- Test User。Sample University Electrical Engineering 硕士在读。\n"
            "- 目标是美国有全额资助的 PhD。更偏好构建 LLM Agent。\n",
            encoding="utf-8",
        )
        (shared / "memory").mkdir(parents=True)
        (shared / "memory" / "journal.jsonl").write_text(
            json.dumps({"seq": 1, "ts": "2026-10-25T09:00:00", "by": "user",
                        "topic": "pitfall", "text": "PITFALL_MARKER 技术坑"},
                       ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        library = PersonalLibrary(path=shared, log=self.logs.append)
        overview = self.make_overview(personal=library)
        self.assertTrue(overview.refresh(force=True))
        self.assertTrue(overview.data["personal"]["available"])
        self.assertIn("Test User", overview.data["personal"]["recap"])
        html = overview.html_path.read_text(encoding="utf-8")
        json_text = overview.json_path.read_text(encoding="utf-8")
        self.assertNotIn("PITFALL_MARKER", html)
        self.assertNotIn("PITFALL_MARKER", json_text)

    def test_missing_personal_gives_note(self):
        self.build_workspace()
        overview = self.make_overview(personal=None)
        self.assertTrue(overview.refresh(force=True))
        self.assertFalse(overview.data["personal"]["available"])
        self.assertIn("未接入", overview.data["personal"]["error"])

    # ---------- AI 建议 ----------
    def test_template_suggestions_without_key(self):
        self.build_workspace()
        write_file(self.ws / "WorkA" / "TODO.md", "- 接好总览")
        overview = self.make_overview(personal=FakePersonal())
        self.assertTrue(overview.refresh(force=True))
        suggestions = overview.data["suggestions"]
        self.assertEqual(suggestions["source"], "template")
        self.assertGreaterEqual(len(suggestions["items"]), 3)
        self.assertIn("接好总览", " ".join(suggestions["items"]))

    def test_ai_suggestions_via_local_fake_server(self):
        self.build_workspace()
        server = FakeLLMServer()
        self.addCleanup(server.stop)
        config = self.make_config()
        config["llm"] = {
            "enabled": True,
            "api_key": "fake-key-ABC123",
            "base_url": server.base_url,
            "model": "deepseek-chat",
        }
        overview = self.make_overview(personal=FakePersonal(), config=config)
        self.assertTrue(overview.refresh(force=True))
        suggestions = overview.data["suggestions"]
        self.assertEqual(suggestions["source"], "ai")
        self.assertEqual(len(suggestions["items"]), 3)

        self.assertEqual(len(server.requests), 1)
        request = server.requests[0]
        self.assertTrue(request["path"].endswith("/chat/completions"))
        self.assertIn("Bearer fake-key-ABC123", request["authorization"])
        self.assertIn("deepseek-chat", request["body"])
        # 隐私：个人库正文绝不外发
        self.assertNotIn("PERSONAL_BLOCK_MARKER", request["body"])
        self.assertNotIn("个人库背景正文", request["body"])
        # Key 不进日志/HTML/JSON
        html = overview.html_path.read_text(encoding="utf-8")
        json_text = overview.json_path.read_text(encoding="utf-8")
        self.assertNotIn("fake-key-ABC123", html)
        self.assertNotIn("fake-key-ABC123", json_text)
        self.assertNotIn("fake-key-ABC123", "\n".join(self.logs))

    def test_ai_suggestions_failure_falls_back_to_template(self):
        self.build_workspace()
        server = FakeLLMServer()
        self.addCleanup(server.stop)
        server.mode = "error"
        config = self.make_config()
        config["llm"] = {
            "enabled": True,
            "api_key": "fake-key-ABC123",
            "base_url": server.base_url,
            "model": "deepseek-chat",
        }
        overview = self.make_overview(config=config)
        self.assertTrue(overview.refresh(force=True))
        suggestions = overview.data["suggestions"]
        self.assertEqual(suggestions["source"], "template")
        self.assertGreaterEqual(len(suggestions["items"]), 3)
        self.assertNotIn("fake-key-ABC123", "\n".join(self.logs))
        self.assertNotIn("fake-key-ABC123", overview.html_path.read_text(encoding="utf-8"))

    def test_ai_disabled_in_config_does_not_call(self):
        self.build_workspace()
        server = FakeLLMServer()
        self.addCleanup(server.stop)
        config = self.make_config()
        config["llm"] = {
            "enabled": False,
            "api_key": "fake-key-ABC123",
            "base_url": server.base_url,
            "model": "deepseek-chat",
        }
        overview = self.make_overview(config=config)
        self.assertTrue(overview.refresh(force=True))
        self.assertEqual(overview.data["suggestions"]["source"], "template")
        self.assertEqual(server.requests, [])


if __name__ == "__main__":
    unittest.main()
